"""SQLite adapters for the scanner's audit ledger and the tracker - the
prospective registry in data/investment_committee.db (memory.database).

They implement the existing ports (OpportunityScanLedger, TrackerLedger),
so the scanner and the tracker are unchanged. Every row they write is
record_kind = PROSPECTIVO; retrospective backtests live in backtest_runs.

MonitorScanLedger (for the prospective monitor) writes, in ONE transaction:
the scan, its observations, its ranked candidates (recommendations), one
monitor_evaluations row per (strategy, instrument, closed candle) and the
incidents (provider failures, stale data, a candle already evaluated) - so
a crash or a restart never leaves half a cycle behind.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from investment_committee.memory.database import DEFAULT_DB_PATH, DatabaseError, audit, connect, register_strategy, utc

from .opportunities import OpportunityScan
from .opportunity_ledger import OpportunityLedgerError, OpportunityScanLedger, to_audit_record
from .strategies import parameters_fingerprint, strategy_by_id, strategy_parameters
from .tracker import TrackerLedger, TrackerLedgerError, _encode, candidates_from_scan

BASELINE = "tendencia-momentum-volatilidad"


def ensure_strategy(conn: sqlite3.Connection, name: str, version: str, at: datetime) -> str:
    """Register the strategy version with its parameters' fingerprint."""
    try:
        strategy = strategy_by_id(f"{name}@{version}")
        params = strategy_parameters(strategy)
        payload, fingerprint = json.loads(json.dumps(params.__dict__, default=str)), parameters_fingerprint(params)
    except KeyError:  # not in the registry (e.g. a test double): identified by name and version only
        payload, fingerprint = {}, hashlib.sha256(f"{name}@{version}:sin-parametros-registrados".encode()).hexdigest()
    return register_strategy(conn, name, version, payload, fingerprint, "BASE_EXPERIMENTAL" if name == BASELINE else "EXPERIMENTAL", at)


def insert_scan(conn: sqlite3.Connection, scan: OpportunityScan) -> str:
    record = to_audit_record(scan)
    strategy_id = ensure_strategy(conn, scan.strategy_name, scan.strategy_version, scan.started_at)
    conn.execute(
        "INSERT INTO scans (scan_id, record_kind, strategy_id, started_at, finished_at, scanner_version, data_source, "
        "technical_signals, candidates, simulated_trade_opened, audit_json) VALUES (?, 'PROSPECTIVO', ?, ?, ?, ?, ?, ?, ?, 0, ?)",
        (scan.scan_id, strategy_id, utc(scan.started_at), utc(scan.finished_at), scan.scanner_version, scan.data_source,
         scan.technical_signals, len(scan.candidates), json.dumps(record, ensure_ascii=False, sort_keys=True)),
    )
    for item in scan.instruments:
        data = item.data
        conn.execute(
            "INSERT INTO observations (scan_id, symbol, category, technical_direction, last_close, last_closed_bar_close, bars_received, "
            "future_bars_dropped, forming_bars_excluded, irregular_bars_dropped, reasons_json, notes_json, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (scan.scan_id, item.symbol, item.category.value, item.technical_direction.value,
             str(item.signal.reading.quote_price) if item.signal and item.signal.reading.quote_price is not None else None,
             utc(data.last_closed_bar_close) if data and data.last_closed_bar_close else None,
             data.bars_received if data else None, data.future_bars_dropped if data else None,
             data.forming_bars_excluded if data else None, data.irregular_bars_dropped if data else None,
             json.dumps([r.value for r in item.signal.reasons] if item.signal else []),
             json.dumps(list(item.notes), ensure_ascii=False), item.error),
        )
    audit(conn, scan.started_at, "ESCANEO", "scan", scan.scan_id, {"strategy": strategy_id, "ranked": len(scan.ranked)})
    return strategy_id


