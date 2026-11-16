"""What the viewer shows of one simulated trade, assembled from the verified
study and nothing else. Pure data, standard library only: charts.py draws
it, tests check it against the engine's records.

Rules that keep the picture honest:
  - Candles are the snapshot's bars (Yahoo's single indicative price), in
    their original UTC labels; a bar labelled L covers [L, L + 1h).
  - Indicators are EXACTLY the values the strategy recorded in signals.csv.
    The value recorded for the candle that closed at t is drawn on the bar
    labelled t - 1h (that candle). Where the strategy did not evaluate
    (warm-up, incomplete data, closed market) the value is None: the line
    breaks. Nothing is interpolated or recomputed.
  - Entry, stop, target and exit are the engine's numbers. They are
    ESTIMATED executions on an estimated bid/ask, not observed prices.
  - Stops and targets were compared with the exit side (bar price -/+ half
    the assumed spread), so on candles of the mid price the level "seems"
    to trigger half a spread early or late. `stop_trigger_mid` and
    `target_trigger_mid` are the same levels moved onto the candles' price:
    DERIVED from the recorded level and spread, for reading only.
  - The engine dates an exit inside a bar at the bar's close; the moment
    inside the hour is unknown. `exit_timing` says whether the exit belongs
    to a whole bar (DENTRO_DE_LA_VELA, at `exit_index`) or to an opening
    instant (EN_LA_APERTURA).
  - An indeterminate trade has no exit: both hypothetical results are
    carried, and the interval in which the outcome is unknown.
  - Time zones are for display only (show_time); nothing stored changes.
  - The sheet shows prices and indicators with at most six decimals; the
    study's files keep every digit.

Window: 50 bars before the entry bar, every bar of the trade, 20 after -
fewer when the snapshot has no more. Expected hours without a bar inside
the window are listed in `holes`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from investment_committee.domain import PriceBar
from investment_committee.simulation import ema_cross, ema_pullback, ema_trend_cross
from investment_committee.simulation.forex_backtest import SAME_BAR
from investment_committee.simulation.forex_backtest_study import STANDARD_LOT, YAHOO_CALENDAR, yahoo_week
from investment_committee.simulation.trade_proposal import instrument_for

from .study_store import Run, RunKey, Signals, Study, StudyError, instant, instrument, number

UTC = timezone.utc
HOUR = timedelta(hours=1)
ZONES = {"Bogotá": "America/Bogota", "Nueva York": "America/New_York", "UTC": "UTC"}
DEFAULT_ZONE = "Bogotá"
BARS_BEFORE, BARS_AFTER = 50, 20
WON, LOST, FLAT, UNKNOWN, STILL_OPEN = "GANADORA", "PERDEDORA", "NEUTRA", "INDETERMINADA", "ABIERTA_AL_FINAL"
INSIDE_BAR, AT_OPEN = "DENTRO_DE_LA_VELA", "EN_LA_APERTURA"
OBSERVED, ESTIMATED, DERIVED = "OBSERVADO", "ESTIMADO", "DERIVADO"
SIMULATION_WARNING = ("Simulación con modelo de ejecución ESTIMADO sobre el precio indicativo único de Yahoo (sin bid/ask real). "
                      "No es una operación real ni una rentabilidad neta real.")


def show_time(moment: datetime | None, zone: str = DEFAULT_ZONE) -> str:
    """`moment` in the chosen zone, with the zone and its offset written out."""
    if moment is None:
        return "—"
    local = moment.astimezone(ZoneInfo(ZONES[zone]))
    offset = local.strftime("%z")
    return f"{local.strftime('%Y-%m-%d %H:%M')} {zone} (UTC{offset[:3]}:{offset[3:]})"


def pips(symbol: str, distance: Decimal) -> Decimal:
    rules = instrument_for(symbol)
    assert rules is not None
    return distance / rules.pip


def lots(units: int | Decimal) -> Decimal:
    return Decimal(units) / STANDARD_LOT


def indicator_columns(strategy_id: str, parameters_sha256: str, columns: Sequence[str]) -> dict[str, str]:
    """signals.csv column -> the name to draw it with. The periods are named
    only when the study's fingerprint is that of the parameters in code."""
    if strategy_id == ema_cross.STRATEGY_ID and parameters_sha256 == ema_cross.fingerprint():
        p = ema_cross.EmaCrossParameters()
        named = {"ema_fast": f"EMA{p.ema_fast}", "ema_slow": f"EMA{p.ema_slow}"}
    elif strategy_id == ema_trend_cross.STRATEGY_ID and parameters_sha256 == ema_trend_cross.fingerprint():
        q = ema_trend_cross.EmaTrendCrossParameters()
        named = {"ema_cross_fast": f"EMA{q.ema_cross_fast}", "ema_cross_slow": f"EMA{q.ema_cross_slow}", "ema_trend_short": f"EMA{q.ema_trend_short}",
                 "ema_trend_mid": f"EMA{q.ema_trend_mid}", "ema_trend_long": f"EMA{q.ema_trend_long}"}
    elif strategy_id == ema_pullback.STRATEGY_ID and parameters_sha256 == ema_pullback.fingerprint():
        r = ema_pullback.EmaPullbackParameters()
        named = {"ema_fast": f"EMA{r.ema_fast}", "ema_trend_short": f"EMA{r.ema_trend_short}", "ema_trend_mid": f"EMA{r.ema_trend_mid}",
                 "ema_trend_long": f"EMA{r.ema_trend_long}"}
    else:
        named = {c: c for c in columns if c.startswith("ema_")}
    return {column: label for column, label in named.items() if column in columns}


