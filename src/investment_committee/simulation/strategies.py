"""Experimental strategies V2 for the scanner and the replay.

Three independent, deterministic strategies, each with its own name and
version. tendencia-momentum-volatilidad v1.0.0 (strategy.py) is untouched
and stays the baseline. Every strategy here:
  - uses only bars CLOSED at the decision moment (simulation.closed_bars);
  - applies the same data gates as v1 (consistent timestamps, enough
    history, FX market open, data no older than 3 hours);
  - sizes and checks costs with v1's own plan rules (strategy._plan, reused
    unchanged: stop/target at ATR multiples from the simulated fill, units
    from the account's risk fraction, gates on minimum size, margin, costs
    above 25 % of the risk and net reward/risk below 1.5);
  - emits SETUP_LONG / SETUP_SHORT when its rules point to a direction, so
    a cost or size rejection is still visible as a technical signal.

PARAMETERS WERE FIXED A PRIORI, from conventional textbook values, before
any of these strategies was run on any data, and were NOT tuned on April-
September 2026 (or on any other period). Changing any value means a new
version. All are EXPERIMENTAL and NOT VALIDATED.

1. tendencia-confirmada-4h v0.1.0 - trend following with higher-timeframe
   confirmation. 1h trend: close > SMA20 > SMA50 (LONG) or close < SMA20 <
   SMA50 (SHORT). 4h confirmation, from 4-hour buckets of closed 1h bars
   (UTC 00/04/08/12/16/20, only buckets fully elapsed): 4h close above its
   SMA20 and that SMA20 higher than 3 buckets earlier (mirror for SHORT).
   Entry filter: RSI14 (1h) in 45-65 for LONG, 35-55 for SHORT (not
   overextended). ATR14 between 3 and 40 pips. Stop 1.5 ATR, target 3 ATR.
2. ruptura-rango-londres v0.1.0 - range breakout with volatility and time
   filters. Range: high and low of the 8 closed bars before the last one.
   Compression: range width between 0.8 and 3 ATR14. Breakout: the last
   close beyond the range by more than 0.1 ATR. Time: the last bar must
   close between 08:00 and 12:00 London time (Europe/London, DST applied).
   ATR14 between 3 and 40 pips. Stop 1.5 ATR, target 3 ATR.
3. reversion-media-lateral v0.1.0 - mean reversion ONLY in a sideways
   market. Sideways: SMA50 moved at most 0.5 ATR over the last 12 bars and
   |SMA20 - SMA50| <= 0.75 ATR. Extremes: close below SMA20 - 2 standard
   deviations (20 closes) with RSI14 < 30 -> LONG; above SMA20 + 2 SD with
   RSI14 > 70 -> SHORT. ATR14 between 3 and 40 pips. Stop 1 ATR, target
   2 ATR (back towards the mean).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from investment_committee.domain import PriceBar

from .models import (
    AccountAssumptions,
    InstrumentSpec,
    MarketReading,
    SignalReason,
    StrategyParameters,
    TradeDirection,
    TradeSignal,
    TradingCosts,
    Trend,
)
from .opportunities import InstrumentProfile, ScanStrategy, TrendMomentumVolatilityStrategy, conversion_for
from .strategy import _plan
from .technical import atr, closed_bars, is_forex_market_open, rsi, sma

LONDON = ZoneInfo("Europe/London")
_EMPTY = MarketReading(None, None, None, None, None, None, None, None, None)


def parameters_fingerprint(parameters: Any) -> str:
    """SHA-256 of a strategy's parameters (stable JSON): a version is frozen
    to exactly these values."""
    payload = json.dumps(asdict(parameters), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class CommonRules:
    """Gates and execution values every V2 strategy shares with v1."""

    timeframe: timedelta = timedelta(hours=1)
    rsi_period: int = 14
    atr_period: int = 14
    atr_min_pips: Decimal = Decimal("3")
    atr_max_pips: Decimal = Decimal("40")
    min_net_reward_risk: Decimal = Decimal("1.5")
    max_cost_fraction_of_risk: Decimal = Decimal("0.25")
    max_data_age: timedelta = timedelta(hours=3)
    max_entry_delay: timedelta = timedelta(hours=2)
    expiry_bars: int = 24


class _V2Strategy(ScanStrategy):
    """Shared skeleton: data gates, then `_rules`, then v1's plan rules."""

    interval_code = "1h"
    history_period = "30d"
    calibrated_symbols: frozenset[str] = frozenset()
    stop_atr_multiple: Decimal
    target_atr_multiple: Decimal
    min_bars: int
    parameters_object: Any

    def __init__(self) -> None:
        self.common = CommonRules()
        self.timeframe = self.common.timeframe
        self.min_reward_risk = self.common.min_net_reward_risk
        # The execution-side values the replay, tracker and plan rules read.
        self.parameters = StrategyParameters(
            timeframe=self.common.timeframe, rsi_period=self.common.rsi_period, atr_period=self.common.atr_period,
            atr_min_pips=self.common.atr_min_pips, atr_max_pips=self.common.atr_max_pips,
            stop_atr_multiple=self.stop_atr_multiple, target_atr_multiple=self.target_atr_multiple,
            min_net_reward_risk=self.common.min_net_reward_risk, max_cost_fraction_of_risk=self.common.max_cost_fraction_of_risk,
            expiry_bars=self.common.expiry_bars, max_data_age=self.common.max_data_age, max_entry_delay=self.common.max_entry_delay,
        )

    @property
    def strategy_id(self) -> str:
        return f"{self.name}@{self.version}"

    @property
    def fingerprint(self) -> str:
        return parameters_fingerprint(self.parameters_object)

    def unsupported_reason(self, profile: InstrumentProfile) -> str | None:
        if conversion_for(profile) is None:
            return "ni la divisa base ni la de cotización es USD: haría falta un tipo de cambio cruzado"
        return None

    def evaluate(self, bars: Sequence[PriceBar], as_of: datetime, *, signal_id: str, profile: InstrumentProfile, account: AccountAssumptions) -> TradeSignal:
        closed = closed_bars(bars, self.timeframe, as_of)[-(self.min_bars + 1):]
        spec, costs = profile.spec, profile.costs

        def signal(direction: TradeDirection, reasons: list[SignalReason], reading: MarketReading, plan=None) -> TradeSignal:  # type: ignore[no-untyped-def]
            return TradeSignal(
                signal_id=signal_id, created_at=as_of, instrument=spec, timeframe=self.timeframe,
                data_as_of=closed[-1].timestamp + self.timeframe if closed else None,
                last_bar_open_time=closed[-1].timestamp if closed else None, direction=direction, reasons=reasons,
                reading=reading, plan=plan, costs=costs, parameters=self.parameters, account=account, bars_used=len(closed),
                strategy_name=self.name, strategy_version=self.version,
            )

        if any(later.timestamp <= earlier.timestamp for earlier, later in zip(closed, closed[1:])):
            return signal(TradeDirection.NO_TRADE, [SignalReason.INCONSISTENT_DATA], _EMPTY)
        if len(closed) < self.min_bars:
            return signal(TradeDirection.NO_TRADE, [SignalReason.INSUFFICIENT_DATA], _EMPTY)
        if not is_forex_market_open(as_of):
            return signal(TradeDirection.NO_TRADE, [SignalReason.MARKET_CLOSED], _EMPTY)
        if as_of - (closed[-1].timestamp + self.timeframe) > self.common.max_data_age:
            return signal(TradeDirection.NO_TRADE, [SignalReason.STALE_DATA], _EMPTY)

        reading, direction, reasons = self._rules(closed, spec, as_of)
        average = reading.atr_pips
        if average is None or reading.atr is None:
            return signal(TradeDirection.NO_TRADE, [*reasons, SignalReason.INSUFFICIENT_DATA], reading)
        if average < self.common.atr_min_pips:
            return signal(TradeDirection.NO_TRADE, [*reasons, SignalReason.VOLATILITY_TOO_LOW], reading)
        if average > self.common.atr_max_pips:
            return signal(TradeDirection.NO_TRADE, [*reasons, SignalReason.VOLATILITY_TOO_HIGH], reading)
        reasons.append(SignalReason.VOLATILITY_OK)
        if direction is TradeDirection.NO_TRADE:
            return signal(TradeDirection.NO_TRADE, reasons, reading)
        reasons.append(SignalReason.SETUP_LONG if direction is TradeDirection.LONG else SignalReason.SETUP_SHORT)
        plan, rejection = _plan(direction, reading, closed[-1], spec, self.parameters, costs, account)
        if rejection is not None:
            return signal(TradeDirection.NO_TRADE, [*reasons, rejection], reading)
        return signal(direction, reasons, reading, plan)

    def _rules(self, closed: Sequence[PriceBar], spec: InstrumentSpec, as_of: datetime) -> tuple[MarketReading, TradeDirection, list[SignalReason]]:
        raise NotImplementedError

    @staticmethod
    def _reading(closed: Sequence[PriceBar], spec: InstrumentSpec, fast: Decimal | None, slow: Decimal | None, trend: Trend | None,
                 support: Decimal | None = None, resistance: Decimal | None = None) -> MarketReading:
        closes = [b.close for b in closed]
        average = atr(closed, 14)
        return MarketReading(
            quote_price=closes[-1], sma_fast=fast, sma_slow=slow, rsi=rsi(closes, 14), atr=average,
            atr_pips=None if average is None else average / spec.pip_size,
            support=support if support is not None else min(b.low for b in closed[-24:]),
            resistance=resistance if resistance is not None else max(b.high for b in closed[-24:]),
            trend=trend,
        )


