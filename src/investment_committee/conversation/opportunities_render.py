"""Spanish (es-CO) rendering of an opportunity scan (/oportunidades).

Presentation only: every number was computed by simulation/ via
application/. The exploratory ranking is kept apart from authorized
operations - V1 authorizes and opens none - and an unvalidated strategy's
expectancy is shown as NO VALIDADA, never as a figure. Long reports are
split into Telegram-sized messages by the channel adapter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from investment_committee.application import InstrumentScan, OpportunityScan

from .trading_render import _reason_lines, num

DIRECTIONS = {"LONG": "COMPRA (LONG)", "SHORT": "VENTA (SHORT)", "NO_TRADE": "sin dirección"}
CATEGORIES = {
    "OPORTUNIDAD_VALIDADA": "OPORTUNIDAD CON EVIDENCIA HISTÓRICA FAVORABLE",
    "CANDIDATA_EXPLORATORIA": "CANDIDATA EXPLORATORIA",
    "SEÑAL_DESCARTADA": "señal descartada",
    "SIN_SEÑAL": "sin señal",
    "INSTRUMENTO_NO_ELEGIBLE": "no elegible",
    "ERROR_DE_DATOS": "error de datos",
}
VALIDATION = {"VALIDADA": "VALIDADA", "NO_VALIDADA": "NO VALIDADA"}
HEADER_NOTICE = (
    "SIMULACIÓN EXPLORATORIA: ninguna candidata es una operación autorizada. No se abrió ninguna operación "
    "simulada ni se envió ninguna orden; el sistema no está conectado a ningún bróker."
)
CLOSING_NOTICE = (
    "Aviso: precios indicativos de Yahoo Finance (sin bid/ask); spread y deslizamiento son supuestos. Un "
    "stop-loss no garantiza que la pérdida se limite exactamente al riesgo hipotético: un hueco de precio o un "
    "deslizamiento mayor puede hacerla más grande. Análisis educativo, no es asesoría financiera."
)


def _when(moment: datetime) -> str:
    return f"{moment.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC"


def _price(value: Decimal, places: int) -> str:
    return num(value, places)


def render_opportunity_scan(scan: OpportunityScan) -> str:
    limits, account = scan.limits, scan.account
    names = [i.display_name for i in scan.instruments]
    per_trade = limits.risk_per_trade * account.equity
    daily = limits.max_daily_loss * account.day_start_equity
    remaining = daily - max(Decimal("0"), -account.realized_pnl_today)
    hours = scan.timeframe.total_seconds() / 3600
    timeframe = "velas de 1 hora" if hours == 1 else f"velas de {num(Decimal(str(hours)), 0)} horas"
    lines = [
        f"Escáner de oportunidades · {_when(scan.started_at)}",
        HEADER_NOTICE,
        "",
        f"Estrategia: {scan.strategy_name} v{scan.strategy_version} · {timeframe} · base experimental: sus backtests "
        "fueron negativos e inconsistentes",
        f"Instrumentos escaneados ({len(names)}): {', '.join(names)}",
        f"Señales técnicas detectadas: {scan.technical_signals} · superan los controles: {len(scan.candidates)} · "
        f"descartadas: {len(scan.discarded)} · errores: {len(scan.errors)}",
        f"Cuenta hipotética: equity {num(account.equity)} USD · riesgo máx. por operación {num(limits.risk_per_trade * 100)} % "
        f"({num(per_trade)} USD) · pérdida diaria máx. {num(limits.max_daily_loss * 100)} % ({num(daily)} USD; quedan "
        f"{num(max(remaining, Decimal('0')))} USD) · riesgo abierto {num(account.open_risk)} USD",
    ]

    ranked = scan.ranked
    validated = [s for s in ranked if s.category.value == "OPORTUNIDAD_VALIDADA"]
    exploratory = [s for s in ranked if s.category.value == "CANDIDATA_EXPLORATORIA"]
    if validated:
        lines += ["", "Con evidencia histórica favorable (siguen siendo simulación, no operaciones autorizadas):"]
        for scan_item in validated:
            lines += _candidate_block(scan_item)
    if exploratory:
        lines += [
            "",
            f"Ranking exploratorio (máximo {limits.max_visible}). NO son operaciones autorizadas ni oportunidades "
            "validadas: la estrategia no tiene evidencia histórica favorable.",
        ]
        for scan_item in exploratory:
            lines += _candidate_block(scan_item)
    if not ranked:
        lines += [
            "",
            "No hay candidatas en este escaneo. Ningún instrumento tuvo una señal que superara todos los controles de "
            "Python, así que no se propone ninguna operación (no se fabrican operaciones para llenar el ranking).",
        ]

    unranked = [s for s in scan.candidates if s.rank is None]
    if unranked:
        lines += ["", "Superan los controles pero no se muestran en el ranking:"]
        lines += [f"- {s.display_name}: {s.notes[-1] if s.notes else 'fuera del ranking'}" for s in unranked]

    discarded = scan.discarded
    if discarded:
        lines += ["", "Señales técnicas descartadas:"]
        for item in discarded:
            reasons = [n.removeprefix("descartada: ") for n in item.notes] or _signal_reasons(item)
            lines.append(f"- {item.display_name} · {DIRECTIONS[item.technical_direction.value]}: {'; '.join(reasons)}{_data_note(item)}")

    quiet = [s for s in scan.instruments if s.category.value == "SIN_SEÑAL"]
    if quiet:
        lines += ["", "Sin señal técnica:"]
        lines += [f"- {s.display_name}: {' '.join(_signal_reasons(s)) or 'sin señal'}{_data_note(s)}" for s in quiet]

    problems = [s for s in scan.instruments if s.category.value in ("INSTRUMENTO_NO_ELEGIBLE", "ERROR_DE_DATOS")]
    if problems:
        lines += ["", "No elegibles o con errores (el resto del escaneo continuó):"]
        lines += [f"- {s.display_name}: {s.error or '; '.join(s.notes)}" for s in problems]
    other_errors = [e for e in scan.errors if not any(e.startswith(s.display_name) for s in problems)]
    lines += [f"- {e}" for e in other_errors]

    if account.notes:
        lines += ["", "Notas de la cuenta hipotética:", *(f"- {n}" for n in account.notes)]

    ai = (
        f"revisión del comité de IA: {scan.ai_reviews_used} análisis (solo puede objetar, nunca autorizar)"
        if scan.ai_review_enabled
        else "comité de IA: desactivado en este escaneo (sin llamadas pagadas)"
    )
    lines += [
        "",
        "Operaciones autorizadas: ninguna. El escáner V1 no autoriza ni abre operaciones; una propuesta registrada se "
        "pide con /trade EUR/USD.",
        CLOSING_NOTICE,
        f"Auditoría: escaneo {scan.scan_id} · {scan.scanner_version} · {ai} · fuente: {scan.data_source}",
    ]
    return "\n".join(lines)


def _candidate_block(item: InstrumentScan) -> list[str]:
    signal, money, score = item.signal, item.money, item.score
    assert signal is not None and signal.plan is not None and money is not None and score is not None
    plan, places = signal.plan, signal.instrument.price_places
    lines = [
        f"{item.rank}. {item.display_name} · {DIRECTIONS[signal.direction.value]} · {CATEGORIES[item.category.value]}",
        f"   Entrada {_price(plan.entry_price, places)} · Stop-loss {_price(plan.stop_loss, places)} · "
        f"Take-profit {_price(plan.take_profit, places)}",
        f"   Riesgo/beneficio tras costos: {num(money.net_reward_risk)} · Riesgo hipotético: {num(money.risk_amount)} USD · "
        f"Tamaño: {num(Decimal(plan.units), 0)} unidades (margen {num(money.margin_required)} USD)",
        f"   Costos estimados: {num(money.total_costs)} USD (spread {num(money.spread_cost)}, deslizamiento "
        f"{num(money.slippage_cost)}, comisión {num(money.commission)}) · {money.conversion_note}",
        f"   Validación histórica: {VALIDATION[item.validation.status.value]} - {item.validation.detail}",
    ]
    if not item.validation.validated:
        lines.append("   Expectativa neta: NO VALIDADA (no se calcula ni se usa para justificar la candidata)")
    parts = [f"{c.label} {c.points}/{c.max_points} ({c.detail})" for c in score.components]
    lines.append(f"   Puntuación de priorización {score.total}/{score.maximum} (orden, no probabilidad): " + "; ".join(parts))
    lines.append(f"   Por qué la estrategia la señala: {' '.join(_signal_reasons(item))}")
    if item.data is not None:
        data = item.data
        if data.last_closed_bar_close is not None:
            opened = data.last_closed_bar_close - item.signal.timeframe
            last = f"abrió {opened.astimezone(timezone.utc):%Y-%m-%d %H:%M} y cerró {_when(data.last_closed_bar_close)}"
        else:
            last = "sin datos"
        lines.append(
            f"   Datos: última vela COMPLETA usada: {last} · {data.forming_bars_excluded} vela(s) en formación excluida(s) · "
            f"{data.future_bars_dropped} con fecha futura y {data.irregular_bars_dropped} irregular(es) descartada(s)"
        )
    if item.ai_review is not None:
        lines.append(f"   Comité de IA: no objetó ({item.ai_review.detail}); esto no autoriza la operación")
    return lines


def _data_note(item: InstrumentScan) -> str:
    data = item.data
    if data is None or not (data.forming_bars_excluded or data.future_bars_dropped):
        return ""
    return (
        f" (datos: se excluyeron {data.forming_bars_excluded} vela(s) en formación y {data.future_bars_dropped} con fecha "
        "futura; solo se usan velas cerradas)"
    )


def _signal_reasons(item: InstrumentScan) -> list[str]:
    return _reason_lines(item.signal) if item.signal is not None else []