@dataclass(frozen=True)
class Field:
    section: str
    label: str
    value: str
    kind: str  # OBSERVADO | ESTIMADO | DERIVADO


@dataclass(frozen=True)
class RejectedSignal:
    index: int | None  # the bar of its would-be entry, if inside the window
    at: datetime
    direction: str
    reasons: tuple[str, ...]  # the engine's own rejection codes
    detail: str


@dataclass(frozen=True)
class TradeView:
    key: RunKey
    study_name: str
    study_sha256: str
    data_kind: str
    trade: Mapping[str, Any]  # as run.json holds it
    trade_id: str
    symbol: str
    direction: str
    status: str
    outcome: str  # GANADORA | PERDEDORA | NEUTRA | INDETERMINADA | ABIERTA_AL_FINAL
    exit_reason: str | None
    bars: Sequence[PriceBar]
    signal_index: int | None  # the candle that produced the signal
    entry_index: int  # the bar whose open is the entry
    last_index: int  # the last bar that belongs to the trade
    exit_index: int | None
    exit_timing: str | None
    signal_candle_close_at: datetime
    entry_at: datetime
    exit_at: datetime | None
    recorded_at: datetime | None
    holes: Sequence[tuple[int, tuple[datetime, ...]]]  # (index of the bar before the hole, the missing labels)
    indicators: Mapping[str, Sequence[Decimal | None]]  # name -> one value per bar
    rsi: Sequence[Decimal | None]
    atr: Sequence[Decimal | None]
    entry: Decimal
    stop: Decimal
    target: Decimal
    exit_price: Decimal | None
    stop_trigger_mid: Decimal
    target_trigger_mid: Decimal
    stop_pips: Decimal
    target_pips: Decimal
    reward_risk_gross: Decimal
    units: int
    net_pnl: Decimal | None
    r_multiple: Decimal | None
    conservative: Mapping[str, Any] | None  # INDETERMINADA: the result as if the stop came first ...
    optimistic: Mapping[str, Any] | None  # ... and as if the target did
    uncertainty: Mapping[str, Any] | None
    uncertainty_indexes: tuple[int, int] | None
    rejected: Sequence[RejectedSignal]
    signal_row: Mapping[str, str]
    sheet: Sequence[Field]


def calendar_of(study: Study) -> Callable[[datetime], bool]:
    """Which hours the study expected a bar in. Only Yahoo's calendar is
    named in a manifest so far; any other is read as 'every hour'."""
    return yahoo_week if study.manifest.get("calendar") == YAHOO_CALENDAR else (lambda _moment: True)


def _holes(bars: Sequence[PriceBar], expected: Callable[[datetime], bool]) -> list[tuple[int, tuple[datetime, ...]]]:
    out = []
    for index in range(len(bars) - 1):
        label, missing = bars[index].timestamp + HOUR, []
        while label < bars[index + 1].timestamp:
            if expected(label) and expected(label + HOUR - timedelta(microseconds=1)):
                missing.append(label)
            label += HOUR
        if missing:
            out.append((index, tuple(missing)))
    return out


