"""Excel report regenerated from the SQLite registry (never the other way).

Each run rebuilds the whole workbook from the database, so regenerating it
loses nothing and duplicates nothing: one row per recommendation, one per
latest result, one per day, one per strategy, one per backtest summary,
one per alert event.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from .database import DEFAULT_DB_PATH, connect
from .xlsx import write_xlsx

DEFAULT_XLSX_PATH = Path("data") / "reports" / "investment_committee.xlsx"
FINAL = ("STOP", "OBJETIVO", "CIERRE_DE_SESIÓN")


def _d(text: str | None) -> Decimal | None:
    return None if text is None else Decimal(text)


def sheets_from(conn: sqlite3.Connection) -> list[tuple[str, Sequence[str], list[list[Any]]]]:
    recs = conn.execute(
        "SELECT r.*, s.status AS strategy_status FROM recommendations r JOIN strategies s USING (strategy_id) ORDER BY r.detected_at, r.candidate_id"
    ).fetchall()
    recommendations = [
        [r["candidate_id"], r["record_kind"], r["detected_at"], r["strategy_id"], r["strategy_status"], r["symbol"], r["direction"], r["rank"],
         r["category"], _d(r["reference_entry"]), _d(r["reference_stop"]), _d(r["reference_target"]), r["units"],
         _d(r["spread_pips"]), _d(r["slippage_pips"]), "observada (hipotética), no es una operación"]
        for r in recs
    ]
    latest = {
        row["candidate_id"]: row
        for row in conn.execute("SELECT * FROM candidate_events ORDER BY event_id")
    }
    results = []
    for r in recs:
        e = latest.get(r["candidate_id"])
        results.append([
            r["candidate_id"], r["symbol"], r["direction"], r["strategy_id"], e["status"] if e else "PENDIENTE (sin evaluar)",
            e["evaluated_at"] if e else None, e["entry_time"] if e else None, _d(e["entry_price"]) if e else None,
            e["exit_time"] if e else None, _d(e["exit_price"]) if e else None, _d(e["net_pnl_usd"]) if e else None,
            "hipotético" if e else None,
        ])
    by_day: dict[str, dict[str, Any]] = defaultdict(lambda: {"detected": 0, "closed": 0, "wins": 0, "losses": 0, "pnl": Decimal("0")})
    for r in recs:
        by_day[r["detected_at"][:10]]["detected"] += 1
    for e in latest.values():
        if e["status"] in FINAL and e["net_pnl_usd"] is not None and e["exit_time"]:
            day = by_day[e["exit_time"][:10]]
            pnl = Decimal(e["net_pnl_usd"])
            day["closed"] += 1
            day["wins"] += pnl > 0
            day["losses"] += pnl < 0
            day["pnl"] += pnl
    daily, cumulative = [], Decimal("0")
    for day in sorted(by_day):
        item = by_day[day]
        cumulative += item["pnl"]
        daily.append([day, item["detected"], item["closed"], item["wins"], item["losses"], item["pnl"], cumulative])
    strategies = [
        [s["strategy_id"], s["name"], s["version"], s["status"], "NO VALIDADA", s["parameters_sha256"], s["registered_at"], s["parameters_json"]]
        for s in conn.execute("SELECT * FROM strategies ORDER BY registered_at, strategy_id")
    ]
    backtests = [
        [b["run_id"], b["record_kind"], b["strategy_id"], b["mode"], b["policy"], b["cost_scenario"], b["month"] or "periodo completo",
         b["period_start"], b["period_end"], b["trades"], _d(b["net_pnl"]), _d(b["net_return"]), b["data_sha256"]]
        for b in conn.execute("SELECT * FROM backtest_runs ORDER BY strategy_id, cost_scenario, policy, mode, month")
    ]
    alerts = [
        [a["event_id"], a["at"], a["transition"], a["kind"], a["position_id"], a["key"], a["message"]]
        for a in conn.execute("SELECT * FROM alert_events ORDER BY event_id")
    ]
    signals = [
        [e["moment"], e["strategy_id"], e["symbol"], e["bar_close"], e["category"], e["technical_direction"], e["evaluated_at"],
         e["data_fetched_at"], e["scan_id"], e["record_kind"]]
        for e in conn.execute("SELECT * FROM monitor_evaluations ORDER BY moment, strategy_id, symbol")
    ]
    incidents = [
        [i["incident_id"], i["at"], i["moment"], i["kind"], i["symbol"], i["strategy_id"], i["detail"]]
        for i in conn.execute("SELECT * FROM monitor_incidents ORDER BY incident_id")
    ]
    cycles = [
        [c["moment"], c["status"], c["started_at"], c["finished_at"], c["detail_json"]]
        for c in conn.execute("SELECT * FROM monitor_cycles ORDER BY moment")
    ]
    notices = [
        [n["notification_id"], n["created_at"], n["kind"], n["status"], n["sent_at"], n["text"]]
        for n in conn.execute("SELECT * FROM notifications ORDER BY notification_id")
    ]
    return [
        ("Senales prospectivas", ["vela del ciclo (UTC)", "estrategia", "instrumento", "vela usada cierra (UTC)", "categoría", "dirección técnica",
                                  "evaluada (UTC)", "datos descargados (UTC)", "escaneo", "tipo de registro"], signals),
        ("Recomendaciones", ["candidato", "tipo de registro", "detectada (UTC)", "estrategia", "estado de la estrategia", "instrumento", "dirección",
                             "puesto", "categoría", "entrada ref.", "stop ref.", "objetivo ref.", "unidades", "spread (pips)", "deslizamiento (pips)", "nota"],
         recommendations),
        ("Resultados", ["candidato", "instrumento", "dirección", "estrategia", "estado", "evaluada (UTC)", "entrada (UTC)", "precio de entrada",
                        "salida (UTC)", "precio de salida", "resultado neto USD", "naturaleza"], results),
        ("Evolucion diaria", ["día (UTC)", "detectadas", "cerradas", "ganadoras", "perdedoras", "resultado hipotético USD", "acumulado USD"], daily),
        ("Estrategias", ["id", "nombre", "versión", "estado", "validación", "sha256 de parámetros", "registrada (UTC)", "parámetros"], strategies),
        ("Backtests retrospectivos", ["corrida", "tipo de registro", "estrategia", "modo", "política", "costos", "mes", "inicio (UTC)", "fin (UTC)",
                                      "operaciones", "P&L USD", "rentabilidad", "sha256 de datos"], backtests),
        ("Alertas", ["evento", "momento (UTC)", "transición", "tipo", "posición", "clave", "mensaje"], alerts),
        ("Errores y huecos", ["incidencia", "registrada (UTC)", "vela (UTC)", "tipo", "instrumento", "estrategia", "detalle"], incidents),
        ("Ciclos de monitoreo", ["vela (UTC)", "estado", "inicio (UTC)", "fin (UTC)", "detalle"], cycles),
        ("Notificaciones", ["id", "creada (UTC)", "tipo", "estado", "enviada (UTC)", "texto"], notices),
    ]


def export_excel(db_path: Path | str = DEFAULT_DB_PATH, xlsx_path: Path | str = DEFAULT_XLSX_PATH) -> Path:
    with connect(db_path) as conn:
        sheets = sheets_from(conn)
    target = Path(xlsx_path)
    write_xlsx(target, sheets)
    return target