def insert_observed(conn: sqlite3.Connection, c: dict[str, Any], at: datetime) -> bool:
    """A ranked candidate; False (nothing written) if the same strategy,
    instrument and candle was already recorded."""
    strategy_id = ensure_strategy(conn, c["strategy_name"], c["strategy_version"], at)
    data_as_of = utc(datetime.fromisoformat(c["signal_data_as_of"]))
    exists = conn.execute(
        "SELECT 1 FROM recommendations WHERE strategy_id = ? AND symbol = ? AND signal_data_as_of = ?", (strategy_id, c["symbol"], data_as_of)
    ).fetchone()
    if exists:
        return False
    conn.execute(
        "INSERT INTO recommendations (candidate_id, record_kind, scan_id, strategy_id, symbol, direction, rank, category, detected_at, "
        "signal_data_as_of, reference_entry, reference_stop, reference_target, atr, units, spread_pips, slippage_pips, frozen_json) "
        "VALUES (?, 'PROSPECTIVO', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (c["candidate_id"], c["scan_id"], strategy_id, c["symbol"], c["direction"], c["rank"], c["category"],
         utc(datetime.fromisoformat(c["detected_at"])), data_as_of, c["reference_entry"], c["reference_stop"], c["reference_target"],
         c["atr"], c["units"], c["spread_pips"], c["slippage_pips"], json.dumps(c, ensure_ascii=False, sort_keys=True)),
    )
    audit(conn, at, "OBSERVADA", "recommendation", c["candidate_id"], {"symbol": c["symbol"], "direction": c["direction"]})
    return True


class SqliteOpportunityScanLedger(OpportunityScanLedger):
    def __init__(self, path: Path | str = DEFAULT_DB_PATH) -> None:
        self._path = Path(path)

    def append(self, scan: OpportunityScan) -> None:
        try:
            with connect(self._path) as conn:
                insert_scan(conn, scan)
        except (sqlite3.Error, DatabaseError) as exc:
            raise OpportunityLedgerError(f"cannot record scan {scan.scan_id}: {exc}") from exc

    def records(self) -> Sequence[dict[str, Any]]:
        try:
            with connect(self._path) as conn:
                return tuple(json.loads(r["audit_json"]) for r in conn.execute("SELECT audit_json FROM scans ORDER BY started_at, rowid"))
        except (sqlite3.Error, DatabaseError) as exc:
            raise OpportunityLedgerError(str(exc)) from exc


class MonitorScanLedger(SqliteOpportunityScanLedger):
    """One monitor cycle's scan for one strategy, written atomically."""

    def __init__(self, path: Path | str, moment: datetime, data_fetched_at: datetime) -> None:
        super().__init__(path)
        self.moment = moment
        self.data_fetched_at = data_fetched_at
        self.new_recommendations: list[str] = []
        self.incidents: list[tuple[str, str | None, str]] = []

    def append(self, scan: OpportunityScan) -> None:
        moment = utc(self.moment)
        try:
            with connect(self._path) as conn:
                strategy_id = insert_scan(conn, scan)
                for candidate in candidates_from_scan(scan):
                    if insert_observed(conn, _encode(candidate), scan.started_at):
                        self.new_recommendations.append(candidate.candidate_id)
                for item in scan.instruments:
                    data = item.data
                    if item.error or data is None or data.last_closed_bar_close is None:
                        self._incident(conn, scan.started_at, "FALLO_PROVEEDOR", item.symbol, strategy_id, item.error or "sin velas cerradas")
                        continue
                    bar_close = utc(data.last_closed_bar_close)
                    if bar_close < moment:  # type: ignore[operator]
                        self._incident(conn, scan.started_at, "DATOS_DESACTUALIZADOS", item.symbol, strategy_id,
                                       f"última vela cerrada {bar_close}; se esperaba la que cierra {moment}")
                    seen = conn.execute(
                        "SELECT 1 FROM monitor_evaluations WHERE strategy_id = ? AND symbol = ? AND bar_close = ?", (strategy_id, item.symbol, bar_close)
                    ).fetchone()
                    if seen:
                        self._incident(conn, scan.started_at, "VELA_YA_EVALUADA", item.symbol, strategy_id, f"la vela que cierra {bar_close} ya se evaluó")
                        continue
                    conn.execute(
                        "INSERT INTO monitor_evaluations (strategy_id, symbol, bar_close, record_kind, moment, scan_id, evaluated_at, data_fetched_at, "
                        "category, technical_direction) VALUES (?, ?, ?, 'PROSPECTIVO', ?, ?, ?, ?, ?, ?)",
                        (strategy_id, item.symbol, bar_close, moment, scan.scan_id, utc(scan.started_at), utc(self.data_fetched_at),
                         item.category.value, item.technical_direction.value),
                    )
        except (sqlite3.Error, DatabaseError) as exc:
            raise OpportunityLedgerError(f"cannot record monitor scan {scan.scan_id}: {exc}") from exc

    def _incident(self, conn: sqlite3.Connection, at: datetime, kind: str, symbol: str | None, strategy_id: str | None, detail: str) -> None:
        conn.execute("INSERT INTO monitor_incidents (at, moment, kind, symbol, strategy_id, detail) VALUES (?, ?, ?, ?, ?, ?)",
                     (utc(at), utc(self.moment), kind, symbol, strategy_id, detail))
        self.incidents.append((kind, symbol, detail))