def _series(bars: Sequence[PriceBar], rows: Mapping[datetime, Mapping[str, str]], column: str) -> tuple[Decimal | None, ...]:
    """The recorded value of `column` for each bar: the evaluation of the
    candle that closed one hour after the bar's label. None when absent."""
    return tuple(number(rows.get(bar.timestamp + HOUR, {}).get(column)) for bar in bars)


def _touched(bar: PriceBar, level: Decimal, half_spread: Decimal, sign: Decimal, *, target: bool) -> bool:
    """The engine's own test: did this bar reach `level` on the exit side?"""
    if target:
        extreme = (bar.high if sign > 0 else bar.low) - sign * half_spread
        return (extreme - level) * sign >= 0
    extreme = (bar.low if sign > 0 else bar.high) - sign * half_spread
    return (extreme - level) * sign <= 0


def build_trade_view(study: Study, run: Run, signals: Signals, trade_id: str, zone: str = DEFAULT_ZONE) -> TradeView:
    trade = run.trade(trade_id)
    p = trade["proposal"]
    symbol = instrument(trade["symbol"])
    series = study.bars.get(symbol)
    if not series:
        raise StudyError(f"el snapshot no tiene velas de {symbol}")
    rows = signals.get(symbol, {})
    labels = {bar.timestamp: index for index, bar in enumerate(series)}
    entry_at, signal_close = instant(trade["entry_at"]), instant(trade["signal_candle_close_at"])
    exit_at, recorded_at = instant(trade["exit_at"]), instant(trade["recorded_at"])
    assert entry_at is not None and signal_close is not None
    if entry_at not in labels:
        raise StudyError(f"falta la vela de entrada {entry_at.isoformat()} de {symbol}: no se puede reconstruir la operación {trade_id}")
    signal_row = rows.get(signal_close)
    if signal_row is None or signal_row.get("outcome") != trade["direction"]:
        raise StudyError(f"signals.csv no tiene la señal {trade['direction']} de {symbol} en {signal_close.isoformat()}")
    if number(signal_row.get("atr")) != number(p["atr"]):
        raise StudyError(f"el ATR de la señal y el de la operación {trade_id} no coinciden")

    entry, stop, target = number(p["entry"]), number(p["stop"]), number(p["target"])
    spread, units = number(p["spread"]), int(p["units"])
    assert entry is not None and stop is not None and target is not None and spread is not None
    sign, half = (Decimal(1) if trade["direction"] == "LONG" else Decimal(-1)), spread / 2
    result, unknown = trade["result"], trade["indeterminate"]

    # where the trade ends on the tape
    first = labels[entry_at]
    end_moment = exit_at or recorded_at
    exit_index: int | None = None
    timing: str | None = None
    if end_moment is None:  # open at the end
        last = len(series) - 1
    else:
        before = labels.get(end_moment - HOUR)
        at_open = labels.get(end_moment)
        if result is not None:
            reason = result["reason"]
            inside = before is not None and before >= first and (
                (reason == "STOP" and trade["exit_price_source"] == "NIVEL")
                or (reason == "OBJETIVO" and _touched(series[before], target, half, sign, target=True)))
            if inside:
                exit_index, timing = before, INSIDE_BAR
            else:
                exit_index, timing = (at_open if at_open is not None else before), AT_OPEN
            last = exit_index if exit_index is not None else first
        else:  # indeterminate: the bar that held both levels, or the first bar after the hole
            same_bar = (trade["uncertainty"] or {}).get("kind") == SAME_BAR
            last = max(i for i in ((before if same_bar else at_open), before, first) if i is not None) if not same_bar or before is None else before
    start, stop_at = max(0, first - BARS_BEFORE), min(len(series), last + BARS_AFTER + 1)
    bars = series[start:stop_at]

    def local(index: int | None) -> int | None:
        return None if index is None or not start <= index < stop_at else index - start

    doubt = trade["uncertainty"]
    doubt_indexes = None
    if doubt is not None:
        a, b = instant(doubt["start"]), instant(doubt["end"])
        low = max((i for moment, i in labels.items() if a is not None and moment <= a), default=first)
        high = low if doubt["kind"] == SAME_BAR else labels.get(b, last) if b is not None else last
        doubt_indexes = (max(low, start) - start, min(high, stop_at - 1) - start)

    names = indicator_columns(run.data["strategy_id"], run.data["parameters_sha256"], list(signal_row))
    net = number(result["net_pnl"]) if result else None
    outcome = (UNKNOWN if unknown is not None else STILL_OPEN if result is None
               else WON if net > 0 else LOST if net < 0 else FLAT)  # type: ignore[operator]
    window_start, window_end = bars[0].timestamp, bars[-1].timestamp + HOUR
    rejected = [
        RejectedSignal(local(labels.get(at)), at, d["direction"], tuple(d["proposal"]["rejections"]), d["proposal"]["detail"])
        for d in run.decisions
        if d["proposal"]["status"] != "ACEPTADA" and instrument(d["symbol"]) == symbol
        for at in [instant(d["at"])] if at is not None and window_start <= at <= window_end
    ]
    view = TradeView(
        key=run.key, study_name=study.name, study_sha256=study.study_sha256, data_kind=p["data_kind"], trade=trade, trade_id=trade_id, symbol=symbol,
        direction=trade["direction"], status=trade["status"], outcome=outcome, exit_reason=result["reason"] if result else None, bars=bars,
        signal_index=local(labels.get(signal_close - HOUR)), entry_index=first - start, last_index=last - start, exit_index=local(exit_index),
        exit_timing=timing, signal_candle_close_at=signal_close, entry_at=entry_at, exit_at=exit_at, recorded_at=recorded_at,
        holes=_holes(bars, calendar_of(study)), indicators={label: _series(bars, rows, column) for column, label in names.items()},
        rsi=_series(bars, rows, "rsi"), atr=_series(bars, rows, "atr"), entry=entry, stop=stop, target=target,
        exit_price=number(result["exit_price"]) if result else None, stop_trigger_mid=stop + sign * half, target_trigger_mid=target + sign * half,
        stop_pips=pips(symbol, abs(entry - stop)), target_pips=pips(symbol, abs(target - entry)), reward_risk_gross=abs(target - entry) / abs(entry - stop),
        units=units, net_pnl=net, r_multiple=number(result["r_multiple"]) if result else None,
        conservative=unknown["conservative"] if unknown else None, optimistic=unknown["optimistic"] if unknown else None, uncertainty=doubt,
        uncertainty_indexes=doubt_indexes, rejected=rejected, signal_row=signal_row, sheet=(),
    )
    return _with_sheet(view, names, zone)


