"""Notification outbox (SQLite `notifications`): the monitor QUEUES messages
here; it never sends anything. A separate, explicitly invoked sender
(scripts/send_notifications.py, Telegram) delivers PENDIENTE messages when
the user authorizes it. Each message has a dedupe key, so the same event is
queued once however many times a cycle is re-run.

Every opportunity message says it is an EXPERIMENTAL signal from a strategy
that is NOT VALIDATED, and that no operation was opened. None carries a
button or command that would execute anything.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from investment_committee.memory.database import utc

KINDS = ("NUEVA_OPORTUNIDAD", "CAMBIO_RECOMENDACION", "FALLO_DATOS", "DATOS_DESACTUALIZADOS", "ALERTA_PORTAFOLIO")
NAMES = {"EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD", "USDJPY=X": "USD/JPY"}
DIRECTIONS = {"LONG": "COMPRA (LONG)", "SHORT": "VENTA (SHORT)"}


@dataclass(frozen=True)
class Notification:
    notification_id: int
    created_at: str
    kind: str
    text: str
    status: str


def queue(conn: sqlite3.Connection, kind: str, dedupe_key: str, text: str, at: datetime) -> bool:
    """True if newly queued; False if that event was already queued."""
    if kind not in KINDS:
        raise ValueError(f"unknown notification kind {kind}")
    cursor = conn.execute(
        "INSERT OR IGNORE INTO notifications (created_at, kind, dedupe_key, text, status) VALUES (?, ?, ?, ?, 'PENDIENTE')",
        (utc(at), kind, dedupe_key, text),
    )
    return cursor.rowcount == 1


def pending(conn: sqlite3.Connection) -> list[Notification]:
    return [Notification(r["notification_id"], r["created_at"], r["kind"], r["text"], r["status"])
            for r in conn.execute("SELECT * FROM notifications WHERE status = 'PENDIENTE' ORDER BY notification_id")]


def mark(conn: sqlite3.Connection, notification_id: int, status: str, at: datetime) -> None:
    if status not in ("ENVIADA", "DESCARTADA"):
        raise ValueError(status)
    conn.execute("UPDATE notifications SET status = ?, sent_at = ? WHERE notification_id = ? AND status = 'PENDIENTE'",
                 (status, utc(at), notification_id))


def _label(strategy_status: str) -> str:
    if strategy_status == "VALIDADA":  # impossible in the current schema; kept so the wording can never be mixed up
        return "estrategia VALIDADA según el protocolo"
    return "SEÑAL EXPERIMENTAL · estrategia NO VALIDADA"


def opportunity_text(rec: Any, strategy_status: str) -> str:
    return (
        f"{_label(strategy_status)}\n"
        f"{NAMES.get(rec['symbol'], rec['symbol'])} {DIRECTIONS.get(rec['direction'], rec['direction'])} · {rec['strategy_id']} · puesto {rec['rank']}\n"
        f"Referencia (no es una orden): entrada {rec['reference_entry']} · stop {rec['reference_stop']} · objetivo {rec['reference_target']}\n"
        f"Detectada {rec['detected_at']} con la vela que cerró {rec['signal_data_as_of']}.\n"
        "Evaluación prospectiva del protocolo: no se abrió ninguna operación, ni real ni simulada."
    )


def status_text(rec: Any, status: str, net_pnl: str | None, strategy_status: str) -> str:
    result = f" · resultado hipotético {Decimal(net_pnl):+.2f} USD" if net_pnl is not None else ""
    return (
        f"{_label(strategy_status)} · seguimiento\n"
        f"{NAMES.get(rec['symbol'], rec['symbol'])} {DIRECTIONS.get(rec['direction'], rec['direction'])} ({rec['strategy_id']}): {status.replace('_', ' ')}{result}\n"
        "Resultado HIPOTÉTICO de una candidata observada; no es una operación."
    )


def data_text(kind: str, symbol: str | None, moment: str, detail: str) -> str:
    title = "Fallo al obtener datos" if kind == "FALLO_DATOS" else "Datos desactualizados"
    return f"{title} · {NAMES.get(symbol or '', symbol or 'proveedor')} · vela {moment}\n{detail}\nNo se generan señales retrospectivas por este hueco."


def alert_text(transition: str, message: str) -> str:
    return f"Portfolio Guardian · alerta {transition}\n{message}\nSolo seguimiento: no se envió ninguna orden ni se cerró ninguna posición."
