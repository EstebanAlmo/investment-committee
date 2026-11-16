"""Signal detection of the experimental strategy ema-rsi-atr-forex v0.1.0.

EXPERIMENTAL and NOT VALIDATED. This module only says whether the candle
that just closed produced a signal. It computes no entry, stop, target or
size, opens nothing, and is not registered among the frozen strategies
(simulation/strategies.py), whose protocol it must not disturb.

evaluate(bars, as_of, ...) looks at ONE candle: the 1-hour candle that
closed at the last full hour at or before `as_of` (so 14:00 and 14:37 look
at the same candle, the one labelled 13:00). Steps, in order; the first that
fails ends the evaluation with DATOS_RECHAZADOS and its reason (the gates
themselves live in simulation/signals.py, shared with the other
experimental strategy; this module only chooses the window and tolerances):

  1. MERCADO_CERRADO        that candle's hour is not inside the calendar.
  2. Only bars fully closed at `as_of` are used (label + 1h <= as_of); a
     forming bar is dropped and counted.
  3. DATOS_INCONSISTENTES   quality.audit_bars finds, from the start of the
     window on, two different bars for one instant, a label off the hourly
     grid, or a non-positive price. Bars outside the calendar are dropped
     (counted); exact duplicates collapse.
  4. Continuity, BEFORE any indicator. The window is the last
     `window_bars` (300) hours the calendar EXPECTS, ending at that candle -
     not "the last 300 bars available". Two expected hours separated by a
     closed market (a weekend) are neighbours: that is a normal closure,
     counted in `market_closures_in_window`. An expected hour with no bar
     is an anomalous gap, counted in `missing_in_window`; the window never
     reaches further back to replace it.
       DATOS_INSUFICIENTES    the history does not reach the window's start;
       DATOS_DESACTUALIZADOS  the candle itself has no bar;
       DATOS_INCOMPLETOS      more than `max_missing_in_window` (3) hours
                              missing, or any among the last
                              `contiguous_tail` (50).
     So the candle and the one before it are always consecutive market
     hours: a cross is never read across a hole. Up to 3 older holes are
     tolerated (the approved rule); the averages then run over 297-299
     bars, a hole being passed over, not filled.

Then, on the closes of the window's bars:
  cross up    EMA20 <= EMA50 on the previous bar and EMA20 > EMA50 on this one
  cross down  EMA20 >= EMA50 on the previous bar and EMA20 < EMA50 on this one
  LONG   = cross up   and RSI14 > 50
  SHORT  = cross down and RSI14 < 50
  CRUCE_SIN_CONFIRMACION = a cross whose RSI does not confirm (RSI = 50
  confirms neither side). It is not confirmed later by another candle.
  SIN_CRUCE otherwise - two averages that touch or come close are not a cross.
ATR14 is reported with every evaluation; this module does not use it.

Both EMAs of the "previous bar" come from the same 300-bar window as the
current ones. The window makes an evaluation reproducible from 300 bars,
whatever else the provider returned; its effect on the values is
documented in docs/ema-rsi-atr-signals.md.

One signal per strategy, instrument and candle: `signal_id` is built from
exactly those three, so evaluating the same candle again - a minute later,
after a restart, with more history - yields the same id. The store that
will record signals (a later delivery) must key on it.

The prices' provenance (QuoteKind and source, market_intelligence.quotes)
is required and travels with the evaluation: a signal computed on
estimated or synthetic prices says so.
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
from .signals import Outcome, closed_window
from .strategies import parameters_fingerprint
from .wilder import ema_series, wilder_atr, wilder_rsi

STRATEGY_NAME = "ema-rsi-atr-forex"
STRATEGY_VERSION = "0.1.0"
STRATEGY_ID = f"{STRATEGY_NAME}@{STRATEGY_VERSION}"


@dataclass(frozen=True)
class EmaCrossParameters:
    """Hypotheses fixed a priori, not tuned on any data. Any change is a new
    version."""

    timeframe: timedelta = timedelta(hours=1)
    ema_fast: int = 20
    ema_slow: int = 50
    rsi_period: int = 14
    atr_period: int = 14
    rsi_threshold: Decimal = Decimal("50")
    window_bars: int = 300
    max_missing_in_window: int = 3
    contiguous_tail: int = 50

    def __post_init__(self) -> None:
        if not (0 < self.ema_fast < self.ema_slow):
            raise ValueError("ema_fast must be positive and shorter than ema_slow")
        if self.window_bars < max(self.ema_slow, self.rsi_period, self.atr_period) + 2:
            raise ValueError("window_bars is too short for the indicators")
        if not (0 < self.contiguous_tail <= self.window_bars):
            raise ValueError("contiguous_tail must be within the window")


def fingerprint(parameters: EmaCrossParameters | None = None) -> str:
    """SHA-256 of the parameters: what freezes a version."""
    return parameters_fingerprint(parameters or EmaCrossParameters())


class Reason(str, Enum):
    MARKET_CLOSED = "MERCADO_CERRADO"
    INCONSISTENT_DATA = "DATOS_INCONSISTENTES"
    INSUFFICIENT_DATA = "DATOS_INSUFICIENTES"
    STALE_DATA = "DATOS_DESACTUALIZADOS"
    INCOMPLETE_DATA = "DATOS_INCOMPLETOS"
    CROSS_UP = "CRUCE_ALCISTA"
    CROSS_DOWN = "CRUCE_BAJISTA"
    NO_CROSS = "SIN_CRUCE"
    RSI_CONFIRMS = "RSI_CONFIRMA"
    RSI_DOES_NOT_CONFIRM = "RSI_NO_CONFIRMA"


@dataclass(frozen=True)
class SignalEvaluation:
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
    bars_used: int = 0  # bars the indicators were computed on (window_bars minus tolerated holes), else 0
    window_start: datetime | None = None  # first expected hour of the window
    missing_in_window: int = 0  # anomalous gaps: expected hours without a bar
    market_closures_in_window: int = 0  # normal closures (e.g. weekends) between neighbouring hours
    last_close: Decimal | None = None
    ema_fast: Decimal | None = None
    ema_slow: Decimal | None = None
    previous_ema_fast: Decimal | None = None
    previous_ema_slow: Decimal | None = None
    rsi: Decimal | None = None
    atr: Decimal | None = None

    @property
    def evaluation_key(self) -> str:
        """Strategy, instrument and candle: the same for every evaluation of
        that candle."""
        return f"{self.strategy_id}|{self.symbol}|{self.candle_close.isoformat()}"

    @property
    def is_signal(self) -> bool:
        return self.outcome in (Outcome.LONG, Outcome.SHORT)

    @property
    def signal_id(self) -> str | None:
        return self.evaluation_key if self.is_signal else None


def classify_cross(
    previous_fast: Decimal, previous_slow: Decimal, fast: Decimal, slow: Decimal, rsi: Decimal, threshold: Decimal = Decimal("50")
) -> tuple[Outcome, tuple[Reason, ...]]:
    """The rule itself, on five numbers."""
    if previous_fast <= previous_slow and fast > slow:
        confirmed = rsi > threshold
        return (Outcome.LONG if confirmed else Outcome.NOT_CONFIRMED,
                (Reason.CROSS_UP, Reason.RSI_CONFIRMS if confirmed else Reason.RSI_DOES_NOT_CONFIRM))
    if previous_fast >= previous_slow and fast < slow:
        confirmed = rsi < threshold
        return (Outcome.SHORT if confirmed else Outcome.NOT_CONFIRMED,
                (Reason.CROSS_DOWN, Reason.RSI_CONFIRMS if confirmed else Reason.RSI_DOES_NOT_CONFIRM))
    return Outcome.NO_CROSS, (Reason.NO_CROSS,)


def evaluate(
    bars: Sequence[PriceBar],
    as_of: datetime,
    *,
    symbol: str,
    data_kind: QuoteKind,
    data_source: str,
    parameters: EmaCrossParameters | None = None,
    is_open: Calendar = is_week_open,
) -> SignalEvaluation:
    """Evaluate the candle that closed at the last full hour at or before
    `as_of`, from `bars` (mid prices, any order, any UTC offset)."""
    p = parameters or EmaCrossParameters()
    data = closed_window(bars, as_of, timeframe=p.timeframe, window_bars=p.window_bars, max_missing=p.max_missing_in_window,
                         contiguous_tail=p.contiguous_tail, is_open=is_open)

    def result(outcome: Outcome, reasons: Sequence[Reason], detail: str, **values: object) -> SignalEvaluation:
        return SignalEvaluation(
            strategy_id=STRATEGY_ID, parameters_sha256=fingerprint(p), symbol=symbol, as_of=data.as_of, candle_open=data.candle_open,
            candle_close=data.candle_close, outcome=outcome, reasons=tuple(reasons), detail=detail, data_kind=data_kind, data_source=data_source,
            bars_received=data.bars_received, forming_bars_excluded=data.forming_bars_excluded, **values,  # type: ignore[arg-type]
        )

    if data.rejection is not None:
        return result(Outcome.DATA_REJECTED, [Reason(data.rejection)], data.detail)

    window = data.bars
    closes = [bar.close for bar in window]
    fast, slow = ema_series(closes, p.ema_fast), ema_series(closes, p.ema_slow)
    rsi, atr = wilder_rsi(closes, p.rsi_period), wilder_atr(window, p.atr_period)
    assert fast[-1] is not None and fast[-2] is not None and slow[-1] is not None and slow[-2] is not None and rsi is not None and atr is not None
    outcome, reasons = classify_cross(fast[-2], slow[-2], fast[-1], slow[-1], rsi, p.rsi_threshold)
    return result(
        outcome, reasons, "", bars_used=len(window), window_start=data.window_start, missing_in_window=data.missing,
        market_closures_in_window=data.market_closures, last_close=closes[-1],
        ema_fast=fast[-1], ema_slow=slow[-1], previous_ema_fast=fast[-2], previous_ema_slow=slow[-2], rsi=rsi, atr=atr,
    )
