"""Signals of the experimental strategy ema-pullback-forex v0.1.0 ("C"):
enter when a pullback ends inside an established trend, with the stop
behind the pullback. docs/specs/spec-007-ema-pullback-forex.md.

EXPERIMENTAL, NOT VALIDATED. Every rule and number here is a hypothesis
fixed before any result was seen; none has been shown to work. Independent
of ema_cross.py (A) and ema_trend_cross.py (B), which are frozen: nothing
of theirs is changed. No order, no broker, no backtest loop.

The data gates are those of signals.closed_window (B's window: 720 expected
hours, at most 7 missing, none among the last 50). All averages and the ATR
are computed ONCE, on the window that ends at the candle being evaluated;
the values of earlier candles are read from those same series.

THE RULE, for the candle c that just closed (LONG; SHORT is the mirror):
  1 trend          EMA20_c > EMA50_c > EMA120_c            (strict)
  2 leg            close_(c-1) <= EMA9_(c-1). The leg is the longest run of
                   consecutive candles c-n .. c-1 that each closed at or
                   below their own EMA9; c is the first close back above.
  3 confirmation   close_c > EMA9_c, close_c > EMA20_c and close_c > open_c
  4 short leg      n <= 12
  5 touch          some candle of the leg has low <= its EMA20
  6 trend intact   at every candle of the leg EMA20 > EMA50 > EMA120, and
                   none closed below its EMA50
  7 observed       c-n .. c are consecutive clock hours, each with a bar:
                   no data hole and no market closure inside
Equalities: a low exactly at EMA20 is a touch; a close exactly at EMA9
belongs to the leg and confirms nothing; equal averages are not a trend.

Outcomes (the enum is shared with A and B, so its names are theirs):
  LONG / SHORT              every condition holds
  SIN_CRUCE                 nothing to confirm: no trend, no leg before c,
                            or the leg is still running
  CRUCE_SIN_CONFIRMACION    c closed back beyond EMA9 after a leg, but a
                            condition failed; every failed one is reported
  DATOS_RECHAZADOS          the window failed a data gate

The rule is a pure function of the window: no state is carried between
hours, so the same candle always evaluates the same, and nothing after c is
read.

THE STOP is structural and travels with the signal (StructuralStop):
  reference   lowest low of c-n .. c, less 0.10 x ATR14_c   (LONG)
              highest high, plus 0.10 x ATR14_c             (SHORT)
  floor       at least 1.0 x ATR14_c from the entry
  cap         beyond 3.0 x ATR14_c from the entry there is no trade
The entry is not known here (it is the next bar's open), so the floor and
the cap are applied by trade_proposal.build_proposal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Sequence

from investment_committee.domain import PriceBar
from investment_committee.market_intelligence.quality import Calendar
from investment_committee.market_intelligence.quotes import QuoteKind

from .sessions import is_week_open
from .signals import Outcome, StructuralStop, closed_window
from .strategies import parameters_fingerprint
from .wilder import ema_series, wilder_atr

STRATEGY_NAME = "ema-pullback-forex"
STRATEGY_VERSION = "0.1.0"
STRATEGY_ID = f"{STRATEGY_NAME}@{STRATEGY_VERSION}"


@dataclass(frozen=True)
class EmaPullbackParameters:
    """Hypotheses fixed a priori, not tuned on any data. Any change is a new
    version."""

    timeframe: timedelta = timedelta(hours=1)
    ema_fast: int = 9
    ema_trend_short: int = 20
    ema_trend_mid: int = 50
    ema_trend_long: int = 120
    atr_period: int = 14
    window_bars: int = 720
    max_missing_in_window: int = 7
    contiguous_tail: int = 50
    max_leg_bars: int = 12
    stop_buffer_atr: Decimal = Decimal("0.10")
    stop_floor_atr: Decimal = Decimal("1.0")
    stop_cap_atr: Decimal = Decimal("3.0")
    net_reward_risk: Decimal = Decimal("2")  # the target: at least this many R net of the estimated costs

    def __post_init__(self) -> None:
        if not (0 < self.ema_fast < self.ema_trend_short < self.ema_trend_mid < self.ema_trend_long):
            raise ValueError("the averages must be positive and in increasing length")
        if self.window_bars < max(self.ema_trend_long, self.atr_period) + 2:
            raise ValueError("window_bars is too short for the indicators")
        if not (self.max_leg_bars + 1 <= self.contiguous_tail <= self.window_bars):
            raise ValueError("contiguous_tail must cover the longest leg and fit the window")
        if self.max_leg_bars < 1 or self.max_missing_in_window < 0:
            raise ValueError("max_leg_bars must be at least 1 and max_missing_in_window not negative")
        if not (Decimal(0) <= self.stop_buffer_atr and Decimal(0) < self.stop_floor_atr <= self.stop_cap_atr) or self.net_reward_risk <= 0:
            raise ValueError("stop distances and the reward must be positive, with floor <= cap")


def fingerprint(parameters: EmaPullbackParameters | None = None) -> str:
    """SHA-256 of the parameters: what freezes a version."""
    return parameters_fingerprint(parameters or EmaPullbackParameters())


class Reason(str, Enum):
    MARKET_CLOSED = "MERCADO_CERRADO"
    INCONSISTENT_DATA = "DATOS_INCONSISTENTES"
    INSUFFICIENT_DATA = "DATOS_INSUFICIENTES"
    STALE_DATA = "DATOS_DESACTUALIZADOS"
    INCOMPLETE_DATA = "DATOS_INCOMPLETOS"
    NO_TREND = "SIN_TENDENCIA"
    UPTREND = "TENDENCIA_ALCISTA"
    DOWNTREND = "TENDENCIA_BAJISTA"
    NO_LEG = "SIN_RETROCESO"  # the previous candle did not close on the pullback side of EMA9
    LEG_RUNNING = "RETROCESO_EN_CURSO"  # c itself still closed on the pullback side
    CONFIRMED = "CONFIRMACION"
    NOT_BEYOND_TREND_AVERAGE = "CIERRE_SIN_SUPERAR_EMA20"
    NOT_DIRECTIONAL_CANDLE = "VELA_DE_CONFIRMACION_CONTRARIA"  # LONG needs close > open; SHORT close < open
    LEG_TOO_LONG = "RETROCESO_DEMASIADO_LARGO"
    NO_TOUCH = "RETROCESO_SIN_TOCAR_EMA20"
    TREND_BROKEN = "TENDENCIA_ROTA_EN_EL_RETROCESO"
    CLOSED_BEYOND_MID = "CIERRE_MAS_ALLA_DE_EMA50"
    LEG_NOT_OBSERVED = "RETROCESO_CON_HUECO_O_CIERRE_DE_MERCADO"
    VALID_PULLBACK = "RETROCESO_VALIDO"


@dataclass(frozen=True)
class PullbackEvaluation:
    """Meets signals.TradeSignal, the contract of the proposal engine."""

    strategy_id: str
    parameters_sha256: str
    symbol: str
    as_of: datetime
    candle_open: datetime  # the candle evaluated: its label ...
    candle_close: datetime  # ... and when it closed
    outcome: Outcome
    reasons: Sequence[Reason]
    detail: str
    data_kind: QuoteKind
    data_source: str
    bars_received: int
    forming_bars_excluded: int
    bars_used: int = 0
    window_start: datetime | None = None
    missing_in_window: int = 0
    market_closures_in_window: int = 0
    last_close: Decimal | None = None
    last_open: Decimal | None = None
    ema_fast: Decimal | None = None  # EMA9
    ema_trend_short: Decimal | None = None  # EMA20
    ema_trend_mid: Decimal | None = None  # EMA50
    ema_trend_long: Decimal | None = None  # EMA120
    atr: Decimal | None = None
    leg_bars: int | None = None  # n: candles of the leg, before c
    leg_start: datetime | None = None  # label of the leg's first candle
    pullback_extreme: Decimal | None = None  # lowest low (LONG) or highest high (SHORT) of the leg and c
    structural_stop: StructuralStop | None = None  # only with a signal

    @property
    def evaluation_key(self) -> str:
        return f"{self.strategy_id}|{self.symbol}|{self.candle_close.isoformat()}"

    @property
    def is_signal(self) -> bool:
        return self.outcome in (Outcome.LONG, Outcome.SHORT)

    @property
    def signal_id(self) -> str | None:
        return self.evaluation_key if self.is_signal else None


def evaluate(
    bars: Sequence[PriceBar],
    as_of: datetime,
    *,
    symbol: str,
    data_kind: QuoteKind,
    data_source: str,
    parameters: EmaPullbackParameters | None = None,
    is_open: Calendar = is_week_open,
) -> PullbackEvaluation:
    """Evaluate the candle that closed at the last full hour at or before
    `as_of`, from `bars` (mid prices, any order, any UTC offset)."""
    p = parameters or EmaPullbackParameters()
    data = closed_window(bars, as_of, timeframe=p.timeframe, window_bars=p.window_bars, max_missing=p.max_missing_in_window,
                         contiguous_tail=p.contiguous_tail, is_open=is_open)

    def result(outcome: Outcome, reasons: Sequence[Reason], detail: str = "", **values: object) -> PullbackEvaluation:
        return PullbackEvaluation(
            strategy_id=STRATEGY_ID, parameters_sha256=fingerprint(p), symbol=symbol, as_of=data.as_of, candle_open=data.candle_open,
            candle_close=data.candle_close, outcome=outcome, reasons=tuple(reasons), detail=detail, data_kind=data_kind, data_source=data_source,
            bars_received=data.bars_received, forming_bars_excluded=data.forming_bars_excluded, **values,  # type: ignore[arg-type]
        )

    if data.rejection is not None:
        return result(Outcome.DATA_REJECTED, [Reason(data.rejection)], data.detail)

    window = data.bars
    closes = [bar.close for bar in window]
    fast, short, mid, long_ = (ema_series(closes, n) for n in (p.ema_fast, p.ema_trend_short, p.ema_trend_mid, p.ema_trend_long))
    atr = wilder_atr(window, p.atr_period)
    c = len(window) - 1
    assert atr is not None and all(series[c - p.max_leg_bars - 1] is not None for series in (fast, short, mid, long_))
    seen = dict(bars_used=len(window), window_start=data.window_start, missing_in_window=data.missing, market_closures_in_window=data.market_closures,
                last_close=closes[c], last_open=window[c].open, ema_fast=fast[c], ema_trend_short=short[c], ema_trend_mid=mid[c],
                ema_trend_long=long_[c], atr=atr)

    def trend(i: int, sign: int) -> bool:
        return (short[i] - mid[i]) * sign > 0 and (mid[i] - long_[i]) * sign > 0  # type: ignore[operator]

    if trend(c, 1):
        sign, side, direction = 1, Outcome.LONG, Reason.UPTREND
    elif trend(c, -1):
        sign, side, direction = -1, Outcome.SHORT, Reason.DOWNTREND
    else:
        return result(Outcome.NO_CROSS, [Reason.NO_TREND], **seen)

    def retraced(i: int) -> bool:
        """The candle closed on the pullback side of its EMA9, or exactly on it."""
        return (closes[i] - fast[i]) * sign <= 0  # type: ignore[operator]

    if not retraced(c - 1):
        return result(Outcome.NO_CROSS, [direction, Reason.NO_LEG], **seen)
    if retraced(c):
        return result(Outcome.NO_CROSS, [direction, Reason.LEG_RUNNING], **seen)

    n = 1
    while n <= p.max_leg_bars and c - n - 1 >= 0 and retraced(c - n - 1):
        n += 1  # stops at max_leg_bars + 1: enough to know the leg is too long
    leg = range(c - n, c)
    extreme = min(window[i].low for i in range(c - n, c + 1)) if sign > 0 else max(window[i].high for i in range(c - n, c + 1))
    seen.update(leg_bars=n, leg_start=window[c - n].timestamp, pullback_extreme=extreme)

    failed: list[Reason] = []
    if (closes[c] - short[c]) * sign <= 0:  # type: ignore[operator]
        failed.append(Reason.NOT_BEYOND_TREND_AVERAGE)
    if (closes[c] - window[c].open) * sign <= 0:
        failed.append(Reason.NOT_DIRECTIONAL_CANDLE)
    if n > p.max_leg_bars:
        failed.append(Reason.LEG_TOO_LONG)
    if not any(((window[i].low if sign > 0 else window[i].high) - short[i]) * sign <= 0 for i in leg):  # type: ignore[operator]
        failed.append(Reason.NO_TOUCH)
    if not all(trend(i, sign) for i in leg):
        failed.append(Reason.TREND_BROKEN)
    if any((closes[i] - mid[i]) * sign < 0 for i in leg):  # type: ignore[operator]
        failed.append(Reason.CLOSED_BEYOND_MID)
    if any(window[i + 1].timestamp - window[i].timestamp != p.timeframe for i in leg):
        failed.append(Reason.LEG_NOT_OBSERVED)
    if failed:
        return result(Outcome.NOT_CONFIRMED, [direction, *failed], **seen)

    stop = StructuralStop(reference=extreme - sign * p.stop_buffer_atr * atr, floor_atr=p.stop_floor_atr, cap_atr=p.stop_cap_atr)
    return result(side, [direction, Reason.VALID_PULLBACK, Reason.CONFIRMED], structural_stop=stop, **seen)