# --- 1. trend with higher-timeframe confirmation ---


@dataclass(frozen=True)
class TrendHTFParameters:
    sma_fast: int = 20
    sma_slow: int = 50
    htf_hours: int = 4
    htf_sma: int = 20
    htf_slope_buckets: int = 3
    rsi_long: tuple[Decimal, Decimal] = (Decimal("45"), Decimal("65"))
    rsi_short: tuple[Decimal, Decimal] = (Decimal("35"), Decimal("55"))
    stop_atr_multiple: Decimal = Decimal("1.5")
    target_atr_multiple: Decimal = Decimal("3.0")
    common: CommonRules = field(default_factory=CommonRules)


def higher_timeframe_closes(closed: Sequence[PriceBar], hours: int, as_of: datetime) -> list[Decimal]:
    """Closes of `hours`-hour UTC buckets made of closed 1h bars; a bucket
    counts only once fully elapsed at `as_of` (no partial bucket)."""
    step = hours * 3600
    buckets: dict[int, PriceBar] = {}
    for bar in closed:
        key = int(bar.timestamp.timestamp()) // step * step
        buckets[key] = bar  # bars are in time order: the last one is the bucket's close
    cutoff = int(as_of.timestamp())
    return [buckets[k].close for k in sorted(buckets) if k + step <= cutoff]


