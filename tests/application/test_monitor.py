"""Tests for application.ProspectiveMonitor - the pre-registered prospective
monitor, one hourly cycle at a time.

Everything is fake or temporary: a fake market provider (no network), fixed
clocks, and a SQLite file under tmp_path. Every test runs with the working
directory moved to tmp_path, so nothing here can reach the real registry
(data/investment_committee.db is a path relative to the working directory).
These tests describe the monitor as it is; they change none of its rules.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator, Mapping, Sequence

import pytest

from investment_committee.application.factory import build_default_monitor
from investment_committee.application.monitor import (
    FROZEN_FINGERPRINTS,
    PROTOCOL_END,
    PROTOCOL_START,
    CachingMarket,
    MonitorSettings,
    ProspectiveMonitor,
    ProtocolViolationError,
    verify_frozen,
)
from investment_committee.domain import PriceBar
from investment_committee.market_intelligence import MarketDataError, MarketIntelligenceService
from investment_committee.memory.database import connect
from investment_committee.memory.excel_export import sheets_from
from investment_committee.simulation.opportunity_ledger import OpportunityLedgerError
from investment_committee.simulation.sqlite_store import MonitorScanLedger
from investment_committee.simulation.strategies import TrendHTFStrategy, all_strategies

from tests.application.test_opportunities import DEFAULT_DATA, NOW, FakeMarket
from tests.simulation._support import bars_from_moves

UTC = timezone.utc
HOUR = timedelta(hours=1)
STRATEGY_IDS = tuple(f"{s.name}@{s.version}" for s in all_strategies())
SYMBOLS = ("EURUSD=X", "GBPUSD=X", "USDJPY=X")
# NOW is Wednesday 2026-10-14 22:00 UTC, the close of the fixture's last bar:
# inside the FX week, and the candle the first cycle evaluates.
SETTINGS = MonitorSettings(window_start=NOW, window_end=NOW + timedelta(days=2))


@pytest.fixture(autouse=True)
def isolated_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Relative default paths (data/...) resolve inside tmp_path, and the
    monitor must not create any of them."""
    monkeypatch.chdir(tmp_path)
    yield
    assert not (tmp_path / "data").exists()


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def extended(hours: int, data: Mapping[str, Sequence[PriceBar]] = DEFAULT_DATA) -> dict[str, list[PriceBar]]:
    """The fixture's series with `hours` more quiet bars after NOW."""
    return {
        symbol: [*bars, *bars_from_moves(["0.0001"] * hours, start=bars[-1].timestamp + HOUR, first_close=str(bars[-1].close))]
        for symbol, bars in data.items()
    }


def monitor(db: Path, clock: Clock, data: Mapping[str, object] | None = None, **kwargs):  # type: ignore[no-untyped-def]
    market = FakeMarket(dict(DEFAULT_DATA) if data is None else data)  # type: ignore[arg-type]
    built = build_default_monitor(db_path=db, settings=kwargs.pop("settings", SETTINGS), market_intelligence_service=MarketIntelligenceService(market), **kwargs)
    built._clock = clock  # the factory takes no clock; the monitor reads it only through this attribute
    return built, market


def count(db: Path, sql: str, *args: object) -> int:
    with connect(db) as conn:
        return conn.execute(sql, args).fetchone()[0]


def rows(db: Path, sql: str, *args: object) -> list[dict[str, object]]:
    with connect(db) as conn:
        return [dict(r) for r in conn.execute(sql, args)]


# --- frozen protocol ---


def test_the_protocol_window_and_fingerprints_are_the_registered_ones() -> None:
    assert PROTOCOL_START == datetime(2026, 10, 19, 0, tzinfo=UTC) and PROTOCOL_END == datetime(2027, 1, 15, 21, tzinfo=UTC)
    assert set(FROZEN_FINGERPRINTS) == set(STRATEGY_IDS)
    assert MonitorSettings().window_start == PROTOCOL_START and MonitorSettings().window_end == PROTOCOL_END
    verify_frozen(all_strategies())  # the code's parameters are the frozen ones


