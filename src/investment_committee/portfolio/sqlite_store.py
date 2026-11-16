"""SQLite alert store for the Portfolio Guardian (memory.database): the
alerts' current states in alert_states (the only mutable table), every
alert event in alert_events (append-only), plus an audit row per event."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

from investment_committee.memory.database import DEFAULT_DB_PATH, DatabaseError, audit, connect, utc

from .guardian import AlertEvent, AlertState
from .store import AlertStore, PortfolioStoreError


class SqliteAlertStore(AlertStore):
    def __init__(self, path: Path | str = DEFAULT_DB_PATH) -> None:
        self._path = Path(path)

    def load(self) -> dict[str, AlertState]:
        try:
            with connect(self._path) as conn:
                rows = conn.execute("SELECT key, active, since, last_notified, last_value FROM alert_states").fetchall()
        except (sqlite3.Error, DatabaseError) as exc:
            raise PortfolioStoreError(str(exc)) from exc
        return {
            r["key"]: AlertState(r["key"], bool(r["active"]), _time(r["since"]), _time(r["last_notified"]), r["last_value"]) for r in rows
        }

    def save(self, states: Mapping[str, AlertState], events: Sequence[AlertEvent]) -> None:
        try:
            with connect(self._path) as conn:
                for s in states.values():
                    conn.execute(
                        "INSERT INTO alert_states (key, active, since, last_notified, last_value) VALUES (?, ?, ?, ?, ?) "
                        "ON CONFLICT (key) DO UPDATE SET active = excluded.active, since = excluded.since, "
                        "last_notified = excluded.last_notified, last_value = excluded.last_value",
                        (s.key, int(s.active), utc(s.since), utc(s.last_notified), s.last_value),
                    )
                for e in events:
                    conn.execute(
                        "INSERT INTO alert_events (key, kind, position_id, transition, at, message) VALUES (?, ?, ?, ?, ?, ?)",
                        (e.key, e.kind.value, e.position_id, e.transition, utc(e.at), e.message),
                    )
                    audit(conn, e.at, f"ALERTA_{e.transition}", "alert", e.key, {"message": e.message})
        except (sqlite3.Error, DatabaseError) as exc:
            raise PortfolioStoreError(str(exc)) from exc


def _time(text: str | None) -> datetime | None:
    return None if text is None else datetime.fromisoformat(text)