class TrendHTFStrategy(_V2Strategy):
    name = "tendencia-confirmada-4h"
    version = "0.1.0"

    def __init__(self) -> None:
        self.parameters_object = TrendHTFParameters()
        p = self.parameters_object
        self.stop_atr_multiple, self.target_atr_multiple = p.stop_atr_multiple, p.target_atr_multiple
        self.min_bars = (p.htf_sma + p.htf_slope_buckets + 1) * p.htf_hours + 4
        super().__init__()

    def _rules(self, closed, spec, as_of):  # type: ignore[no-untyped-def]
        p = self.parameters_object
        closes = [b.close for b in closed]
        fast, slow = sma(closes, p.sma_fast), sma(closes, p.sma_slow)
        last = closes[-1]
        trend = Trend.SIDEWAYS
        if fast is not None and slow is not None:
            if last > fast > slow:
                trend = Trend.UP
            elif last < fast < slow:
                trend = Trend.DOWN
        reading = self._reading(closed, spec, fast, slow, trend)
        reasons = [{Trend.UP: SignalReason.TREND_UP, Trend.DOWN: SignalReason.TREND_DOWN}.get(trend, SignalReason.TREND_SIDEWAYS)]
        if trend is Trend.SIDEWAYS:
            return reading, TradeDirection.NO_TRADE, reasons
        htf = higher_timeframe_closes(closed, p.htf_hours, as_of)
        now = sma(htf, p.htf_sma)
        before = sma(htf[: -p.htf_slope_buckets], p.htf_sma) if len(htf) > p.htf_slope_buckets else None
        if now is None or before is None:
            return reading, TradeDirection.NO_TRADE, [*reasons, SignalReason.INSUFFICIENT_DATA]
        up = trend is Trend.UP
        confirms = (htf[-1] > now and now > before) if up else (htf[-1] < now and now < before)
        reasons.append(SignalReason.HTF_CONFIRMS if confirms else SignalReason.HTF_NOT_CONFIRMING)
        low, high = p.rsi_long if up else p.rsi_short
        in_band = reading.rsi is not None and low <= reading.rsi <= high
        reasons.append(SignalReason.RSI_IN_BAND if in_band else SignalReason.RSI_OUT_OF_BAND)
        if confirms and in_band:
            return reading, TradeDirection.LONG if up else TradeDirection.SHORT, reasons
        return reading, TradeDirection.NO_TRADE, reasons


