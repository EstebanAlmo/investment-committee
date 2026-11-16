"""SQLite prospective registry and Excel export: migrations, integrity,
append-only, prospective vs retrospective, end-to-end with the scanner,
tracker and alerts, and deterministic regeneration. Temporary files only."""

from __future__ import annotations

import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree

import pytest

from investment_committee.application import OpportunityTrackerService, PortfolioGuardianService
from investment_committee.market_intelligence import MarketIntelligenceService
from investment_committee.memory.database import DatabaseError, connect, migrate, record_backtest_run, register_strategy, tables, utc
from investment_committee.memory.excel_export import export_excel, sheets_from
from investment_committee.memory.xlsx import write_xlsx
from investment_committee.portfolio.guardian import Side, forex_position
from investment_committee.portfolio.sqlite_store import SqliteAlertStore
from investment_committee.portfolio.store import InMemoryPositionStore
from investment_committee.simulation.sqlite_store import SqliteOpportunityScanLedger, SqliteTrackerLedger
from investment_committee.simulation.strategies import TrendHTFStrategy

from tests.application.test_opportunities import DEFAULT_DATA, NOW, FakeMarket, scanner
from tests.simulation._support import bars_from_moves

UTC = timezone.utc


def _scan(db: Path, **kwargs):  # type: ignore[no-untyped-def]
    service, _ = scanner(ledger=SqliteOpportunityScanLedger(db), tracker=SqliteTrackerLedger(db), **kwargs)
    return service.scan()


# --- schema ---


def test_migrations_are_versioned_and_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    with connect(db) as conn:
        assert set(tables(conn)) >= {"schema_migrations", "strategies", "scans", "observations", "recommendations", "candidate_events",
                                     "alert_states", "alert_events", "audit_events", "backtest_runs"}
        assert [r["version"] for r in conn.execute("SELECT version FROM schema_migrations")] == [1, 2]
        assert migrate(conn) == []  # nothing left to apply
        assert conn.execute("SELECT count(*) FROM audit_events WHERE action = 'MIGRACION'").fetchone()[0] == 2


