"""Intraday technical calculations used by the trading strategy.

Pure functions over closed PriceBars, oldest first. Each returns None when
there are not enough bars - never a guessed value. Kept separate from
indicators/ (daily IndicatorSnapshot for the committee) because
simulation/ may not depend on indicators/ (docs/architecture.md).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Sequence

from investment_committee.domain import PriceBar


def sma(closes: Sequence[Decimal], period: int) -> Decimal | None:
    if period <= 0 or len(closes) < period:
        return None
    return sum(closes[-period:], Decimal("0")) / Decimal(period)


def rsi(closes: Sequence[Decimal], period: int) -> Decimal | None:
    """Simple-average RSI, the same formula as indicators/engine.py."""
    if len(closes) < period + 1:
        return None
    window = closes[-(period + 1):]
    changes = [window[i] - window[i - 1] for i in range(1, len(window))]
    average_gain = sum((c for c in changes if c > 0), Decimal("0")) / Decimal(period)
    average_loss = sum((-c for c in changes if c < 0), Decimal("0")) / Decimal(period)
    if average_loss == 0:
        return Decimal("100") if average_gain > 0 else Decimal("50")
    relative_strength = average_gain / average_loss
    return Decimal("100") - (Decimal("100") / (Decimal("1") + relative_strength))


def atr(bars: Sequence[PriceBar], period: int) -> Decimal | None:
    """Average True Range: simple average of the last `period` true ranges."""
    if len(bars) < period + 1:
        return None
    window = bars[-(period + 1):]
    true_ranges = []
    for previous, bar in zip(window, window[1:]):
        true_ranges.append(
            max(bar.high - bar.low, abs(bar.high - previous.close), abs(bar.low - previous.close))
        )
    return sum(true_ranges, Decimal("0")) / Decimal(period)


def recent_range(bars: Sequence[PriceBar], lookback: int) -> tuple[Decimal, Decimal] | None:
    """(support, resistance): the lowest low and highest high of the last
    `lookback` bars - a simple, mechanical reading, not a drawn level."""
    if lookback <= 0 or len(bars) < lookback:
        return None
    window = bars[-lookback:]
    return min(bar.low for bar in window), max(bar.high for bar in window)


def is_forex_market_open(moment: datetime) -> bool:
    """Spot FX trades around the clock on weekdays. Approximation: closed
    from Friday 22:00 UTC to Sunday 22:00 UTC (ignores daylight saving and
    bank holidays)."""
    utc = moment.astimezone(timezone.utc)
    weekday, hour = utc.weekday(), utc.hour  # Monday == 0
    if weekday == 4 and hour >= 22:
        return False
    if weekday == 5:
        return False
    if weekday == 6 and hour < 22:
        return False
    return True


def closed_bars(bars: Sequence[PriceBar], timeframe: timedelta, as_of: datetime) -> list[PriceBar]:
    """Bars whose period has fully ended by `as_of`. The provider returns the
    current bar while it is still forming; using it would leak prices that
    were not final at decision time."""
    return [bar for bar in bars if bar.timestamp + timeframe <= as_of]