# --- 2. range breakout with volatility and time filters ---


@dataclass(frozen=True)
class BreakoutParameters:
    range_bars: int = 8
    min_width_atr: Decimal = Decimal("0.8")
    max_width_atr: Decimal = Decimal("3.0")
    breakout_buffer_atr: Decimal = Decimal("0.1")
    session_start_london: time = time(8, 0)  # last bar closes at or after
    session_end_london: time = time(12, 0)  # ... and at or before
    stop_atr_multiple: Decimal = Decimal("1.5")
    target_atr_multiple: Decimal = Decimal("3.0")
    common: CommonRules = field(default_factory=CommonRules)


class BreakoutStrategy(_V2Strategy):
    name = "ruptura-rango-londres"
    version = "0.1.0"

    def __init__(self) -> None:
        self.parameters_object = BreakoutParameters()
        p = self.parameters_object
        self.stop_atr_multiple, self.target_atr_multiple = p.stop_atr_multiple, p.target_atr_multiple
        self.min_bars = max(p.range_bars + 1, 15, 24) + 1
        super().__init__()

    def _rules(self, closed, spec, as_of):  # type: ignore[no-untyped-def]
        p = self.parameters_object
        box = closed[-(p.range_bars + 1):-1]
        low, high = min(b.low for b in box), max(b.high for b in box)
        reading = self._reading(closed, spec, None, None, None, support=low, resistance=high)
        close_time = (closed[-1].timestamp + self.timeframe).astimezone(LONDON).time()
        if not (p.session_start_london <= close_time <= p.session_end_london):
            return reading, TradeDirection.NO_TRADE, [SignalReason.OUTSIDE_TRADING_HOURS]
        if reading.atr is None:
            return reading, TradeDirection.NO_TRADE, [SignalReason.INSUFFICIENT_DATA]
        width = (high - low) / reading.atr
        if width < p.min_width_atr:
            return reading, TradeDirection.NO_TRADE, [SignalReason.RANGE_TOO_NARROW]
        if width > p.max_width_atr:
            return reading, TradeDirection.NO_TRADE, [SignalReason.RANGE_TOO_WIDE]
        reasons = [SignalReason.RANGE_COMPRESSED]
        buffer = p.breakout_buffer_atr * reading.atr
        last = closed[-1].close
        if last > high + buffer:
            return reading, TradeDirection.LONG, [*reasons, SignalReason.BREAKOUT_UP]
        if last < low - buffer:
            return reading, TradeDirection.SHORT, [*reasons, SignalReason.BREAKOUT_DOWN]
        return reading, TradeDirection.NO_TRADE, [*reasons, SignalReason.NO_BREAKOUT]


