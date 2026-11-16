"""Local SQLite registry (sqlite3, standard library): data/investment_committee.db.

Responsibilities, checked against the existing JSONL ledgers so nothing is
stored twice:
  - kept as they are (unchanged, not migrated): decisions.jsonl (the
    deterministic committee), ai_committee.jsonl (AI audit), and
    simulated_trades.jsonl (the /trade journal);
  - moved to SQLite (they had never been written in production, so there
    is nothing to migrate): the opportunity scans, the candidates the scanner
    ranked and their hypothetical follow-up, the portfolio alerts, and a
    general audit trail; plus the registry of strategy versions and the
    summaries of RETROSPECTIVE backtests.
The JSONL classes for scans, tracker and alerts remain available, for
tests and as a fallback.

Integrity:
  - versioned migrations (schema_migrations), applied in order, each in its
    own transaction;
  - every timestamp is UTC ISO 8601 ("...+00:00", enforced by CHECK);
  - unique ids; foreign keys ON; enumerated values CHECKed;
  - PROSPECTIVE records (scans, recommendations, their events) and
    RETROSPECTIVE ones (backtest_runs) are told apart by `record_kind`, a
    column each table pins to its one allowed value;
  - append-only tables: UPDATE and DELETE raise (triggers). Only the
    alerts' current state (alert_states) is mutable - it is a state, and
    its history goes to alert_events;
  - a strategy version is registered with the SHA-256 of its parameters;
    the same name@version with other parameters is refused.
  - no strategy status can be VALIDADA in this schema: validating requires
    a future migration that implements the pre-registered protocol.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

DEFAULT_DB_PATH = Path("data") / "investment_committee.db"
_UTC = "LIKE '%+00:00'"
APPEND_ONLY = ("strategies", "scans", "observations", "recommendations", "candidate_events", "alert_events", "audit_events", "backtest_runs")

MIGRATIONS: tuple[tuple[int, str, str], ...] = (
    (1, "registro prospectivo inicial", f"""