def _fmt(value: Any, suffix: str = "") -> str:
    return "—" if value is None or value == "" else f"{value}{suffix}"


def _px(value: Any, places: int = 6) -> str:
    """A price or indicator for reading: at most `places` decimals, no
    trailing zeros. The files keep every digit."""
    exact = number(value)
    if exact is None:
        return "—"
    rounded = exact.quantize(Decimal(1).scaleb(-places))
    if places >= 5:  # a price: five decimals (the tick) unless a sixth is needed
        return format(rounded.quantize(Decimal("0.00001")) if rounded == rounded.quantize(Decimal("0.00001")) else rounded, "f")
    return format(rounded.normalize(), "f")


def _percent(fraction: Decimal | None) -> str:
    return "—" if fraction is None else f"{(fraction * 100).quantize(Decimal('0.0001'))} %"


def _with_sheet(view: TradeView, names: Mapping[str, str], zone: str) -> TradeView:
    """The trade's technical sheet: the engine's own figures, each marked
    OBSERVADO (Yahoo's price and clock), ESTIMADO (the execution model and
    its money) or DERIVADO (arithmetic on those)."""
    t, p, s = view.trade, view.trade["proposal"], view.signal_row
    result, equity = t["result"], number(p["equity"])
    assert equity is not None
    bid, ask, loss = number(p["bid"]), number(p["ask"]), number(p["estimated_loss_at_stop"])
    assert bid is not None and ask is not None and loss is not None
    booked = number(t["booked_pnl"])
    hours = None if view.exit_at is None else (view.exit_at - view.entry_at) / HOUR
    f: list[Field] = []

    def add(section: str, label: str, value: str, kind: str) -> None:
        f.append(Field(section, label, value, kind))

    add("Identificación", "ID de operación", view.trade_id, OBSERVED)
    add("Identificación", "Estrategia y versión", t["strategy_id"], OBSERVED)
    add("Identificación", "Escenario / período", f"{view.key.scenario} / {view.key.period}", OBSERVED)
    add("Identificación", "Par y dirección", f"{view.symbol} {view.direction}", OBSERVED)
    add("Identificación", "Cierre de la vela de señal", show_time(view.signal_candle_close_at, zone), OBSERVED)
    add("Identificación", "Entrada (apertura de la vela siguiente)", show_time(view.entry_at, zone), ESTIMATED)
    add("Identificación", "Salida", show_time(view.exit_at, zone) if view.exit_at else "sin salida determinada", ESTIMATED)
    add("Identificación", "Duración", _fmt(None if hours is None else f"{hours:g}", " h") + f" · {t['bars_held']} velas observadas", DERIVED)
    add("Identificación", "Estado", f"{view.status} · {view.outcome}", ESTIMATED)

    for column, label in names.items():
        add("Indicadores en la señal", label, _px(s.get(column)), DERIVED)
    add("Indicadores en la señal", "RSI14", _px(s.get("rsi"), 2), DERIVED)
    add("Indicadores en la señal", "ATR14", f"{_px(s.get('atr'))} ({pips(view.symbol, number(s.get('atr')) or Decimal(0)).quantize(Decimal('0.1'))} pips)", DERIVED)
    add("Indicadores en la señal", "Condiciones que confirmaron la señal", _fmt(s.get("reasons")), DERIVED)
    add("Indicadores en la señal", "Cierre de la vela de señal (precio)", _px(s.get("last_close")), OBSERVED)

    add("Entrada y niveles", "Precio de referencia (apertura de Yahoo)", _px((bid + ask) / 2), OBSERVED)
    add("Entrada y niveles", "Bid / ask estimados", f"{_px(bid)} / {_px(ask)}", ESTIMATED)
    add("Entrada y niveles", "Spread asumido", f"{_px(p['spread'])} ({_px(pips(view.symbol, number(p['spread']) or Decimal(0)), 2)} pips)", ESTIMATED)
    add("Entrada y niveles", "Slippage asumido por ejecución", f"{_px(p['slippage'])} ({_px(pips(view.symbol, number(p['slippage']) or Decimal(0)), 2)} pips)", ESTIMATED)
    add("Entrada y niveles", "Entrada estimada", str(view.entry), ESTIMATED)
    add("Entrada y niveles", "Stop loss", f"{view.stop} · {view.stop_pips:f} pips · 1 R", ESTIMATED)
    add("Entrada y niveles", "Take profit", f"{view.target} · {view.target_pips:f} pips · {view.reward_risk_gross.quantize(Decimal('0.01'))} R bruto", ESTIMATED)
    add("Entrada y niveles", "Stop sobre el precio medio del gráfico", _px(view.stop_trigger_mid), DERIVED)
    add("Entrada y niveles", "Objetivo sobre el precio medio del gráfico", _px(view.target_trigger_mid), DERIVED)
    add("Entrada y niveles", "Relación neta planificada antes de financiación", _fmt(None if p["net_reward_risk_before_financing"] is None else
                                                                                   number(p["net_reward_risk_before_financing"]).quantize(Decimal("0.0001"))), ESTIMATED)  # type: ignore[union-attr]
    add("Entrada y niveles", "Precio de salida estimado", _fmt(view.exit_price), ESTIMATED)

    add("Tamaño y capital", "Capital antes de la operación", f"{equity} USD", ESTIMATED)
    add("Tamaño y capital", "Capital después (antes + resultado contabilizado)", "—" if booked is None else f"{equity + booked} USD", DERIVED)
    add("Tamaño y capital", "Unidades de moneda base", str(view.units), ESTIMATED)
    add("Tamaño y capital", "Lotes estándar (100 000 unidades)", f"{lots(view.units):f}", DERIVED)
    add("Tamaño y capital", "Valor nocional", f"{p['notional']} USD", ESTIMATED)
    add("Tamaño y capital", "Margen requerido", f"{p['margin_required']} USD", ESTIMATED)
    add("Tamaño y capital", "Riesgo presupuestado", f"{p['risk_budget']} USD", ESTIMATED)
    add("Tamaño y capital", "Pérdida estimada si se alcanza el stop", f"{loss} USD ({_percent(loss / equity)} del capital)", ESTIMATED)
    add("Tamaño y capital", "Ganancia estimada si se alcanza el objetivo", f"{p['expected_net_profit']} USD", ESTIMATED)

    add("Costos", "Spread (informativo, ya dentro de los precios)", f"{p['spread_cost']} USD", ESTIMATED)
    add("Costos", "Slippage de entrada (informativo)", f"{p['entry_slippage_cost']} USD", ESTIMATED)
    add("Costos", "Comisión", f"{result['commission_cost'] if result else p['commission_cost']} USD (supuesto, no verificada)", ESTIMATED)
    add("Costos", "Financiación", (f"{result['financing_cost']} USD · {result['nights']} noches · {p['financing_status']}" if result
                                    else f"{p['financing_status']}"), ESTIMATED)

    if result:
        net = number(result["net_pnl"])
        assert net is not None
        add("Resultado", "Resultado", f"{net} USD · {result['r_multiple']} R · {_percent(net / equity)} del capital", ESTIMATED)
        add("Resultado", "Motivo de salida", result["reason"] + (" con gap" if result["gap"] else ""), ESTIMATED)
        add("Resultado", "Momento de la salida", "dentro de la vela (instante desconocido)" if view.exit_timing == INSIDE_BAR else "en una apertura", DERIVED)
        add("Resultado", "Pérdida por encima de la estimada", f"{result['loss_beyond_estimate']} USD"
            + (" · superó el presupuesto" if result["exceeded_budget"] else ""), ESTIMATED)
    elif view.conservative and view.optimistic:
        doubt = view.uncertainty or {}
        add("Resultado", "Resultado", "INDETERMINADO: el orden de los hechos no es observable", ESTIMATED)
        add("Resultado", "Lectura conservadora (stop primero)", f"{view.conservative['net_pnl']} USD a {view.conservative['exit_price']}", ESTIMATED)
        add("Resultado", "Lectura optimista (objetivo primero)", f"{view.optimistic['net_pnl']} USD a {view.optimistic['exit_price']}", ESTIMATED)
        add("Resultado", "Contabilizado en la cuenta", f"{_fmt(booked)} USD", ESTIMATED)
        add("Resultado", "Tipo de incertidumbre", f"{doubt.get('kind')} · {len(doubt.get('unobserved_hours', []))} horas sin observar", OBSERVED)
        add("Resultado", "Intervalo sin certeza", f"{show_time(instant(doubt.get('start')), zone)} a {show_time(instant(doubt.get('end')), zone)}", OBSERVED)
    else:
        add("Resultado", "Resultado", "posición abierta al terminar el período: sin resultado", ESTIMATED)
        add("Resultado", "Valoración no realizada", f"{_fmt(t['unrealized_pnl'])} USD al precio {_fmt(t['valuation_price'])}"
            + (" (precio no vigente)" if t["valuation_stale"] else ""), ESTIMATED)
    add("Resultado", "Marcas del motor", " ".join(p["flags"]), ESTIMATED)
    return TradeView(**{**view.__dict__, "sheet": tuple(f)})