def test_changed_parameters_or_an_unregistered_strategy_stop_the_monitor(tmp_path: Path) -> None:
    changed = TrendHTFStrategy()
    changed.parameters_object = replace(changed.parameters_object, sma_fast=21)
    with pytest.raises(ProtocolViolationError, match="tendencia-confirmada-4h@0.1.0"):
        verify_frozen([changed])

    unregistered = TrendHTFStrategy()
    unregistered.version = "0.2.0"
    with pytest.raises(ProtocolViolationError, match="0.2.0"):
        verify_frozen([unregistered])

    with pytest.raises(ProtocolViolationError):  # checked when the monitor is built, before any cycle
        ProspectiveMonitor(MarketIntelligenceService(FakeMarket({})), db_path=tmp_path / "ic.db", strategies=[changed],
                           scan_ledger_factory=MonitorScanLedger, tracker_ledger=object())  # type: ignore[arg-type]


def test_the_monitor_needs_its_sqlite_adapters(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="build_default_monitor"):
        ProspectiveMonitor(MarketIntelligenceService(FakeMarket({})), db_path=tmp_path / "ic.db")


# --- when a cycle does not run ---


def test_outside_the_protocol_window_nothing_is_fetched_or_written(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    service, market = monitor(db, Clock(NOW + timedelta(minutes=2)), settings=MonitorSettings())  # the real window starts 2026-10-19

    result = service.run_once()

    assert result.status == "FUERA_DE_VENTANA" and result.moment == NOW
    assert market.requests == [] and not db.exists()


def test_a_candle_outside_the_fx_week_is_not_evaluated(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    saturday = datetime(2026, 10, 17, 10, 5, tzinfo=UTC)
    service, market = monitor(db, Clock(saturday), settings=MonitorSettings(window_start=NOW, window_end=saturday + HOUR))

    result = service.run_once()

    assert result.status == "MERCADO_CERRADO" and market.requests == [] and not db.exists()


def test_a_cycle_that_runs_too_late_is_a_gap_and_is_never_evaluated(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    clock = Clock(NOW + timedelta(minutes=55))
    service, market = monitor(db, clock)

    result = service.run_once()
    again = service.run_once()

    assert result.status == "TARDE" and again.status == "TARDE" and market.requests == []
    assert count(db, "SELECT count(*) FROM monitor_incidents WHERE kind = 'MONITOR_NO_EJECUTADO' AND moment = ?", NOW.isoformat()) == 1
    assert count(db, "SELECT count(*) FROM monitor_evaluations") == 0 and count(db, "SELECT count(*) FROM scans") == 0


def test_it_waits_for_the_candle_that_just_closed(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    clock = Clock(NOW + HOUR + timedelta(minutes=2))  # the fixture's last candle closed an hour earlier
    service, _ = monitor(db, clock, settings=MonitorSettings(window_start=NOW + HOUR, window_end=NOW + timedelta(days=2)))

    result = service.run_once()

    assert result.status == "ESPERANDO_DATOS" and all(symbol in result.detail for symbol in SYMBOLS)
    assert count(db, "SELECT count(*) FROM monitor_cycles") == 0 and count(db, "SELECT count(*) FROM scans") == 0


# --- a completed cycle ---


def test_a_cycle_evaluates_every_frozen_strategy_on_the_closed_candle(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    service, market = monitor(db, Clock(NOW + timedelta(minutes=2)))

    result = service.run_once()

    assert result.status == "COMPLETADO" and result.moment == NOW
    assert tuple(result.evaluated) == STRATEGY_IDS and result.incidents == 0 and result.gaps_recorded == 0
    assert sorted({r[0] for r in market.requests}) == sorted(SYMBOLS)
    assert len(market.requests) == len(SYMBOLS)  # each instrument downloaded once for the whole cycle

    evaluations = rows(db, "SELECT * FROM monitor_evaluations")
    assert {(e["strategy_id"], e["symbol"]) for e in evaluations} == {(s, i) for s in STRATEGY_IDS for i in SYMBOLS}
    for e in evaluations:
        assert e["record_kind"] == "PROSPECTIVO" and e["bar_close"] == NOW.isoformat() == e["moment"]
        assert e["bar_close"] <= e["data_fetched_at"] <= e["evaluated_at"]  # type: ignore[operator]
    scans = rows(db, "SELECT * FROM scans")
    assert len(scans) == len(STRATEGY_IDS) and all(s["simulated_trade_opened"] == 0 and s["record_kind"] == "PROSPECTIVO" for s in scans)
    assert rows(db, "SELECT moment, status FROM monitor_cycles") == [{"moment": NOW.isoformat(), "status": "COMPLETADO"}]
    recommendations = rows(db, "SELECT * FROM recommendations")
    assert result.new_recommendations == len(recommendations) >= 1
    assert all(r["signal_data_as_of"] == NOW.isoformat() and r["detected_at"] >= r["signal_data_as_of"] for r in recommendations)  # type: ignore[operator]


def test_new_candidates_are_queued_as_notifications_and_never_sent(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    service, _ = monitor(db, Clock(NOW + timedelta(minutes=2)))

    result = service.run_once()

    notices = rows(db, "SELECT * FROM notifications")
    assert result.notifications_queued == len(notices) == result.new_recommendations
    assert all(n["kind"] == "NUEVA_OPORTUNIDAD" and n["status"] == "PENDIENTE" and n["sent_at"] is None for n in notices)
    assert all("NO VALIDADA" in n["text"] and "no se abrió ninguna operación" in n["text"] for n in notices)  # type: ignore[operator]


def test_the_same_candle_is_never_evaluated_twice_even_after_a_restart(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    clock = Clock(NOW + timedelta(minutes=2))
    first, _ = monitor(db, clock)
    first.run_once()
    before = {t: count(db, f"SELECT count(*) FROM {t}") for t in ("scans", "recommendations", "monitor_evaluations", "notifications", "monitor_incidents")}

    clock.now = NOW + timedelta(minutes=30)
    same_process = first.run_once()
    restarted, market = monitor(db, clock)  # a new process on the same database
    after_restart = restarted.run_once()

    assert same_process.status == after_restart.status == "YA_PROCESADO" and market.requests == []
    assert {t: count(db, f"SELECT count(*) FROM {t}") for t in before} == before


def test_an_interrupted_cycle_resumes_only_the_strategies_still_missing(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    clock = Clock(NOW + timedelta(minutes=2))
    calls = {"n": 0}

    def crashing_after_two(path, moment, fetched_at):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] > 2:
            raise KeyboardInterrupt  # the process is killed mid-cycle
        return MonitorScanLedger(path, moment, fetched_at)

    crashed, _ = monitor(db, clock)
    crashed._scan_ledger_factory = crashing_after_two
    with pytest.raises(KeyboardInterrupt):
        crashed.run_once()
    assert rows(db, "SELECT status FROM monitor_cycles") == [{"status": "EN_CURSO"}]

    resumed, _ = monitor(db, clock)
    result = resumed.run_once()

    assert result.status == "COMPLETADO" and tuple(result.evaluated) == STRATEGY_IDS[2:]
    assert count(db, "SELECT count(*) FROM scans") == len(STRATEGY_IDS)
    assert count(db, "SELECT count(*) FROM monitor_evaluations") == len(STRATEGY_IDS) * len(SYMBOLS)


# --- gaps, stale data, provider failures ---


def test_hours_the_monitor_missed_are_recorded_as_gaps_not_evaluated_later(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    clock = Clock(NOW + timedelta(minutes=2))
    service, _ = monitor(db, clock, data=extended(3))
    service.run_once()

    clock.now = NOW + 3 * HOUR + timedelta(minutes=2)  # the machine was off for two candles
    result = service.run_once()

    assert result.status == "COMPLETADO" and result.gaps_recorded == 2
    gaps = rows(db, "SELECT moment, detail FROM monitor_incidents WHERE kind = 'MONITOR_NO_EJECUTADO' ORDER BY moment")
    assert [g["moment"] for g in gaps] == [(NOW + HOUR).isoformat(), (NOW + 2 * HOUR).isoformat()]
    evaluated = {r["bar_close"] for r in rows(db, "SELECT DISTINCT bar_close FROM monitor_evaluations")}
    assert evaluated == {NOW.isoformat(), (NOW + 3 * HOUR).isoformat()}  # nothing retrospective for the missed candles
    assert count(db, "SELECT count(*) FROM recommendations WHERE signal_data_as_of IN (?, ?)", (NOW + HOUR).isoformat(), (NOW + 2 * HOUR).isoformat()) == 0

    clock.now = NOW + 4 * HOUR + timedelta(minutes=2)  # nothing missed this time, and the old gaps are not recorded again
    data_not_yet_there = service.run_once()
    assert data_not_yet_there.status == "ESPERANDO_DATOS" and data_not_yet_there.gaps_recorded == 0
    assert count(db, "SELECT count(*) FROM monitor_incidents WHERE kind = 'MONITOR_NO_EJECUTADO'") == 2


def test_stale_data_is_an_incident_and_an_already_evaluated_candle_is_not_repeated(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    clock = Clock(NOW + timedelta(minutes=2))
    service, _ = monitor(db, clock)  # the provider never moves past the candle that closed at NOW
    service.run_once()

    clock.now = NOW + HOUR + timedelta(minutes=25)  # past data_wait: evaluate with what exists
    result = service.run_once()

    assert result.status == "COMPLETADO_CON_INCIDENCIAS" and result.new_recommendations == 0
    kinds = {r["kind"]: r["n"] for r in rows(db, "SELECT kind, count(*) AS n FROM monitor_incidents WHERE moment = ? GROUP BY kind", (NOW + HOUR).isoformat())}
    assert kinds == {"DATOS_DESACTUALIZADOS": len(STRATEGY_IDS) * len(SYMBOLS), "VELA_YA_EVALUADA": len(STRATEGY_IDS) * len(SYMBOLS)}
    assert count(db, "SELECT count(*) FROM monitor_evaluations") == len(STRATEGY_IDS) * len(SYMBOLS)  # still only the first candle
    stale = rows(db, "SELECT * FROM notifications WHERE kind = 'DATOS_DESACTUALIZADOS'")
    assert len(stale) == len(SYMBOLS) and all("No se generan señales retrospectivas" in n["text"] for n in stale)  # type: ignore[operator]


def test_a_provider_failure_is_recorded_and_the_other_instruments_go_on(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    data = {**DEFAULT_DATA, "EURUSD=X": MarketDataError("no price history")}
    service, _ = monitor(db, Clock(NOW + timedelta(minutes=25)), data=data)

    result = service.run_once()

    assert result.status == "COMPLETADO_CON_INCIDENCIAS" and tuple(result.evaluated) == STRATEGY_IDS
    failures = rows(db, "SELECT symbol FROM monitor_incidents WHERE kind = 'FALLO_PROVEEDOR'")
    assert len(failures) == len(STRATEGY_IDS) and {f["symbol"] for f in failures} == {"EURUSD=X"}
    assert {e["symbol"] for e in rows(db, "SELECT symbol FROM monitor_evaluations")} == {"GBPUSD=X", "USDJPY=X"}
    assert count(db, "SELECT count(*) FROM notifications WHERE kind = 'FALLO_DATOS'") == 1  # one message, not one per strategy


def test_one_strategy_failing_does_not_stop_the_others(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    calls = {"n": 0}

    class Unwritable(MonitorScanLedger):
        def append(self, scan):  # type: ignore[no-untyped-def]
            raise OpportunityLedgerError("disk full")

    def second_fails(path, moment, fetched_at):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return (Unwritable if calls["n"] == 2 else MonitorScanLedger)(path, moment, fetched_at)

    service, _ = monitor(db, Clock(NOW + timedelta(minutes=2)))
    service._scan_ledger_factory = second_fails

    result = service.run_once()

    assert result.status == "COMPLETADO_CON_INCIDENCIAS" and result.incidents == 1
    assert tuple(result.evaluated) == (STRATEGY_IDS[0], *STRATEGY_IDS[2:])
    errors = rows(db, "SELECT strategy_id, detail FROM monitor_incidents WHERE kind = 'ERROR_INTERNO'")
    assert [e["strategy_id"] for e in errors] == [STRATEGY_IDS[1]] and "disk full" in errors[0]["detail"]  # type: ignore[operator]


# --- follow-up, alerts, reports ---


def test_open_candidates_are_followed_up_hypothetically(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    clock = Clock(NOW + timedelta(minutes=2))
    service, _ = monitor(db, clock, data=extended(2))
    first = service.run_once()

    clock.now = NOW + HOUR + timedelta(minutes=2)
    second = service.run_once()

    events = rows(db, "SELECT * FROM candidate_events")
    assert first.new_recommendations >= 1 and events and all(e["hypothetical"] == 1 for e in events)
    assert {e["status"] for e in events} <= {"PENDIENTE", "ACTIVADA", "STOP", "OBJETIVO", "CIERRE_DE_SESIÓN", "EXPIRADA", "NO_EVALUABLE"}
    assert all(e["entry_time"] is None or e["entry_time"] >= NOW.isoformat() for e in events)  # type: ignore[operator]  # never a fill before detection
    changes = rows(db, "SELECT text FROM notifications WHERE kind = 'CAMBIO_RECOMENDACION'")
    assert len(changes) == sum(1 for e in events if e["status"] != "PENDIENTE")
    assert all("HIPOTÉTICO" in c["text"] and "no es una operación" in c["text"] for c in changes)  # type: ignore[operator]
    assert second.notifications_queued >= len(changes)


def test_portfolio_alerts_are_queued_once(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    event = SimpleNamespace(key="p1:stop", transition="ACTIVADA", at=NOW, message="EUR/USD cerca del stop")
    guardian = SimpleNamespace(check=lambda: SimpleNamespace(events=[event]))
    clock = Clock(NOW + timedelta(minutes=2))
    service, _ = monitor(db, clock, data=extended(1), guardian=guardian)

    service.run_once()
    clock.now = NOW + HOUR + timedelta(minutes=2)
    service.run_once()  # the guardian reports the very same event again

    alerts = rows(db, "SELECT text, status FROM notifications WHERE kind = 'ALERTA_PORTAFOLIO'")
    assert len(alerts) == 1 and alerts[0]["status"] == "PENDIENTE"
    assert "no se envió ninguna orden" in alerts[0]["text"]  # type: ignore[operator]


def test_the_monitoring_sheets_are_built_from_the_registry(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    clock = Clock(NOW + timedelta(minutes=2))
    service, _ = monitor(db, clock)
    service.run_once()
    clock.now = NOW + 2 * HOUR + timedelta(minutes=25)
    service.run_once()  # one missed candle, then stale data

    with connect(db) as conn:
        sheets = {name: body for name, _, body in sheets_from(conn)}

    assert len(sheets["Ciclos de monitoreo"]) == 2
    assert len(sheets["Senales prospectivas"]) == len(STRATEGY_IDS) * len(SYMBOLS)
    assert len(sheets["Errores y huecos"]) == count(db, "SELECT count(*) FROM monitor_incidents") > 0
    assert len(sheets["Notificaciones"]) == count(db, "SELECT count(*) FROM notifications") > 0


# --- continuous mode and the shared download ---


def test_continuous_mode_repeats_cycles_and_survives_an_ordinary_failure(tmp_path: Path) -> None:
    service, _ = monitor(tmp_path / "ic.db", Clock(NOW + timedelta(minutes=2)), settings=MonitorSettings())
    outcomes = iter([RuntimeError("network down"), None, None])
    runs, sleeps = [], []

    def run_once():  # type: ignore[no-untyped-def]
        runs.append(1)
        failure = next(outcomes)
        if failure is not None:
            raise failure
        return SimpleNamespace(moment=NOW, status="FUERA_DE_VENTANA", detail="")

    service.run_once = run_once  # type: ignore[method-assign]
    service.run_forever(timedelta(minutes=5), sleep=sleeps.append, iterations=3)

    assert len(runs) == 3 and sleeps == [300.0, 300.0]  # no sleep after the last iteration


def test_continuous_mode_stops_on_a_protocol_violation(tmp_path: Path) -> None:
    service, _ = monitor(tmp_path / "ic.db", Clock(NOW))

    def violated():  # type: ignore[no-untyped-def]
        raise ProtocolViolationError("parámetros cambiados")

    service.run_once = violated  # type: ignore[method-assign]
    with pytest.raises(ProtocolViolationError):
        service.run_forever(timedelta(seconds=1), sleep=lambda _s: None, iterations=5)


def test_the_cycle_cache_downloads_each_instrument_once_and_remembers_failures() -> None:
    market = FakeMarket({"EURUSD=X": DEFAULT_DATA["EURUSD=X"], "GBPUSD=X": MarketDataError("down")})
    cache = CachingMarket(MarketIntelligenceService(market), Clock(NOW), "30d")

    first = cache.get_intraday_history("EURUSD=X", interval="1h", period="7d")
    second = cache.get_intraday_history("EURUSD=X", interval="1h", period="7d")
    for _ in range(2):
        with pytest.raises(MarketDataError):
            cache.get_intraday_history("GBPUSD=X", interval="1h", period="30d")

    assert first is second and market.requests == [("EURUSD=X", "1h", "30d"), ("GBPUSD=X", "1h", "30d")]
    assert cache.fetched_at == {"EURUSD=X": NOW, "GBPUSD=X": NOW}