class SqliteTrackerLedger(TrackerLedger):
    """OBSERVED events become `recommendations` rows, STATUS events
    `candidate_events` rows (one per status per candidate, never updated)."""

    def __init__(self, path: Path | str = DEFAULT_DB_PATH) -> None:
        self._path = Path(path)

    def append(self, event: dict[str, Any]) -> None:
        try:
            with connect(self._path) as conn:
                at = datetime.fromisoformat(event["recorded_at"])
                if event["event"] == "OBSERVED":
                    insert_observed(conn, event["candidate"], at)
                elif event["event"] == "STATUS":
                    e = event["evaluation"]

                    def t(key: str) -> str | None:
                        return utc(datetime.fromisoformat(e[key])) if e.get(key) else None

                    conn.execute(
                        "INSERT INTO candidate_events (candidate_id, status, evaluated_at, bars_used_until, entry_time, entry_price, exit_time, "
                        "exit_price, net_pnl_usd, hypothetical, evaluation_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                        (event["candidate_id"], e["status"], t("evaluated_at"), t("bars_used_until"), t("entry_time"), e.get("entry_price"),
                         t("exit_time"), e.get("exit_price"), e.get("net_pnl_usd"), json.dumps(e, ensure_ascii=False, sort_keys=True)),
                    )
                    audit(conn, at, "ESTADO", "recommendation", event["candidate_id"], {"status": e["status"], "net_pnl_usd": e.get("net_pnl_usd")})
                else:
                    raise TrackerLedgerError(f"unknown tracker event {event['event']}")
        except (sqlite3.Error, DatabaseError) as exc:
            raise TrackerLedgerError(str(exc)) from exc

    def events(self) -> Sequence[dict[str, Any]]:
        try:
            with connect(self._path) as conn:
                observed = [
                    {"event": "OBSERVED", "candidate_id": r["candidate_id"], "recorded_at": r["detected_at"], "candidate": json.loads(r["frozen_json"])}
                    for r in conn.execute("SELECT candidate_id, detected_at, frozen_json FROM recommendations ORDER BY rowid")
                ]
                statuses = [
                    {"event": "STATUS", "candidate_id": r["candidate_id"], "recorded_at": r["evaluated_at"], "evaluation": json.loads(r["evaluation_json"])}
                    for r in conn.execute("SELECT candidate_id, evaluated_at, evaluation_json FROM candidate_events ORDER BY event_id")
                ]
        except (sqlite3.Error, DatabaseError) as exc:
            raise TrackerLedgerError(str(exc)) from exc
        return tuple([*observed, *statuses])