def table_row(view_or_trade: Mapping[str, Any], key: RunKey, zone: str = DEFAULT_ZONE) -> dict[str, Any]:
    """One trade of run.json as a row of the explorer and of the exported
    CSV. Times are shown in `zone`; the UTC originals are kept alongside."""
    t = view_or_trade
    p, result, unknown = t["proposal"], t["result"], t["indeterminate"]
    symbol = instrument(t["symbol"])
    entry, stop, target, equity = number(p["entry"]), number(p["stop"]), number(p["target"]), number(p["equity"])
    loss = number(p["estimated_loss_at_stop"])
    assert entry is not None and stop is not None and target is not None and equity is not None and loss is not None
    net = number(result["net_pnl"]) if result else None
    outcome = UNKNOWN if unknown else STILL_OPEN if not result else WON if net > 0 else LOST if net < 0 else FLAT  # type: ignore[operator]
    return {
        "ID": p["proposal_id"], "Estrategia": t["strategy_id"], "Escenario": key.scenario, "Período": key.period, "Par": symbol,
        "Dirección": t["direction"], "Estado": t["status"], "Resultado": outcome,
        "Entrada (hora)": show_time(instant(t["entry_at"]), zone), "Salida (hora)": show_time(instant(t["exit_at"]), zone) if t["exit_at"] else "—",
        "Entrada": entry, "Stop loss": stop, "Take profit": target, "Salida": number(result["exit_price"]) if result else None,
        "SL (pips)": pips(symbol, abs(entry - stop)), "TP (pips)": pips(symbol, abs(target - entry)),
        "Unidades": int(p["units"]), "Lotes": lots(int(p["units"])), "Riesgo (USD)": loss,
        "Riesgo (% capital)": (loss / equity * 100).quantize(Decimal("0.0001")), "Capital antes (USD)": equity,
        "Resultado (USD)": net, "Resultado (R)": number(result["r_multiple"]) if result else None,
        "Motivo de salida": (result["reason"] + (" (gap)" if result["gap"] else "")) if result else ("INDETERMINADA" if unknown else "—"),
        "Conservador (USD)": number(unknown["conservative"]["net_pnl"]) if unknown else None,
        "Optimista (USD)": number(unknown["optimistic"]["net_pnl"]) if unknown else None,
        "Entrada (UTC)": t["entry_at"], "Salida (UTC)": t["exit_at"] or "", "Fuente": p["data_kind"],
    }


