"""Tests for application.notifications - the SQLite outbox the monitor
queues into. Temporary databases only; nothing is ever sent."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from investment_committee.application import notifications as outbox
from investment_committee.memory.database import connect

from tests.application.test_opportunities import NOW

REC = {
    "symbol": "EURUSD=X", "direction": "LONG", "strategy_id": "ruptura-rango-londres@0.1.0", "rank": 1,
    "reference_entry": "1.10512", "reference_stop": "1.10362", "reference_target": "1.10812",
    "detected_at": "2026-10-14T22:02:00+00:00", "signal_data_as_of": "2026-10-14T22:00:00+00:00",
}


def test_an_event_is_queued_once_whatever_the_number_of_attempts(tmp_path: Path) -> None:
    with connect(tmp_path / "ic.db") as conn:
        assert outbox.queue(conn, "NUEVA_OPORTUNIDAD", "op:c1", "uno", NOW) is True
        assert outbox.queue(conn, "NUEVA_OPORTUNIDAD", "op:c1", "otro texto", NOW + timedelta(hours=1)) is False
        assert outbox.queue(conn, "FALLO_DATOS", "f:1", "dos", NOW) is True

        items = outbox.pending(conn)

    assert [(n.kind, n.text, n.status) for n in items] == [("NUEVA_OPORTUNIDAD", "uno", "PENDIENTE"), ("FALLO_DATOS", "dos", "PENDIENTE")]
    assert items[0].created_at == NOW.isoformat()


def test_unknown_kinds_and_statuses_are_refused(tmp_path: Path) -> None:
    with connect(tmp_path / "ic.db") as conn:
        with pytest.raises(ValueError):
            outbox.queue(conn, "ORDEN", "k", "t", NOW)
        outbox.queue(conn, "FALLO_DATOS", "k", "t", NOW)
        with pytest.raises(ValueError):
            outbox.mark(conn, 1, "PENDIENTE", NOW)
        with pytest.raises(sqlite3.IntegrityError):  # the schema refuses it too
            conn.execute("UPDATE notifications SET status = 'EJECUTADA'")


def test_marking_leaves_the_pending_list_and_is_not_repeated(tmp_path: Path) -> None:
    db = tmp_path / "ic.db"
    with connect(db) as conn:
        outbox.queue(conn, "FALLO_DATOS", "a", "a", NOW)
        outbox.queue(conn, "FALLO_DATOS", "b", "b", NOW)
        first, second = (n.notification_id for n in outbox.pending(conn))

        outbox.mark(conn, first, "ENVIADA", NOW + timedelta(minutes=1))
        outbox.mark(conn, first, "DESCARTADA", NOW + timedelta(minutes=9))  # already sent: unchanged

        assert [n.notification_id for n in outbox.pending(conn)] == [second]
        row = conn.execute("SELECT status, sent_at FROM notifications WHERE notification_id = ?", (first,)).fetchone()
        assert (row["status"], row["sent_at"]) == ("ENVIADA", (NOW + timedelta(minutes=1)).isoformat())


def test_notifications_cannot_be_deleted(tmp_path: Path) -> None:
    with connect(tmp_path / "ic.db") as conn:
        outbox.queue(conn, "FALLO_DATOS", "a", "a", NOW)
        with pytest.raises(sqlite3.DatabaseError, match="no se borra"):
            conn.execute("DELETE FROM notifications")


def test_an_opportunity_message_says_it_is_experimental_and_not_an_order() -> None:
    text = outbox.opportunity_text(REC, "EXPERIMENTAL")

    assert text.startswith("SEÑAL EXPERIMENTAL · estrategia NO VALIDADA")
    assert "EUR/USD COMPRA (LONG) · ruptura-rango-londres@0.1.0 · puesto 1" in text
    assert "Referencia (no es una orden): entrada 1.10512 · stop 1.10362 · objetivo 1.10812" in text
    assert "no se abrió ninguna operación, ni real ni simulada" in text
    assert "NO VALIDADA" in outbox.opportunity_text(REC, "BASE_EXPERIMENTAL")


def test_a_status_message_reports_a_hypothetical_result() -> None:
    closed = outbox.status_text({**REC, "direction": "SHORT"}, "CIERRE_DE_SESIÓN", "-12.5", "EXPERIMENTAL")
    running = outbox.status_text(REC, "ACTIVADA", None, "EXPERIMENTAL")

    assert "EUR/USD VENTA (SHORT) (ruptura-rango-londres@0.1.0): CIERRE DE SESIÓN · resultado hipotético -12.50 USD" in closed
    assert "resultado" not in running.splitlines()[1] and "ACTIVADA" in running
    assert all("NO VALIDADA" in t and "no es una operación" in t for t in (closed, running))


def test_data_and_alert_messages() -> None:
    failure = outbox.data_text("FALLO_DATOS", "GBPUSD=X", "2026-10-14T22:00:00+00:00", "sin velas cerradas")
    stale = outbox.data_text("DATOS_DESACTUALIZADOS", None, "2026-10-14T22:00:00+00:00", "detalle")
    alert = outbox.alert_text("ACTIVADA", "EUR/USD cerca del stop")

    assert failure.startswith("Fallo al obtener datos · GBP/USD · vela 2026-10-14T22:00:00+00:00\nsin velas cerradas")
    assert stale.startswith("Datos desactualizados · proveedor")
    assert all("No se generan señales retrospectivas" in t for t in (failure, stale))
    assert alert == "Portfolio Guardian · alerta ACTIVADA\nEUR/USD cerca del stop\nSolo seguimiento: no se envió ninguna orden ni se cerró ninguna posición."