# --- 3. mean reversion only when ranging ---


@dataclass(frozen=True)
class MeanReversionParameters:
    sma_fast: int = 20
    sma_slow: int = 50
    slope_bars: int = 12
    max_slope_atr: Decimal = Decimal("0.5")
    max_gap_atr: Decimal = Decimal("0.75")
    band_sd: Decimal = Decimal("2")
    rsi_low: Decimal = Decimal("30")
    rsi_high: Decimal = Decimal("70")
    stop_atr_multiple: Decimal = Decimal("1.0")
    target_atr_multiple: Decimal = Decimal("2.0")
    common: CommonRules = field(default_factory=CommonRules)


def standard_deviation(values: Sequence[Decimal]) -> Decimal:
    mean = sum(values, Decimal("0")) / len(values)
    return (sum(((v - mean) ** 2 for v in values), Decimal("0")) / len(values)).sqrt()


class MeanReversionStrategy(_V2Strategy):
    name = "reversion-media-lateral"
    version = "0.1.0"

    def __init__(self) -> None:
        self.parameters_object = MeanReversionParameters()
        p = self.parameters_object
        self.stop_atr_multiple, self.target_atr_multiple = p.stop_atr_multiple, p.target_atr_multiple
        self.min_bars = p.sma_slow + p.slope_bars + 2
        super().__init__()

    def _rules(self, closed, spec, as_of):  # type: ignore[no-untyped-def]
        p = self.parameters_object
        closes = [b.close for b in closed]
        fast, slow = sma(closes, p.sma_fast), sma(closes, p.sma_slow)
        slow_before = sma(closes[: -p.slope_bars], p.sma_slow)
        reading = self._reading(closed, spec, fast, slow, Trend.SIDEWAYS)
        if fast is None or slow is None or slow_before is None or reading.atr is None:
            return reading, TradeDirection.NO_TRADE, [SignalReason.INSUFFICIENT_DATA]
        ranging = abs(slow - slow_before) <= p.max_slope_atr * reading.atr and abs(fast - slow) <= p.max_gap_atr * reading.atr
        if not ranging:
            return reading, TradeDirection.NO_TRADE, [SignalReason.MARKET_TRENDING]
        reasons = [SignalReason.MARKET_RANGING]
        sd = standard_deviation(closes[-p.sma_fast:])
        last = closes[-1]
        if reading.rsi is not None and last < fast - p.band_sd * sd and reading.rsi < p.rsi_low:
            return reading, TradeDirection.LONG, [*reasons, SignalReason.BAND_EXTREME_LOW]
        if reading.rsi is not None and last > fast + p.band_sd * sd and reading.rsi > p.rsi_high:
            return reading, TradeDirection.SHORT, [*reasons, SignalReason.BAND_EXTREME_HIGH]
        return reading, TradeDirection.NO_TRADE, [*reasons, SignalReason.NO_EXTREME]


# --- registry ---


def all_strategies() -> list[ScanStrategy]:
    """The baseline v1.0.0 plus the three experimental V2 strategies."""
    return [TrendMomentumVolatilityStrategy(), TrendHTFStrategy(), BreakoutStrategy(), MeanReversionStrategy()]


def strategy_by_id(strategy_id: str) -> ScanStrategy:
    for strategy in all_strategies():
        if f"{strategy.name}@{strategy.version}" == strategy_id or strategy.name == strategy_id:
            return strategy
    raise KeyError(f"unknown strategy {strategy_id}")


def strategy_parameters(strategy: ScanStrategy) -> Any:
    """The object whose values define the strategy's version."""
    return getattr(strategy, "parameters_object", None) or strategy.parameters  # type: ignore[attr-defined]