# --- A and B side by side ---


@dataclass(frozen=True)
class StrategyActivity:
    key: RunKey
    trades: Sequence[Mapping[str, Any]]  # trades of the pair whose life overlaps the interval
    accepted: Sequence[Mapping[str, Any]]  # decisions inside the interval ...
    rejected: Sequence[Mapping[str, Any]]  # ... by what the engine made of them


@dataclass(frozen=True)
class IntervalView:
    symbol: str
    start: datetime
    end: datetime
    bars: Sequence[PriceBar]
    activity: Sequence[StrategyActivity]
    verdict: str
    same_conditions: bool  # the runs share period and scenario
    notes: Sequence[str]


def compare_interval(study: Study, runs: Sequence[Run], symbol: str, start: datetime, end: datetime) -> IntervalView:
    """What each run did on `symbol` between `start` and `end`. Trades are
    never presented as equivalent: every difference in instant, size or
    conditions is written out in `notes`."""
    symbol = instrument(symbol)
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if end <= start:
        raise StudyError("el intervalo debe terminar después de empezar")
    bars = [bar for bar in study.bars.get(symbol, ()) if start <= bar.timestamp < end]
    activity = []
    for run in runs:
        trades = [t for t in run.trades if instrument(t["symbol"]) == symbol and instant(t["entry_at"]) < end  # type: ignore[operator]
                  and (instant(t["exit_at"] or t["recorded_at"]) or end) >= start]
        inside = [d for d in run.decisions if instrument(d["symbol"]) == symbol and start <= instant(d["at"]) < end]  # type: ignore[operator]
        activity.append(StrategyActivity(run.key, trades, [d for d in inside if d["proposal"]["status"] == "ACEPTADA"],
                                         [d for d in inside if d["proposal"]["status"] != "ACEPTADA"]))
    traded = [a.key.strategy_id for a in activity if a.trades]
    verdict = ("NINGUNA_OPERO" if not traded else "TODAS_OPERARON" if len(traded) == len(activity) else "SOLO_OPERO: " + ", ".join(traded))
    same = len({(a.key.period, a.key.scenario) for a in activity}) <= 1
    notes = []
    if not same:
        notes.append("Las corridas comparadas NO comparten período y escenario: " + "; ".join(a.key.folder for a in activity))
    for a in activity:
        for d in a.rejected:
            notes.append(f"{a.key.strategy_id}: señal {d['direction']} rechazada en {d['at']} por {' '.join(d['proposal']['rejections'])}")
    entries = {a.key.strategy_id: sorted({t["entry_at"] for t in a.trades}) for a in activity if a.trades}
    if len(entries) > 1:
        moments = list(entries.values())
        notes.append("Las entradas coinciden en el instante." if all(m == moments[0] for m in moments)
                     else "Las entradas ocurrieron en momentos distintos: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in entries.items()))
        sizes = {k: sorted({t["proposal"]["units"] for t in a.trades}) for a in activity if a.trades for k in [a.key.strategy_id]}
        if len({tuple(v) for v in sizes.values()}) > 1:
            notes.append("Los tamaños difieren (cada cuenta dimensiona con su propio capital): " + "; ".join(f"{k}: {v}" for k, v in sizes.items()))
    return IntervalView(symbol, start, end, tuple(bars), tuple(activity), verdict, same, tuple(notes))
