"""IndicatorEngine: calculates deterministic technical and risk indicators.

The engine performs no I/O and has no dependency on network access, LLMs, or
TradingAgents: it turns a MarketSnapshot already in memory into an
IndicatorSnapshot using only Python's standard library.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

from investment_committee.domain import MarketSnapshot, PriceBar

from .models import IndicatorSnapshot

TRADING_DAYS_PER_YEAR = 252
RSI_PERIOD = 14


class IndicatorCalculationError(Exception):
    """Raised when a MarketSnapshot's PriceBars cannot be used to calculate indicators."""


class IndicatorEngine:
    """Calculates a deterministic set of indicators from a MarketSnapshot."""

    def calculate(self, snapshot: MarketSnapshot) -> IndicatorSnapshot:
        """Return an IndicatorSnapshot for `snapshot`.

        Raises IndicatorCalculationError if the snapshot has no PriceBars,
        the PriceBars are not ordered chronologically, or a closing price is
        invalid for calculation. Individual indicators that cannot be
        computed due to insufficient history are None, not an error.
        """
        bars = _validated_bars(snapshot)
        closes = [bar.close for bar in bars]

        return IndicatorSnapshot(
            market_snapshot=snapshot,
            generated_at=datetime.now(timezone.utc),
            latest_close=closes[-1],
            sma_20=_sma(closes, 20),
            sma_50=_sma(closes, 50),
            sma_200=_sma(closes, 200),
            return_1d=_return_over(closes, 1),
            return_30d=_return_over(closes, 30),
            return_90d=_return_over(closes, 90),
            return_ytd=_return_ytd(bars),
            annualized_volatility=_annualized_volatility(closes),
            maximum_drawdown=_maximum_drawdown(closes),
            rsi_14=_rsi(closes, RSI_PERIOD),
        )


def _validated_bars(snapshot: MarketSnapshot) -> tuple[PriceBar, ...]:
    bars = tuple(snapshot.price_bars)
    if len(bars) == 0:
        raise IndicatorCalculationError("MarketSnapshot contains no PriceBars")
    for earlier, later in zip(bars, bars[1:]):
        if later.timestamp < earlier.timestamp:
            raise IndicatorCalculationError("PriceBars must be ordered chronologically")
    for bar in bars:
        if bar.close <= 0:
            raise IndicatorCalculationError("closing prices must be positive")
    return bars


def _sma(closes: list[Decimal], period: int) -> Decimal | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / Decimal(period)


def _return_over(closes: list[Decimal], trading_days: int) -> Decimal | None:
    if len(closes) <= trading_days:
        return None
    previous_close = closes[-1 - trading_days]
    return (closes[-1] / previous_close) - 1


def _return_ytd(bars: Sequence[PriceBar]) -> Decimal | None:
    latest_year = bars[-1].timestamp.year
    same_year_closes = [bar.close for bar in bars if bar.timestamp.year == latest_year]
    if len(same_year_closes) < 2:
        return None
    return (same_year_closes[-1] / same_year_closes[0]) - 1


def _annualized_volatility(closes: list[Decimal]) -> Decimal | None:
    if len(closes) < 3:
        return None
    daily_returns = [(closes[i] / closes[i - 1]) - 1 for i in range(1, len(closes))]
    daily_stdev = statistics.stdev(daily_returns)
    return daily_stdev * Decimal(TRADING_DAYS_PER_YEAR).sqrt()


def _maximum_drawdown(closes: list[Decimal]) -> Decimal | None:
    if len(closes) < 2:
        return None
    peak = closes[0]
    max_drawdown = Decimal("0")
    for close in closes:
        peak = max(peak, close)
        drawdown = (close / peak) - 1
        max_drawdown = min(max_drawdown, drawdown)
    return max_drawdown


def _rsi(closes: list[Decimal], period: int) -> Decimal | None:
    if len(closes) < period + 1:
        return None
    window = closes[-(period + 1):]
    changes = [window[i] - window[i - 1] for i in range(1, len(window))]
    total_gain = sum(change for change in changes if change > 0)
    total_loss = sum(-change for change in changes if change < 0)
    average_gain = total_gain / Decimal(period)
    average_loss = total_loss / Decimal(period)

    if average_loss == 0 and average_gain > 0:
        return Decimal("100")
    if average_gain == 0 and average_loss == 0:
        return Decimal("50")

    relative_strength = average_gain / average_loss
    return Decimal("100") - (Decimal("100") / (Decimal("1") + relative_strength))