CREATE TABLE strategies (
    strategy_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('BASE_EXPERIMENTAL', 'EXPERIMENTAL')),
    parameters_json TEXT NOT NULL,
    parameters_sha256 TEXT NOT NULL CHECK (length(parameters_sha256) = 64),
    registered_at TEXT NOT NULL CHECK (registered_at {_UTC}),
    UNIQUE (name, version)
);
CREATE TABLE scans (
    scan_id TEXT PRIMARY KEY,
    record_kind TEXT NOT NULL CHECK (record_kind = 'PROSPECTIVO'),
    strategy_id TEXT NOT NULL REFERENCES strategies (strategy_id),
    started_at TEXT NOT NULL CHECK (started_at {_UTC}),
    finished_at TEXT NOT NULL CHECK (finished_at {_UTC}),
    scanner_version TEXT NOT NULL,
    data_source TEXT NOT NULL,
    technical_signals INTEGER NOT NULL CHECK (technical_signals >= 0),
    candidates INTEGER NOT NULL CHECK (candidates >= 0),
    simulated_trade_opened INTEGER NOT NULL CHECK (simulated_trade_opened = 0),
    audit_json TEXT NOT NULL
);
CREATE TABLE observations (
    scan_id TEXT NOT NULL REFERENCES scans (scan_id),
    symbol TEXT NOT NULL,
    category TEXT NOT NULL,
    technical_direction TEXT NOT NULL CHECK (technical_direction IN ('LONG', 'SHORT', 'NO_TRADE')),
    last_close TEXT,
    last_closed_bar_close TEXT CHECK (last_closed_bar_close IS NULL OR last_closed_bar_close {_UTC}),
    bars_received INTEGER,
    future_bars_dropped INTEGER,
    forming_bars_excluded INTEGER,
    irregular_bars_dropped INTEGER,
    reasons_json TEXT NOT NULL,
    notes_json TEXT NOT NULL,
    error TEXT,
    PRIMARY KEY (scan_id, symbol)
);
CREATE TABLE recommendations (
    candidate_id TEXT PRIMARY KEY,
    record_kind TEXT NOT NULL CHECK (record_kind = 'PROSPECTIVO'),
    scan_id TEXT NOT NULL REFERENCES scans (scan_id),
    strategy_id TEXT NOT NULL REFERENCES strategies (strategy_id),
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    rank INTEGER CHECK (rank IS NULL OR rank BETWEEN 1 AND 3),
    category TEXT NOT NULL CHECK (category IN ('CANDIDATA_EXPLORATORIA', 'OPORTUNIDAD_VALIDADA')),
    detected_at TEXT NOT NULL CHECK (detected_at {_UTC}),
    signal_data_as_of TEXT NOT NULL CHECK (signal_data_as_of {_UTC}),
    reference_entry TEXT NOT NULL,
    reference_stop TEXT NOT NULL,
    reference_target TEXT NOT NULL,
    atr TEXT NOT NULL,
    units INTEGER NOT NULL CHECK (units > 0),
    spread_pips TEXT NOT NULL,
    slippage_pips TEXT NOT NULL,
    frozen_json TEXT NOT NULL,
    CHECK (signal_data_as_of <= detected_at)
);
CREATE TABLE candidate_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL REFERENCES recommendations (candidate_id),
    status TEXT NOT NULL CHECK (status IN ('PENDIENTE', 'ACTIVADA', 'STOP', 'OBJETIVO', 'CIERRE_DE_SESIÓN', 'EXPIRADA', 'NO_EVALUABLE')),
    evaluated_at TEXT NOT NULL CHECK (evaluated_at {_UTC}),
    bars_used_until TEXT CHECK (bars_used_until IS NULL OR bars_used_until {_UTC}),
    entry_time TEXT CHECK (entry_time IS NULL OR entry_time {_UTC}),
    entry_price TEXT,
    exit_time TEXT CHECK (exit_time IS NULL OR exit_time {_UTC}),
    exit_price TEXT,
    net_pnl_usd TEXT,
    hypothetical INTEGER NOT NULL CHECK (hypothetical = 1),
    evaluation_json TEXT NOT NULL,
    UNIQUE (candidate_id, status),
    CHECK (bars_used_until IS NULL OR bars_used_until <= evaluated_at)
);
CREATE TABLE alert_states (
    key TEXT PRIMARY KEY,
    active INTEGER NOT NULL CHECK (active IN (0, 1)),
    since TEXT CHECK (since IS NULL OR since {_UTC}),
    last_notified TEXT CHECK (last_notified IS NULL OR last_notified {_UTC}),
    last_value TEXT
);
CREATE TABLE alert_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    kind TEXT NOT NULL,
    position_id TEXT,
    transition TEXT NOT NULL CHECK (transition IN ('ACTIVADA', 'REPETIDA', 'RESUELTA')),
    at TEXT NOT NULL CHECK (at {_UTC}),
    message TEXT NOT NULL
);
CREATE TABLE audit_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL CHECK (at {_UTC}),
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id TEXT,
    detail_json TEXT NOT NULL
);
CREATE TABLE backtest_runs (
    run_id TEXT PRIMARY KEY,
    record_kind TEXT NOT NULL CHECK (record_kind = 'RETROSPECTIVO'),
    strategy_id TEXT NOT NULL REFERENCES strategies (strategy_id),
    period_start TEXT NOT NULL CHECK (period_start {_UTC}),
    period_end TEXT NOT NULL CHECK (period_end {_UTC}),
    mode TEXT NOT NULL CHECK (mode IN ('CONTINUO', 'NORMALIZADO')),
    policy TEXT NOT NULL CHECK (policy IN ('A', 'B', 'C')),
    cost_scenario TEXT NOT NULL,
    month TEXT,
    data_sha256 TEXT NOT NULL,
    net_pnl TEXT NOT NULL,
    net_return TEXT NOT NULL,
    trades INTEGER NOT NULL,
    summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL CHECK (created_at {_UTC})
);
CREATE INDEX candidate_events_by_candidate ON candidate_events (candidate_id, event_id);
CREATE INDEX recommendations_by_detection ON recommendations (detected_at);
""" + "".join(
        f"CREATE TRIGGER {t}_no_update BEFORE UPDATE ON {t} BEGIN SELECT RAISE(ABORT, '{t} es de solo anexado'); END;\n"
        f"CREATE TRIGGER {t}_no_delete BEFORE DELETE ON {t} BEGIN SELECT RAISE(ABORT, '{t} es de solo anexado'); END;\n"
        for t in APPEND_ONLY
    )),
    (2, "monitoreo prospectivo", f"""
