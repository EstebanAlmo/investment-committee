"""Spanish rendering for /seguimiento, /portafolio and /alertas.

Presentation only. Tracked candidates are labelled as OBSERVED and
hypothetical - never as executed trades. Portfolio values say where each
price came from, and an unavailable valuation shows its reason instead of
a number.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from investment_committee.application import AlertCheck, PortfolioView, TrackerView

from .trading_render import num

DIRECTIONS = {"LONG": "COMPRA (LONG)", "SHORT": "VENTA (SHORT)"}
FINAL = {"STOP", "OBJETIVO", "CIERRE_DE_SESIÓN"}
_MAX_TRACKED = 15


def _when(moment: datetime | None) -> str:
    return "-" if moment is None else f"{moment.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC"


def _signed(value: Decimal) -> str:
    return ("+" if value > 0 else "") + num(value)


def render_tracker(view: TrackerView) -> str:
    lines = [
        f"Seguimiento de candidatas · {_when(view.as_of)}",
        "CANDIDATAS OBSERVADAS, NO OPERACIONES: ninguna se ejecutó (ni siquiera en simulación). El resultado es lo que habría "
        "pasado con datos posteriores a la detección; no es una ganancia ni una pérdida real.",
    ]
    if not view.entries:
        lines += ["", "Todavía no hay candidatas registradas. Las candidatas del ranking de /oportunidades se añaden aquí."]
        return "\n".join(lines)
    finished = [e for _, e in view.entries if e is not None and e.status.value in FINAL and e.net_pnl_usd is not None]
    total = sum((e.net_pnl_usd for e in finished), Decimal("0"))  # type: ignore[misc]
    lines += [
        f"Candidatas: {len(view.entries)} · terminadas con resultado: {len(finished)} · resultado hipotético acumulado "
        f"{_signed(total)} USD (seguimiento prospectivo, sin validar) · eventos nuevos en esta actualización: {view.updated}",
        "",
    ]
    for candidate, evaluation in view.entries[:_MAX_TRACKED]:
        places = candidate.price_places
        status = evaluation.status.value.replace("_", " ") if evaluation else "PENDIENTE (sin evaluar)"
        line = (
            f"- {candidate.display_name} {DIRECTIONS.get(candidate.direction.value, candidate.direction.value)} · detectada "
            f"{_when(candidate.detected_at)} · {status}"
        )
        if evaluation and evaluation.entry_price is not None:
            line += f" · entrada {num(evaluation.entry_price, places)} (stop {num(evaluation.stop_loss, places)}, objetivo {num(evaluation.take_profit, places)})"  # type: ignore[arg-type]
        if evaluation and evaluation.net_pnl_usd is not None:
            line += f" · resultado hipotético {_signed(evaluation.net_pnl_usd)} USD"
        if evaluation and evaluation.same_bar_conflict:
            line += " (stop y objetivo en la misma vela: se tomó el stop)"
        if evaluation and evaluation.detail and evaluation.status.value in ("EXPIRADA", "NO_EVALUABLE", "PENDIENTE"):
            line += f" · {evaluation.detail}"
        lines.append(line)
    if len(view.entries) > _MAX_TRACKED:
        lines.append(f"... y {len(view.entries) - _MAX_TRACKED} más (ver data/ledger/opportunity_tracker.jsonl)")
    lines += [f"Sin datos: {e}" for e in view.data_errors]
    lines += ["", "Estrategia y costos supuestos quedan fijados en cada registro. Entradas a la apertura de la primera vela posterior a la detección."]
    return "\n".join(lines)


def _places(kind: str, currency: str) -> int:
    if kind == "FOREX":
        return 3 if currency == "JPY" else 5
    return 2


def render_portfolio(view: PortfolioView) -> str:
    lines = [
        f"Portafolio vigilado · {_when(view.as_of)}",
        "Solo seguimiento: no se envía ninguna orden ni se cierra ninguna posición. Registra posiciones con scripts/portfolio.py.",
    ]
    if not view.valuations:
        lines += ["", "No hay posiciones registradas ni operaciones simuladas abiertas."]
        return "\n".join(lines)
    lines += [
        f"Posiciones: {len(view.valuations)} · exposición total {num(view.total_exposure_usd)} USD · P&L no realizado "
        f"{_signed(view.total_pnl_usd)} USD" + (f" · {view.unavailable} sin valoración" if view.unavailable else "")
        + ("" if view.market_open else " · mercado Forex cerrado (fin de semana)"),
        "",
    ]
    for v in view.valuations:
        p = v.position
        places = _places(p.kind.value, p.currency)
        quantity = num(p.quantity, 0 if p.quantity == p.quantity.to_integral_value() else 2)
        head = f"- {p.instrument} {p.side.value} · {quantity} × {num(p.multiplier, 0)} · entrada {num(p.entry_price, places)} {p.currency} · {p.source.lower()}"
        if not v.available:
            lines += [head, f"  Valoración NO DISPONIBLE: {v.reason}"]
            continue
        lines += [
            head,
            f"  Precio {num(v.price, places)} ({_when(v.price_as_of)})"  # type: ignore[arg-type]
            + (" · DATOS DESACTUALIZADOS" if v.stale else "") + (f" · {v.proxy_note}" if v.proxy_note else ""),
            f"  P&L no realizado {_signed(v.pnl_usd)} USD ({_signed(v.pnl_pct * 100)} % de la posición) · el activo se movió "  # type: ignore[operator]
            f"{_signed(v.asset_change_pct * 100)} % · valor {num(v.market_value_usd)} USD · margen {num(v.margin_usd)} USD"  # type: ignore[operator]
            + (f" · a {num(v.stop_distance_pct * 100)} % del stop" if v.stop_distance_pct is not None else ""),  # type: ignore[operator]
        ]
    return "\n".join(lines)


def render_alerts(check: AlertCheck) -> str:
    rules = check.rules
    lines = [
        f"Alertas del portafolio · {_when(check.view.as_of)}",
        "Comprobación manual: se ejecutó ahora porque la pediste. No hay ninguna tarea automática activada.",
        "",
        "Reglas configuradas:",
        f"- pérdida no realizada: desde {num(rules.max_loss_usd)} USD o {num(rules.max_loss_pct * 100)} % de la posición",
        f"- cerca del stop: a {num(rules.stop_proximity_pct * 100)} % o menos",
        f"- caída del activo: {num(rules.asset_drop_pct * 100)} % bajo la entrada (para un SHORT es favorable y se dice así)",
        f"- exposición total: desde {num(rules.max_exposure_usd)} USD",
        "- datos de mercado desactualizados",
        f"- se repite como máximo cada {int(rules.cooldown.total_seconds() // 3600)} h mientras siga activa; se resuelve al recuperarse "
        f"{num(rules.hysteresis * 100, 0)} % más allá del umbral",
        "",
    ]
    if check.events:
        lines.append("Novedades de esta comprobación:")
        lines += [f"- {e.transition}: {e.message}" for e in check.events]
    else:
        lines.append("Sin novedades en esta comprobación.")
    active = [s for s in check.states if s.active]
    lines += ["", f"Alertas activas: {len(active)}"]
    lines += [f"- {s.key.split(':')[0].replace('_', ' ').lower()} · {s.key.split(':', 1)[1]} · desde {_when(s.since)}" for s in active]
    return "\n".join(lines)
