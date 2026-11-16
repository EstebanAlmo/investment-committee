"""EMA, and Wilder's RSI and ATR, for the experimental strategies
ema-rsi-atr-forex (simulation/ema_cross.py) and ema-3-9-20-50-120-forex
(simulation/ema_trend_cross.py).

Independent of simulation/technical.py, whose SMA and simple-average RSI and
ATR belong to the frozen strategies and are not touched. Pure functions on
Decimals, oldest value first; each uses EVERY value it is given, so the
caller decides the window (the bars of 300 expected hours in ema_cross.py,
of 720 in ema_trend_cross.py).
Each returns None when there are not enough values - never a guess.

EMA(n)   alpha = 2 / (n + 1)
         seed  = simple average of the first n values       (at index n - 1)
         EMA_t = alpha * value_t + (1 - alpha) * EMA_(t-1)

RSI(n), Wilder
         change_t = close_t - close_(t-1)
         gain_t = max(change_t, 0);  loss_t = max(-change_t, 0)
         seed  = simple averages of the first n gains and of the first n losses
         avg_t = ((n - 1) * avg_(t-1) + value_t) / n         (for gains and for losses)
         RSI   = 100 - 100 / (1 + avg_gain / avg_loss)
         avg_loss = 0  ->  100 if avg_gain > 0, else 50 (no movement at all)

ATR(n), Wilder
         TR_t  = max(high_t - low_t, |high_t - close_(t-1)|, |low_t - close_(t-1)|)
         seed  = simple average of the first n true ranges
         ATR_t = ((n - 1) * ATR_(t-1) + TR_t) / n

Because of the seed, a value depends on where the window starts. The seed's
weight after k further values is (1 - alpha)^k for the EMA and
((n - 1) / n)^k for Wilder's averages: with 300 values, about 5e-5 for
EMA50, 7e-13 for EMA20 and 7e-10 for RSI14/ATR14 (docs/ema-rsi-atr-signals.md).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from investment_committee.domain import PriceBar

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


def _check(period: int) -> None:
    if period < 1:
        raise ValueError("period must be at least 1")


def ema_series(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    """One EMA per value; None before the seed (the first period - 1)."""
    _check(period)
    out: list[Decimal | None] = [None] * min(len(values), period - 1)
    if len(values) < period:
        return out
    alpha = Decimal(2) / Decimal(period + 1)
    current = sum(values[:period], _ZERO) / Decimal(period)
    out.append(current)
    for value in values[period:]:
        current = alpha * value + (1 - alpha) * current
        out.append(current)
    return out


def ema(values: Sequence[Decimal], period: int) -> Decimal | None:
    series = ema_series(values, period)
    return series[-1] if series else None


def _wilder(values: Sequence[Decimal], period: int) -> Decimal | None:
    """Wilder's smoothed average of `values`; None with fewer than `period`."""
    if len(values) < period:
        return None
    current = sum(values[:period], _ZERO) / Decimal(period)
    for value in values[period:]:
        current = (Decimal(period - 1) * current + value) / Decimal(period)
    return current


def wilder_rsi(closes: Sequence[Decimal], period: int) -> Decimal | None:
    _check(period)
    changes = [later - earlier for earlier, later in zip(closes, closes[1:])]
    average_gain = _wilder([c if c > 0 else _ZERO for c in changes], period)
    average_loss = _wilder([-c if c < 0 else _ZERO for c in changes], period)
    if average_gain is None or average_loss is None:
        return None
    if average_loss == 0:
        return _HUNDRED if average_gain > 0 else Decimal("50")
    return _HUNDRED - _HUNDRED / (1 + average_gain / average_loss)


def true_ranges(bars: Sequence[PriceBar]) -> list[Decimal]:
    """One true range per bar from the second on."""
    return [
        max(bar.high - bar.low, abs(bar.high - previous.close), abs(bar.low - previous.close))
        for previous, bar in zip(bars, bars[1:])
    ]


def wilder_atr(bars: Sequence[PriceBar], period: int) -> Decimal | None:
    _check(period)
    return _wilder(true_ranges(bars), period)
