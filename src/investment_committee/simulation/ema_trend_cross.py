"""Signal detection of the experimental strategy ema-3-9-20-50-120-forex v0.1.0.

EXPERIMENTAL and NOT VALIDATED. A second, independent hypothesis next to
ema-rsi-atr-forex (simulation/ema_cross.py), which it neither imports nor
changes: a fast cross taken only in the direction of an established trend.
This module only says whether the candle that just closed produced a
signal. It computes no entry, stop, target or size, opens nothing, and is
not registered among the frozen strategies (simulation/strategies.py).

evaluate(bars, as_of, ...) looks at ONE candle: the 1-hour candle that
closed at the last full hour at or before `as_of`. The data gates come
first and are those of simulation/signals.py (closed market, forming bars,
inconsistent bars, continuity on the hours the calendar expects), with this
strategy's own window and tolerances:

  window                720 hours the calendar expects (not "720 bars available")
  missing hours         at most 7 in the window, none among the last 50
  a failed gate         DATOS_RECHAZADOS with its reason; no indicator is computed

Then, on the closes of the window's bars:

  cross up    EMA3 <= EMA9 on the previous bar and EMA3 > EMA9 on this one
  cross down  EMA3 >= EMA9 on the previous bar and EMA3 < EMA9 on this one
  trend       EMA20 > EMA50 > EMA120 on this bar (for a cross up)
              EMA20 < EMA50 < EMA120 on this bar (for a cross down)
              strict: two averages that are equal are not in order
  RSI         RSI14 > 50 (cross up), RSI14 < 50 (cross down); 50 confirms neither

  LONG   = cross up   and trend in order and RSI confirms
  SHORT  = cross down and trend in order and RSI confirms
  CRUCE_SIN_CONFIRMACION = a cross that fails the trend, the RSI or both.
      The reasons name each filter separately (TENDENCIA_ALINEADA or
      TENDENCIA_NO_ALINEADA, RSI_CONFIRMA or RSI_NO_CONFIRMA), so that what
      each one rejects can be counted. It is not confirmed later by another
      candle.
  SIN_CRUCE otherwise - averages that touch or come close are not a cross.

No tolerance is applied to a cross, however small: a minimum separation
would be one more parameter. Decimal arithmetic gives the same comparison
on every machine. ATR14 is reported with every evaluation; this module does
not use it.

The window and the seed. Every average starts from the simple mean of its
first n closes in the window, so a value depends on where the window
starts. The weight the seed keeps at the last bar is (1 - 2/(n+1))^(720-n):
about 4.5e-5 for EMA120 - what EMA50 keeps in the 300-hour window of the
other strategy - 2e-12 for EMA50, and nothing measurable for EMA20, EMA9
and EMA3. In a 300-hour window EMA120 would keep about 5 %, which is why
this strategy needs 720 (about six weeks of market). The crossing pair,
EMA3 and EMA9, does not depend on the window at all. The averages of the
previous bar are read from the same window as the current ones.

One signal per strategy, instrument and candle: `signal_id` is built from
exactly those three, so evaluating the same candle again - a minute later,
after a restart, with more history - yields the same id, and it can never
equal an id of the other strategy.

The prices' provenance (QuoteKind and source) is required and travels with
the evaluation.
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

STRATEGY_NAME = "ema-3-9-20-50-120-forex"
STRATEGY_VERSION = "0.1.0"
STRATEGY_ID = f"{STRATEGY_NAME}@{STRATEGY_VERSION}"


@dataclass(frozen=True)
class EmaTrendCrossParameters:
    """Hypotheses fixed a priori, not tuned on any data. Any change is a new
    version."""

    timeframe: timedelta = timedelta(hours=1)
    ema_cross_fast: int = 3
    ema_cross_slow: int = 9
    ema_trend_short: int = 20
    ema_trend_mid: int = 50
    ema_trend_long: int = 120
    rsi_period: int = 14
    atr_period: int = 14
    rsi_threshold: Decimal = Decimal("50")
    window_bars: int = 720
    max_missing_in_window: int = 7
    contiguous_tail: int = 50

    def __post_init__(self) -> None:
        if not (0 < self.ema_cross_fast < self.ema_cross_slow):
            raise ValueError("ema_cross_fast must be positive and shorter than ema_cross_slow")
        if not (0 < self.ema_trend_short < self.ema_trend_mid < self.ema_trend_long):
            raise ValueError("the trend averages must be positive and in increasing length")
        if self.window_bars < max(self.ema_cross_slow, self.ema_trend_long, self.rsi_period, self.atr_period) + 2:
            raise ValueError("window_bars is too short for the indicators")
        if not (0 < self.contiguous_tail <= self.window_bars):
            raise ValueError("contiguous_tail must be within the window")
        if self.max_missing_in_window < 0:
            raise ValueError("max_missing_in_window must not be negative")


def fingerprint(parameters: EmaTrendCrossParameters | None = None) -> str:
    """SHA-256 of the parameters: what freezes a version."""
    return parameters_fingerprint(parameters or EmaTrendCrossParameters())


class Reason(str, Enum):
    MARKET_CLOSED = "MERCADO_CERRADO"
    INCONSISTENT_DATA = "DATOS_INCONSISTENTES"
    INSUFFICIENT_DATA = "DATOS_INSUFICIENTES"
    STALE_DATA = "DATOS_DESACTUALIZADOS"
    INCOMPLETE_DATA = "DATOS_INCOMPLETOS"
    CROSS_UP = "CRUCE_ALCISTA"
    CROSS_DOWN = "CRUCE_BAJISTA"
    NO_CROSS = "SIN_CRUCE"
    TREND_ALIGNED = "TENDENCIA_ALINEADA"
    TREND_NOT_ALIGNED = "TENDENCIA_NO_ALINEADA"
    RSI_CONFIRMS = "RSI_CONFIRMA"
    RSI_DOES_NOT_CONFIRM = "RSI_NO_CONFIRMA"


@dataclass(frozen=True)
class TrendCrossEvaluation:
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
    bars_used: int = 0  # bars the indicators were computed on (window_bars minus tolerated holes), else 0
    window_start: datetime | None = None  # first expected hour of the window
    missing_in_window: int = 0  # anomalous gaps: expected hours without a bar
    market_closures_in_window: int = 0  # normal closures (e.g. weekends) between neighbouring hours
    last_close: Decimal | None = None
    ema_cross_fast: Decimal | None = None  # EMA3
    ema_cross_slow: Decimal | None = None  # EMA9
    previous_ema_cross_fast: Decimal | None = None
    previous_ema_cross_slow: Decimal | None = None
    ema_trend_short: Decimal | None = None  # EMA20
    ema_trend_mid: Decimal | None = None  # EMA50
    ema_trend_long: Decimal | None = None  # EMA120
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


def classify(
    previous_fast: Decimal, previous_slow: Decimal, fast: Decimal, slow: Decimal, trend_short: Decimal, trend_mid: Decimal, trend_long: Decimal,
    rsi: Decimal, threshold: Decimal = Decimal("50"),
) -> tuple[Outcome, tuple[Reason, ...]]:
    """The rule itself, on eight numbers. A cross always reports both
    filters, whether or not the other one failed."""
    if previous_fast <= previous_slow and fast > slow:
        side, cross = Outcome.LONG, Reason.CROSS_UP
        aligned, confirmed = trend_short > trend_mid > trend_long, rsi > threshold
    elif previous_fast >= previous_slow and fast < slow:
        side, cross = Outcome.SHORT, Reason.CROSS_DOWN
        aligned, confirmed = trend_short < trend_mid < trend_long, rsi < threshold
    else:
        return Outcome.NO_CROSS, (Reason.NO_CROSS,)
    reasons = (cross, Reason.TREND_ALIGNED if aligned else Reason.TREND_NOT_ALIGNED, Reason.RSI_CONFIRMS if confirmed else Reason.RSI_DOES_NOT_CONFIRM)
    return (side if aligned and confirmed else Outcome.NOT_CONFIRMED), reasons


def evaluate(
    bars: Sequence[PriceBar],
    as_of: datetime,
    *,
    symbol: str,
    data_kind: QuoteKind,
    data_source: str,
    parameters: EmaTrendCrossParameters | None = None,
    is_open: Calendar = is_week_open,
) -> TrendCrossEvaluation:
    """Evaluate the candle that closed at the last full hour at or before
    `as_of`, from `bars` (mid prices, any order, any UTC offset)."""
    p = parameters or EmaTrendCrossParameters()
    data = closed_window(bars, as_of, timeframe=p.timeframe, window_bars=p.window_bars, max_missing=p.max_missing_in_window,
                         contiguous_tail=p.contiguous_tail, is_open=is_open)

    def result(outcome: Outcome, reasons: Sequence[Reason], detail: str, **values: object) -> TrendCrossEvaluation:
        return TrendCrossEvaluation(
            strategy_id=STRATEGY_ID, parameters_sha256=fingerprint(p), symbol=symbol, as_of=data.as_of, candle_open=data.candle_open,
            candle_close=data.candle_close, outcome=outcome, reasons=tuple(reasons), detail=detail, data_kind=data_kind, data_source=data_source,
            bars_received=data.bars_received, forming_bars_excluded=data.forming_bars_excluded, **values,  # type: ignore[arg-type]
        )

    if data.rejection is not None:
        return result(Outcome.DATA_REJECTED, [Reason(data.rejection)], data.detail)

    window = data.bars
    closes = [bar.close for bar in window]
    fast, slow = ema_series(closes, p.ema_cross_fast), ema_series(closes, p.ema_cross_slow)
    short, mid, long_ = (ema_series(closes, n)[-1] for n in (p.ema_trend_short, p.ema_trend_mid, p.ema_trend_long))
    rsi, atr = wilder_rsi(closes, p.rsi_period), wilder_atr(window, p.atr_period)
    assert fast[-1] is not None and fast[-2] is not None and slow[-1] is not None and slow[-2] is not None
    assert short is not None and mid is not None and long_ is not None and rsi is not None and atr is not None
    outcome, reasons = classify(fast[-2], slow[-2], fast[-1], slow[-1], short, mid, long_, rsi, p.rsi_threshold)
    return result(
        outcome, reasons, "", bars_used=len(window), window_start=data.window_start, missing_in_window=data.missing,
        market_closures_in_window=data.market_closures, last_close=closes[-1],
        ema_cross_fast=fast[-1], ema_cross_slow=slow[-1], previous_ema_cross_fast=fast[-2], previous_ema_cross_slow=slow[-2],
        ema_trend_short=short, ema_trend_mid=mid, ema_trend_long=long_, rsi=rsi, atr=atr,
    )