CREATE TABLE monitor_cycles (
    moment TEXT PRIMARY KEY CHECK (moment {_UTC}),
    started_at TEXT NOT NULL CHECK (started_at {_UTC}),
    finished_at TEXT CHECK (finished_at IS NULL OR finished_at {_UTC}),
    status TEXT NOT NULL CHECK (status IN ('EN_CURSO', 'COMPLETADO', 'COMPLETADO_CON_INCIDENCIAS')),
    detail_json TEXT NOT NULL,
    CHECK (moment <= started_at)
);
CREATE TABLE monitor_evaluations (
    strategy_id TEXT NOT NULL REFERENCES strategies (strategy_id),
    symbol TEXT NOT NULL,
    bar_close TEXT NOT NULL CHECK (bar_close {_UTC}),
    record_kind TEXT NOT NULL CHECK (record_kind = 'PROSPECTIVO'),
    moment TEXT NOT NULL REFERENCES monitor_cycles (moment),
    scan_id TEXT NOT NULL REFERENCES scans (scan_id),
    evaluated_at TEXT NOT NULL CHECK (evaluated_at {_UTC}),
    data_fetched_at TEXT NOT NULL CHECK (data_fetched_at {_UTC}),
    category TEXT NOT NULL,
    technical_direction TEXT NOT NULL CHECK (technical_direction IN ('LONG', 'SHORT', 'NO_TRADE')),
    PRIMARY KEY (strategy_id, symbol, bar_close),
    CHECK (bar_close <= moment AND moment <= evaluated_at)
);
CREATE TABLE monitor_incidents (
    incident_id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL CHECK (at {_UTC}),
    moment TEXT CHECK (moment IS NULL OR moment {_UTC}),
    kind TEXT NOT NULL CHECK (kind IN ('MONITOR_NO_EJECUTADO', 'FALLO_PROVEEDOR', 'DATOS_DESACTUALIZADOS', 'VELA_YA_EVALUADA', 'ERROR_INTERNO')),
    symbol TEXT,
    strategy_id TEXT,
    detail TEXT NOT NULL
);
CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL CHECK (created_at {_UTC}),
    kind TEXT NOT NULL CHECK (kind IN ('NUEVA_OPORTUNIDAD', 'CAMBIO_RECOMENDACION', 'FALLO_DATOS', 'DATOS_DESACTUALIZADOS', 'ALERTA_PORTAFOLIO')),
    dedupe_key TEXT NOT NULL UNIQUE,
    text TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDIENTE', 'ENVIADA', 'DESCARTADA')),
    sent_at TEXT CHECK (sent_at IS NULL OR sent_at {_UTC})
);
CREATE UNIQUE INDEX recommendations_once_per_candle ON recommendations (strategy_id, symbol, signal_data_as_of);
""" + "".join(
        f"CREATE TRIGGER {t}_no_update BEFORE UPDATE ON {t} BEGIN SELECT RAISE(ABORT, '{t} es de solo anexado'); END;\n"
        f"CREATE TRIGGER {t}_no_delete BEFORE DELETE ON {t} BEGIN SELECT RAISE(ABORT, '{t} es de solo anexado'); END;\n"
        for t in ("monitor_evaluations", "monitor_incidents")
    ) + "CREATE TRIGGER notifications_no_delete BEFORE DELETE ON notifications BEGIN SELECT RAISE(ABORT, 'notifications no se borra'); END;\n"),
)


class DatabaseError(Exception):
    pass


def utc(moment: datetime | None) -> str | None:
    """The one timestamp format stored: UTC ISO 8601 with +00:00."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        raise DatabaseError("naive datetime: every stored timestamp must be timezone-aware")
    return moment.astimezone(timezone.utc).isoformat()