def test_integrity_constraints(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    with connect(db) as conn:
        register_strategy(conn, "x", "1", {"a": 1}, "0" * 64, "EXPERIMENTAL", NOW)
        with pytest.raises(sqlite3.IntegrityError):  # timestamps must be UTC
            conn.execute("INSERT INTO audit_events (at, action, entity, entity_id, detail_json) VALUES ('2026-10-13T10:00:00-05:00', 'a', 'b', NULL, '{}')")
        with pytest.raises(sqlite3.IntegrityError):  # a scan is always prospective
            conn.execute("INSERT INTO scans VALUES ('s', 'RETROSPECTIVO', 'x@1', ?, ?, 'v', 'src', 0, 0, 0, '{}')", (utc(NOW), utc(NOW)))
        with pytest.raises(sqlite3.IntegrityError):  # and never opens a simulated trade
            conn.execute("INSERT INTO scans VALUES ('s', 'PROSPECTIVO', 'x@1', ?, ?, 'v', 'src', 0, 0, 1, '{}')", (utc(NOW), utc(NOW)))
        with pytest.raises(sqlite3.IntegrityError):  # foreign key
            conn.execute("INSERT INTO scans VALUES ('s', 'PROSPECTIVO', 'nope@1', ?, ?, 'v', 'src', 0, 0, 0, '{}')", (utc(NOW), utc(NOW)))
        with pytest.raises(sqlite3.IntegrityError):  # no status can be VALIDADA
            conn.execute("INSERT INTO strategies VALUES ('y@1', 'y', '1', 'VALIDADA', '{}', ?, ?)", ("0" * 64, utc(NOW)))
    with pytest.raises(DatabaseError):
        utc(datetime(2026, 1, 1))  # naive


def test_append_only_tables_refuse_update_and_delete(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    _scan(db)
    with connect(db) as conn:
        for table in ("scans", "recommendations", "audit_events", "strategies"):
            with pytest.raises(sqlite3.DatabaseError, match="solo anexado"):
                conn.execute(f"UPDATE {table} SET rowid = rowid")
            with pytest.raises(sqlite3.DatabaseError, match="solo anexado"):
                conn.execute(f"DELETE FROM {table}")


def test_a_strategy_version_cannot_change_its_parameters(tmp_path: Path) -> None:
    with connect(tmp_path / "ic.db") as conn:
        register_strategy(conn, "s", "1.0", {"k": 1}, "a" * 64, "EXPERIMENTAL", NOW)
        assert register_strategy(conn, "s", "1.0", {"k": 1}, "a" * 64, "EXPERIMENTAL", NOW) == "s@1.0"  # same: fine
        with pytest.raises(DatabaseError, match="versión nueva"):
            register_strategy(conn, "s", "1.0", {"k": 2}, "b" * 64, "EXPERIMENTAL", NOW)


# --- scanner, tracker, alerts in SQLite ---


def test_scan_observations_and_recommendations_are_recorded(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    scan = _scan(db)

    with connect(db) as conn:
        row = conn.execute("SELECT * FROM scans").fetchone()
        assert row["record_kind"] == "PROSPECTIVO" and row["strategy_id"] == "tendencia-momentum-volatilidad@1.0.0"
        assert row["started_at"] == NOW.isoformat() and row["simulated_trade_opened"] == 0
        observations = conn.execute("SELECT * FROM observations ORDER BY symbol").fetchall()
        assert [o["symbol"] for o in observations] == ["EURUSD=X", "GBPUSD=X", "USDJPY=X"]
        assert observations[0]["last_close"] and observations[0]["last_closed_bar_close"] == NOW.isoformat()
        recs = conn.execute("SELECT * FROM recommendations ORDER BY rank").fetchall()
        assert [r["symbol"] for r in recs] == [s.symbol for s in scan.ranked]
        assert all(r["record_kind"] == "PROSPECTIVO" and r["signal_data_as_of"] <= r["detected_at"] for r in recs)
        strategy = conn.execute("SELECT * FROM strategies").fetchone()
        assert strategy["status"] == "BASE_EXPERIMENTAL" and len(strategy["parameters_sha256"]) == 64
    assert SqliteOpportunityScanLedger(db).records()[0]["scan_id"] == scan.scan_id


def test_a_v2_strategy_scan_is_registered_under_its_own_identity(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    _scan(db, strategy=TrendHTFStrategy())

    with connect(db) as conn:
        ids = {r["strategy_id"]: r["status"] for r in conn.execute("SELECT strategy_id, status FROM strategies")}
    assert ids == {"tendencia-confirmada-4h@0.1.0": "EXPERIMENTAL"}


def test_tracker_statuses_are_events_not_updates(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    scan = _scan(db)
    ledger = SqliteTrackerLedger(db)
    times = iter([NOW + timedelta(minutes=30), NOW + timedelta(minutes=40), NOW + timedelta(hours=6)])
    later = {s: [*bars, *bars_from_moves(["0.0001"] * 6, start=bars[-1].timestamp + timedelta(hours=1), first_close=str(bars[-1].close))]
             for s, bars in DEFAULT_DATA.items()}
    tracker = OpportunityTrackerService(MarketIntelligenceService(FakeMarket(later)), ledger, clock=lambda: next(times))

    first, second = tracker.update(), tracker.update()
    third = tracker.update()

    assert first.updated == len(scan.ranked) and second.updated == 0 and third.updated >= 1
    with connect(db) as conn:
        events = conn.execute("SELECT candidate_id, status, hypothetical FROM candidate_events ORDER BY event_id").fetchall()
        assert all(e["hypothetical"] == 1 for e in events)
        assert len({(e["candidate_id"], e["status"]) for e in events}) == len(events)  # never a duplicate
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO candidate_events (candidate_id, status, evaluated_at, hypothetical, evaluation_json) VALUES (?, ?, ?, 1, '{}')",
                         (events[0]["candidate_id"], events[0]["status"], utc(NOW)))


def test_alert_states_and_events_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    positions = InMemoryPositionStore()
    positions.add(forex_position("p1", "EUR/USD", Side.LONG, Decimal("10000"), Decimal("1.20000"), NOW), NOW)
    market = FakeMarket({"EURUSD=X": DEFAULT_DATA["EURUSD=X"]})
    guardian = PortfolioGuardianService(MarketIntelligenceService(market), positions, SqliteAlertStore(db), clock=lambda: NOW)

    first, second = guardian.check(), guardian.check()

    assert first.events and second.events == []
    with connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM alert_events").fetchone()[0] == len(first.events)
        assert conn.execute("SELECT count(*) FROM alert_states WHERE active = 1").fetchone()[0] >= 1
        assert conn.execute("SELECT count(*) FROM audit_events WHERE entity = 'alert'").fetchone()[0] == len(first.events)


def test_backtests_are_retrospective_and_apart_from_prospective_records(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    with connect(db) as conn:
        register_strategy(conn, "s", "1", {}, "c" * 64, "EXPERIMENTAL", NOW)
        row = {"run_id": "r1", "strategy_id": "s@1", "period_start": utc(NOW), "period_end": utc(NOW), "mode": "CONTINUO", "policy": "A",
               "cost_scenario": "base", "month": None, "data_sha256": "d" * 64, "net_pnl": "-10", "net_return": "-0.001", "trades": 3, "summary_json": "{}"}
        record_backtest_run(conn, row, NOW)
        record_backtest_run(conn, row, NOW)  # same run id: not duplicated
        assert conn.execute("SELECT record_kind, count(*) FROM backtest_runs").fetchone()[:] == ("RETROSPECTIVO", 1)
        assert conn.execute("SELECT count(*) FROM scans").fetchone()[0] == 0


# --- Excel ---


def _read_xlsx(path: Path) -> dict[str, list[list[str]]]:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.endswith(".xml") or name.endswith(".rels"):
                ElementTree.fromstring(z.read(name))  # well-formed
        workbook = ElementTree.fromstring(z.read("xl/workbook.xml"))
        names = [s.get("name") for s in workbook.find("m:sheets", ns)]
        sheets = {}
        for i, name in enumerate(names, start=1):
            root = ElementTree.fromstring(z.read(f"xl/worksheets/sheet{i}.xml"))
            rows = []
            for row in root.iter(f"{{{ns['m']}}}row"):
                rows.append(["".join(c.itertext()) for c in row])
            sheets[name] = rows
    return sheets


def test_excel_is_rebuilt_from_sqlite_without_duplicates(tmp_path: Path) -> None:
    db, xlsx = tmp_path / "ic.db", tmp_path / "out" / "ic.xlsx"
    scan = _scan(db)

    export_excel(db, xlsx)
    first_bytes = xlsx.read_bytes()
    export_excel(db, xlsx)  # regenerate: identical, nothing duplicated

    assert xlsx.read_bytes() == first_bytes
    sheets = _read_xlsx(xlsx)
    assert list(sheets) == ["Senales prospectivas", "Recomendaciones", "Resultados", "Evolucion diaria", "Estrategias", "Backtests retrospectivos",
                            "Alertas", "Errores y huecos", "Ciclos de monitoreo", "Notificaciones"]
    assert len(sheets["Recomendaciones"]) == 1 + len(scan.ranked)
    assert len(sheets["Resultados"]) == 1 + len(scan.ranked)
    assert sheets["Estrategias"][1][4] == "NO VALIDADA"
    assert all("no es una operación" in row[-1] for row in sheets["Recomendaciones"][1:])


def test_xlsx_writer_escapes_text_and_validates_names(tmp_path: Path) -> None:
    path = tmp_path / "x.xlsx"
    write_xlsx(path, [("Hoja", ["a", "b"], [["<&>\"", Decimal("1.5")], [None, 3]])])

    assert _read_xlsx(path)["Hoja"] == [["a", "b"], ['<&>"', "1.5"], ["", "3"]]  # the empty cell stays a cell
    with pytest.raises(ValueError):
        write_xlsx(path, [("mal/nombre", ["a"], [])])


def test_sheets_from_an_empty_database(tmp_path: Path) -> None:
    with connect(tmp_path / "ic.db") as conn:
        sheets = sheets_from(conn)
    assert all(rows == [] for _, _, rows in sheets)
