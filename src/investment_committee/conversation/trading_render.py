"""Spanish (es-CO) rendering for simulated trading, backtests, and lessons.

Presentation only: every number shown was computed by simulation/ (via
application/); nothing here calculates a price, level, or metric. Enum
members are matched by their value strings so this module needs nothing
from simulation/ (conversation/ -> application/ only, S8). Replies lead
with a short summary; the detail lives behind /explicar_trade and
/aprender, to stay readable on a phone.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from investment_committee.application import (
    BacktestResult,
    Lesson,
    LessonCatalog,
    SegmentedBacktest,
    TradeExplanation,
    TradeJournalView,
    TradeSignal,
)

SIMULATION_NOTICE = (
    "SIMULACIÓN: no se envió ninguna orden y el sistema no está conectado a ningún bróker. Precios "
    "indicativos de Yahoo Finance; el spread es un supuesto."
)
NOT_THE_COMMITTEE = "Esta señal de trading es independiente de la decisión de inversión del comité (/analyze)."

DIRECTION_LABELS = {"LONG": "COMPRA", "SHORT": "VENTA", "NO_TRADE": "NO OPERAR"}
STATUS_LABELS = {
    "OPEN": "ABIERTA",
    "TAKE_PROFIT": "TAKE-PROFIT alcanzado",
    "STOP_LOSS": "STOP-LOSS alcanzado",
    "EXPIRED": "VENCIDA (cerrada al precio de ese momento)",
    "LIQUIDATED": "LIQUIDADA por falta de margen",
    "END_OF_DATA": "CERRADA al terminar el periodo",
}


# --- number formatting: thousands with ".", decimals with "," ---


def num(value: Decimal, places: int = 2) -> str:
    return f"{value:,.{places}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def price(value: Decimal) -> str:
    return num(value, 5)


def money(value: Decimal) -> str:
    return f"{num(value, 2)} USD"


def signed_money(value: Decimal) -> str:
    return ("+" if value > 0 else "") + money(value)


def signed_pips(value: Decimal) -> str:
    return ("+" if value > 0 else "") + f"{num(value, 1)} pips"


def percent(fraction: Decimal) -> str:
    return f"{num(fraction * 100, 2)} %"


def _when(moment: datetime) -> str:
    # Provider timestamps carry their exchange offset (Yahoo FX: London time).
    return f"{moment.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC"


def _timeframe_label(signal_or_result: TradeSignal | BacktestResult) -> str:
    hours = signal_or_result.timeframe.total_seconds() / 3600
    return "velas de 1 hora" if hours == 1 else f"velas de {num(Decimal(str(hours)), 0)} horas"


# --- /trade ---


_V2_REASONS = {
    "HTF_CONFIRMS": "El marco de 4 horas confirma la tendencia.",
    "HTF_NOT_CONFIRMING": "El marco de 4 horas no confirma la tendencia.",
    "RSI_IN_BAND": "El RSI14 está en la banda de entrada de la estrategia.",
    "RSI_OUT_OF_BAND": "El RSI14 está fuera de la banda de entrada de la estrategia.",
    "OUTSIDE_TRADING_HOURS": "Fuera del horario de la estrategia (08:00-12:00 de Londres).",
    "RANGE_TOO_NARROW": "El rango reciente es demasiado estrecho frente al ATR.",
    "RANGE_TOO_WIDE": "El rango reciente es demasiado amplio frente al ATR.",
    "RANGE_COMPRESSED": "Rango reciente comprimido (ancho adecuado frente al ATR).",
    "BREAKOUT_UP": "El cierre rompió el máximo del rango.",
    "BREAKOUT_DOWN": "El cierre rompió el mínimo del rango.",
    "NO_BREAKOUT": "Sin ruptura del rango.",
    "MARKET_RANGING": "Mercado lateral (medias planas y cercanas).",
    "MARKET_TRENDING": "Mercado con tendencia: la reversión a la media no aplica.",
    "BAND_EXTREME_LOW": "Precio bajo la banda inferior (2 desviaciones) con RSI14 en sobreventa.",
    "BAND_EXTREME_HIGH": "Precio sobre la banda superior (2 desviaciones) con RSI14 en sobrecompra.",
    "NO_EXTREME": "Sin extremo de precio que revertir.",
    "SETUP_LONG": "Las reglas de la estrategia apuntan a COMPRA.",
    "SETUP_SHORT": "Las reglas de la estrategia apuntan a VENTA.",
}


def _reason_lines(signal: TradeSignal) -> list[str]:
    reading, parameters = signal.reading, signal.parameters
    lines: list[str] = []
    for reason in signal.reasons:
        code = reason.value
        if code == "INSUFFICIENT_DATA":
            lines.append(
                f"Datos insuficientes: hay {signal.bars_used} velas cerradas y se necesitan al menos {parameters.min_bars}."
            )
        elif code == "STALE_DATA":
            hours = num(Decimal(parameters.max_data_age.total_seconds() / 3600), 0)
            lines.append(f"Datos antiguos: la última vela cerrada tiene más de {hours} horas. No se opera con datos viejos.")
        elif code == "INCONSISTENT_DATA":
            lines.append("Datos inconsistentes: las velas no llegaron en orden cronológico. No se opera con datos dudosos.")
        elif code == "MARKET_CLOSED":
            lines.append("El mercado Forex está cerrado (de viernes 22:00 a domingo 22:00 UTC).")
        elif code == "TREND_UP":
            lines.append("Tendencia alcista: el precio y la SMA20 están por encima de la SMA50.")
        elif code == "TREND_DOWN":
            lines.append("Tendencia bajista: el precio y la SMA20 están por debajo de la SMA50.")
        elif code == "TREND_SIDEWAYS":
            lines.append("Tendencia lateral: el precio y las medias no apuntan en la misma dirección.")
        elif code in ("MOMENTUM_CONFIRMS", "MOMENTUM_NOT_CONFIRMING") and reading.rsi is not None:
            if reading.trend is not None and reading.trend.value == "DOWN":
                band = f"{num(parameters.rsi_short_min, 0)} y {num(parameters.rsi_short_max, 0)}"
            else:
                band = f"{num(parameters.rsi_long_min, 0)} y {num(parameters.rsi_long_max, 0)}"
            verdict = "confirma" if code == "MOMENTUM_CONFIRMS" else "no confirma"
            lines.append(f"Impulso: el RSI14 es {num(reading.rsi, 1)}; {verdict} (se pide entre {band}).")
        elif code.startswith("VOLATILITY") and reading.atr_pips is not None:
            band = f"entre {num(parameters.atr_min_pips, 0)} y {num(parameters.atr_max_pips, 0)} pips"
            verdict = {"VOLATILITY_OK": "adecuada", "VOLATILITY_TOO_LOW": "demasiado baja", "VOLATILITY_TOO_HIGH": "demasiado alta"}[code]
            lines.append(f"Volatilidad {verdict}: ATR14 de {num(reading.atr_pips, 1)} pips por vela (se pide {band}).")
        elif code == "COSTS_TOO_HIGH":
            lines.append(
                f"Costos demasiado altos: spread, deslizamiento y comisión superarían el "
                f"{num(parameters.max_cost_fraction_of_risk * 100, 0)} % del riesgo de la operación."
            )
        elif code == "REWARD_RISK_TOO_LOW":
            lines.append(
                f"Relación riesgo/beneficio insuficiente: después de costos quedaría por debajo de 1:"
                f"{num(parameters.min_net_reward_risk, 1)}."
            )
        elif code in _V2_REASONS:
            lines.append(_V2_REASONS[code])
        elif code == "POSITION_TOO_SMALL":
            lines.append(
                f"La cuenta hipotética no alcanza para el tamaño mínimo de {num(Decimal(signal.account.lot_step_units), 0)} unidades."
            )
    return lines


def render_trade_signal(signal: TradeSignal) -> str:
    direction = DIRECTION_LABELS.get(signal.direction.value, signal.direction.value)
    lines = [f"Trading simulado · {signal.instrument.display_name} · {_timeframe_label(signal)}"]
    if signal.data_as_of is not None:
        age_minutes = int((signal.created_at - signal.data_as_of).total_seconds() // 60)
        lines.append(f"Datos hasta: {_when(signal.data_as_of)} (vela cerrada hace {age_minutes} min)")
    else:
        lines.append("Datos: no hay velas cerradas disponibles")
    lines.append(f"Generado: {_when(signal.created_at)}")
    lines.append("")
    lines.append(f"Dirección: {direction}")
    lines.append("")
    lines.append("Por qué:")
    lines.extend(f"- {line}" for line in _reason_lines(signal))

    plan = signal.plan
    if plan is not None:
        sign = "-" if signal.direction.value == "LONG" else "+"
        opposite = "+" if sign == "-" else "-"
        account = signal.account
        lines += [
            "",
            "Plan simulado:",
            f"- Precio de cotización (medio): {price(plan.quote_price)}",
            f"- Entrada de referencia: {price(plan.entry_price)} (último cierre + spread de "
            f"{num(signal.costs.spread_pips, 1)} pips y deslizamiento de {num(signal.costs.slippage_pips, 1)} pips)",
            f"- Stop-loss: {price(plan.stop_loss)} ({sign}{num(plan.risk_pips, 1)} pips con deslizamiento)",
            f"- Take-profit: {price(plan.take_profit)} ({opposite}{num(plan.reward_pips, 1)} pips)",
            f"- Riesgo/beneficio: 1:{num(plan.gross_reward_risk, 2)} por niveles · 1:{num(plan.net_reward_risk, 2)} con costos",
            f"- Tamaño: {num(Decimal(plan.units), 0)} unidades · margen {money(plan.margin_required)} "
            f"(apalancamiento {num(plan.leverage, 0)}:1)",
            f"- Riesgo estimado: {money(plan.risk_amount)} ({percent(account.risk_fraction)} de una cuenta hipotética "
            f"de {money(account.capital)}) · ganancia objetivo: {money(plan.reward_amount)}",
            f"- Costos estimados: spread {money(plan.spread_cost)} · deslizamiento hasta {money(plan.slippage_cost)} · "
            f"comisión {money(plan.commission)}",
            f"- Vence: {signal.parameters.expiry_bars} velas después de la entrada si no toca stop ni objetivo",
            "- Ejecución simulada: en la apertura de la siguiente vela, con los niveles recalculados desde ese precio "
            "(así lo evalúan /operaciones y el backtest). La entrada de referencia no es un precio ejecutable.",
            "",
            "En palabras sencillas: "
            + (
                "el precio viene subiendo y el impulso lo acompaña sin estar en exceso, así que la estrategia propone "
                "una compra simulada."
                if signal.direction.value == "LONG"
                else "el precio viene bajando y el impulso lo acompaña sin estar en exceso, así que la estrategia propone "
                "una venta simulada."
            )
            + f" Si se equivoca, el stop-loss limita la pérdida a unos {money(plan.risk_amount)}; si acierta, el "
            f"objetivo ganaría unos {money(plan.reward_amount)}.",
        ]
    else:
        lines += [
            "",
            "En palabras sencillas: la estrategia solo propone una operación cuando tendencia, impulso y volatilidad "
            "están de acuerdo, los datos son confiables y los costos lo permiten. Ahora no se cumple todo, así que no "
            "hay entrada, stop-loss ni take-profit. No operar también es una decisión.",
        ]
    lines += [
        "",
        NOT_THE_COMMITTEE,
        "Usa /explicar_trade para la explicación paso a paso y /operaciones para ver cómo van las simuladas.",
        SIMULATION_NOTICE,
    ]
    return "\n".join(lines)


# --- /explicar_trade ---


def render_trade_explanation(explanation: TradeExplanation) -> str:
    signal = explanation.signal
    direction = DIRECTION_LABELS.get(signal.direction.value, signal.direction.value)
    as_of = f" con datos hasta {_when(signal.data_as_of)}" if signal.data_as_of else ""
    lines = [
        f"Explicación paso a paso · {signal.instrument.display_name} · {direction}",
        f"Señal de trading simulado{as_of}.",
    ]
    for number, lesson in enumerate(explanation.lessons, start=1):
        lines += ["", f"{number}. {lesson.title}"]
        lines.append(f"   Valor: {lesson.current_value or 'no disponible en esta señal'}")
        lines.append(f"   Qué es: {lesson.what_it_is}")
        if lesson.for_this_asset:
            lines.append(f"   Para {signal.instrument.display_name}: {lesson.for_this_asset}")
        lines.append(f"   Ojo: {lesson.limitations}")
    lines += ["", "Profundiza con /aprender <concepto>, por ejemplo /aprender rsi.", SIMULATION_NOTICE]
    return "\n".join(lines)


# --- /backtest ---


_ACCOUNT_STATUS = {
    "ACTIVE": "activa: terminó el periodo pudiendo operar",
    "SUSPENDED_DRAWDOWN": "suspendida por el límite de pérdidas",
    "CAPITAL_EXHAUSTED": "capital agotado: no alcanzaba ni para la posición mínima",
    "NEGATIVE_BALANCE": "saldo negativo: las pérdidas superaron el dinero de la cuenta",
}
_REJECTIONS = (
    ("INSUFFICIENT_MARGIN", "margen insuficiente"),
    ("POSITION_TOO_SMALL", "por debajo del tamaño mínimo"),
    ("ENTRY_NOT_AVAILABLE", "sin vela de entrada a tiempo (fin de semana o datos faltantes)"),
    ("TRADING_SUSPENDED", "velas con señal mientras el trading estaba suspendido"),
)


def _day(moment: datetime) -> str:
    return f"{moment.astimezone(timezone.utc):%Y-%m-%d}"


def _status_line(result: BacktestResult) -> str:
    text = f"Estado final de la cuenta: {_ACCOUNT_STATUS.get(result.status.value, result.status.value)}"
    if result.suspended_at is not None:
        text += f" (desde el {_day(result.suspended_at)})"
    if result.debt > 0:
        text += f". Deuda simulada: {money(result.debt)}"
    return text + "."


def _rejection_line(result: BacktestResult) -> str | None:
    counts = {reason.value: count for reason, count in result.metrics.rejections.items()}
    parts = [f"{num(Decimal(counts[code]), 0)} {label}" for code, label in _REJECTIONS if counts.get(code)]
    return "Señales no ejecutadas: " + " · ".join(parts) + "." if parts else None


def _segment_line(result: BacktestResult, share: str, min_trades: int) -> str:
    m = result.metrics
    head = f"- {result.label.capitalize()} ({share}, {_day(result.data_start)} a {_day(result.data_end)}): "
    if m.trades == 0:
        body = "0 operaciones"
    else:
        body = (
            f"{m.trades} operaciones · aciertos {percent(m.win_rate or Decimal('0'))} · "
            f"{signed_money(result.final_equity - result.initial_capital)} ({percent(m.net_return)}) · "
            f"drawdown {percent(m.max_drawdown_fraction)}"
            + (f" · factor {num(m.profit_factor, 2)}" if m.profit_factor is not None else "")
        )
    if result.status.value != "ACTIVE":
        body += f" · {_ACCOUNT_STATUS[result.status.value].split(':')[0]}"
    if m.trades < min_trades:
        body += f" · muestra insuficiente (menos de {min_trades} operaciones)"
    return head + body


def render_backtest(segmented: SegmentedBacktest) -> str:
    result = segmented.full
    m = result.metrics
    costs, account = result.costs, result.account
    lines = [
        f"Backtest (resultados históricos simulados) · {result.instrument.display_name} · {_timeframe_label(result)}",
        f"Periodo: {_day(result.data_start)} a {_day(result.data_end)} ({num(Decimal(result.bars), 0)} velas) · "
        f"estrategia {result.strategy_name} v{result.strategy_version}, sin optimizar · motor v{result.engine_version}",
        "",
        f"Capital inicial: {money(result.initial_capital)} → saldo final: {money(result.final_balance)} "
        f"(rentabilidad neta {percent(m.net_return)})",
        _status_line(result),
    ]
    if m.trades == 0:
        lines.append("Operaciones: 0. La estrategia no encontró oportunidades ejecutables en este periodo.")
    else:
        lines += [
            f"Operaciones: {m.trades} · ganadoras {m.wins} · perdedoras {m.losses} · aciertos {percent(m.win_rate or Decimal('0'))}",
            f"Factor de beneficio: {num(m.profit_factor, 2) if m.profit_factor is not None else 'no aplica'} · "
            f"ganancia media {money(m.average_win or Decimal('0'))} · pérdida media {money(m.average_loss or Decimal('0'))} · "
            f"expectativa por operación {signed_money(m.expectancy or Decimal('0'))}",
            f"Drawdown máximo: {money(m.max_drawdown)} ({percent(m.max_drawdown_fraction)}) · "
            f"patrimonio mínimo: {money(result.lowest_equity)}",
            f"Costos: {money(m.total_costs)} (spread {money(m.spread_cost)} · deslizamiento {money(m.slippage_cost)} · "
            f"comisiones {money(m.commission_cost)}), ya incluidos en el resultado",
        ]
        extras = []
        if m.liquidations:
            extras.append(f"{m.liquidations} liquidadas por margen")
        if m.closed_at_end:
            extras.append(f"{m.closed_at_end} cerradas al terminar el periodo")
        if m.same_bar_conflicts:
            extras.append(f"{m.same_bar_conflicts} con stop y objetivo en la misma vela (contadas como stop)")
        if extras:
            lines.append("Detalle: " + " · ".join(extras) + ".")
    rejection = _rejection_line(result)
    if rejection:
        lines.append(rejection)

    if segmented.segments:
        total = sum(segment.bars for segment in segmented.segments)
        lines += ["", "Por periodos (orden cronológico, cada uno con una cuenta nueva y sin optimizar nada):"]
        for segment in segmented.segments:
            share = f"{num(Decimal(segment.bars) * 100 / Decimal(total), 0)} %"
            lines.append(_segment_line(segment, share, segmented.min_trades_for_confidence))

    lines += [
        "",
        "Para entenderlo:",
        "- Drawdown: la caída desde el punto más alto de la cuenta hasta el más bajo siguiente; es la peor racha que "
        "habrías tenido que aguantar.",
        "- Se puede acertar muchas veces y perder dinero: si las pérdidas son más grandes que las ganancias, o los "
        "costos se comen el margen de cada operación.",
        "- Quedarse sin margen: la garantía exigida por el apalancamiento ya no alcanza; las operaciones nuevas se "
        "rechazan y una posición abierta puede cerrarse a la fuerza.",
        "- Un backtest rentable tampoco garantiza nada: el mercado cambia y un buen resultado puede ser suerte o "
        "ajuste al pasado.",
        "",
        f"Reglas: señal al cierre de una vela; entrada en la apertura de la siguiente (máximo "
        f"{num(Decimal(result.parameters.max_entry_delay.total_seconds() / 3600), 0)} h después, con mercado abierto) "
        f"con spread de {num(costs.spread_pips, 1)} pips y deslizamiento de {num(costs.slippage_pips, 1)} pips; "
        f"riesgo de {percent(account.risk_fraction)} del patrimonio; apalancamiento máximo {num(account.leverage, 0)}:1; "
        f"reserva de margen {percent(account.margin_reserve_fraction)}; liquidación con nivel de margen del "
        f"{percent(account.stop_out_level)}; suspensión con drawdown del {percent(account.max_drawdown_halt)}.",
        "Limitaciones: velas de 1 hora de Yahoo con precio medio (sin bid/ask reales), spread constante supuesto, sin "
        "swaps ni noticias, horario de mercado aproximado. Las entradas del backtest son históricas: no se podían "
        "ejecutar hoy.",
        "Esto NO son ganancias reales: es una simulación sobre datos históricos.",
    ]
    return "\n".join(lines)


# --- /operaciones ---


def render_trade_journal(view: TradeJournalView) -> str:
    if not view.trades:
        lines = ["Todavía no hay operaciones simuladas. Prueba con /trade EUR/USD."]
        if view.no_trade_signals:
            lines.append(f"Señales de NO OPERAR registradas: {view.no_trade_signals}.")
        lines.append(SIMULATION_NOTICE)
        return "\n".join(lines)

    lines = ["Operaciones simuladas (de la más reciente a la más antigua):"]
    closed_total = Decimal("0")
    closed_count = 0
    for trade in view.trades:
        signal, plan, outcome = trade.signal, trade.signal.plan, trade.outcome
        assert plan is not None
        direction = DIRECTION_LABELS.get(signal.direction.value, signal.direction.value)
        opened = _when(signal.data_as_of) if signal.data_as_of else _when(signal.created_at)
        if outcome is not None and outcome.entry_price is not None:
            assert outcome.stop_loss is not None and outcome.take_profit is not None
            levels = (
                f"entrada simulada {price(outcome.entry_price)} · SL {price(outcome.stop_loss)} · "
                f"TP {price(outcome.take_profit)}"
            )
        else:
            levels = f"entrada de referencia {price(plan.entry_price)}"
        head = f"- {opened} · {signal.instrument.display_name} {direction} · {levels}"
        if outcome is None:
            status = "pendiente de evaluar (no hubo datos de mercado)"
        elif outcome.status.value == "OPEN" and outcome.entry_price is None:
            status = "esperando la apertura de la siguiente vela"
        elif outcome.status.value == "OPEN":
            status = f"ABIERTA ({outcome.bars_observed} de {signal.parameters.expiry_bars} velas observadas)"
        elif outcome.status.value == "NOT_FILLED":
            status = "NO EJECUTADA: la siguiente vela llegó demasiado tarde o con el mercado cerrado"
        else:
            assert outcome.pnl_amount is not None and outcome.pnl_pips is not None
            closed_total += outcome.pnl_amount
            closed_count += 1
            status = (
                f"{STATUS_LABELS.get(outcome.status.value, outcome.status.value)}: {signed_pips(outcome.pnl_pips)} "
                f"({signed_money(outcome.pnl_amount)} simulados)"
            )
            if outcome.same_bar_conflict:
                status += " · stop y objetivo en la misma vela: se asumió el stop"
        lines.append(f"{head} → {status}")
    lines.append("")
    if closed_count:
        lines.append(f"Cerradas en esta lista: {closed_count} · resultado simulado neto: {signed_money(closed_total)}")
    if view.no_trade_signals:
        lines.append(f"Señales de NO OPERAR registradas: {view.no_trade_signals}.")
    if not view.refreshed:
        lines.append("No pude actualizar las operaciones abiertas: el proveedor de datos no respondió.")
    lines.append(SIMULATION_NOTICE)
    return "\n".join(lines)


# --- /aprender ---

_GENERAL_ONLY = frozenset({"inversion_trading_cfd"})


def render_lesson(lesson: Lesson) -> str:
    lines = [f"Aprender: {lesson.title}", "", f"Qué es: {lesson.what_it_is}"]
    if lesson.current_value is not None:
        lines.append(f"Valor actual: {lesson.current_value} (según {lesson.context_source}).")
    elif lesson.concept_key in _GENERAL_ONLY:
        lines.append("Valor actual: no aplica, es un concepto general.")
    else:
        lines.append(
            "Valor actual: no disponible. Este concepto no se calculó en tu último análisis; prueba /trade EUR/USD "
            "o /analyze AAPL y vuelve a preguntar."
        )
    lines.append(f"Cómo se interpreta: {lesson.how_to_read}")
    if lesson.for_this_asset:
        lines.append(f"Qué significa para {lesson.asset_label}: {lesson.for_this_asset}")
    lines += [f"Ejemplo: {lesson.example}", f"Limitaciones: {lesson.limitations}", "", "Más conceptos: /aprender"]
    return "\n".join(lines)


def render_lesson_catalog(catalog: LessonCatalog) -> str:
    lines = []
    if catalog.unknown_query:
        lines += [f"No conozco el concepto '{catalog.unknown_query}'.", ""]
    lines.append("Conceptos que puedo explicarte:")
    lines.extend(f"- /aprender {key} - {title}" for key, title in catalog.entries)
    lines += ["", "También funciona con palabras sueltas: /aprender rsi, /aprender stop-loss, /aprender pips."]
    return "\n".join(lines)