def migrate(conn: sqlite3.Connection, now: datetime | None = None) -> list[int]:
    """Apply missing migrations in order; returns the versions applied."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
    done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    applied = []
    for version, name, sql in MIGRATIONS:
        if version in done:
            continue
        at = utc(now or datetime.now(timezone.utc))
        try:
            conn.execute("BEGIN")
            for statement in _statements(sql):
                conn.execute(statement)
            conn.execute("INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)", (version, name, at))
            conn.execute("INSERT INTO audit_events (at, action, entity, entity_id, detail_json) VALUES (?, 'MIGRACION', 'schema', ?, ?)",
                         (at, str(version), json.dumps({"name": name})))
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            conn.execute("ROLLBACK")
            raise DatabaseError(f"migration {version} failed: {exc}") from exc
        applied.append(version)
    return applied


def _statements(sql: str) -> list[str]:
    """Split a migration into statements; trigger bodies contain ';'."""
    out, current = [], ""
    for line in sql.strip().splitlines():
        current += line + "\n"
        if line.rstrip().endswith(";") and (not current.lstrip().upper().startswith("CREATE TRIGGER") or line.rstrip().upper().endswith("END;")):
            out.append(current.strip())
            current = ""
    if current.strip():
        out.append(current.strip())
    return out


@contextmanager
def connect(path: Path | str = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    """A connection with foreign keys on and the schema migrated; commits
    on success, rolls back on error."""
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        migrate(conn)
        conn.execute("BEGIN")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def audit(conn: sqlite3.Connection, at: datetime, action: str, entity: str, entity_id: str | None, detail: Any) -> None:
    conn.execute("INSERT INTO audit_events (at, action, entity, entity_id, detail_json) VALUES (?, ?, ?, ?, ?)",
                 (utc(at), action, entity, entity_id, json.dumps(detail, ensure_ascii=False, default=str, sort_keys=True)))


def register_strategy(conn: sqlite3.Connection, name: str, version: str, parameters: Any, fingerprint: str, status: str, at: datetime) -> str:
    """Register name@version once; the same id with different parameters is refused."""
    strategy_id = f"{name}@{version}"
    row = conn.execute("SELECT parameters_sha256 FROM strategies WHERE strategy_id = ?", (strategy_id,)).fetchone()
    if row is not None:
        if row["parameters_sha256"] != fingerprint:
            raise DatabaseError(f"{strategy_id} ya está registrada con otros parámetros: un cambio exige una versión nueva")
        return strategy_id
    conn.execute(
        "INSERT INTO strategies (strategy_id, name, version, status, parameters_json, parameters_sha256, registered_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (strategy_id, name, version, status, json.dumps(parameters, ensure_ascii=False, default=str, sort_keys=True), fingerprint, utc(at)),
    )
    audit(conn, at, "REGISTRO", "strategy", strategy_id, {"parameters_sha256": fingerprint, "status": status})
    return strategy_id


def record_backtest_run(conn: sqlite3.Connection, row: dict[str, Any], at: datetime) -> None:
    """A RETROSPECTIVE summary (never a prospective record)."""
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id, record_kind, strategy_id, period_start, period_end, mode, policy, cost_scenario, month, "
        "data_sha256, net_pnl, net_return, trades, summary_json, created_at) VALUES "
        "(:run_id, 'RETROSPECTIVO', :strategy_id, :period_start, :period_end, :mode, :policy, :cost_scenario, :month, :data_sha256, "
        ":net_pnl, :net_return, :trades, :summary_json, :created_at)",
        {**row, "created_at": utc(at)},
    )


def tables(conn: sqlite3.Connection) -> Sequence[str]:
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
