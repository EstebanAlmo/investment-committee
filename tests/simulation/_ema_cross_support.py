"""Synthetic hourly bars and an INDEPENDENT reference calculation for the
ema-rsi-atr-forex tests. Not collected by pytest.

The reference uses exact fractions and formulas written differently from
simulation/wilder.py (a closed-form weighted sum for the EMA, the
"subtract a share, add a share" form for Wilder's average), so an error in
one implementation does not hide in the other. The bars are made up
(QuoteKind.SYNTHETIC) and must never be read as market data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from typing import Sequence

from investment_committee.domain import PriceBar

UTC = timezone.utc
HOUR = timedelta(hours=1)
START = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)  # a Monday
SOURCE = "serie sintética de pruebas"


def always_open(_moment: datetime) -> bool:
    return True


def walk(count: int, seed: int = 7, start: datetime = START, first: str = "1.10000") -> list[PriceBar]:
    """A deterministic pseudo-random walk (a linear congruential generator,
    no `random` module): steps of -12..+12 tenths of a pip, in runs, so that
    the averages cross often and in both directions."""
    state, close, drift, bars = seed, Decimal(first), 0, []
    for index in range(count):
        state = (state * 1103515245 + 12345) % 2147483648
        if index % 37 == 0:
            drift = (state >> 8) % 9 - 4
        step = Decimal((state >> 16) % 25 - 12 + drift) / Decimal(100000)
        open_, close = close, close + step
        wick = Decimal((state >> 4) % 6 + 1) / Decimal(100000)
        bars.append(PriceBar(start + index * HOUR, open_, max(open_, close) + wick, min(open_, close) - wick, close, 0))
    return bars


def closes_to_bars(closes: Sequence[str], start: datetime = START, wick: str = "0.00010") -> list[PriceBar]:
    """One bar per close: open = previous close, a fixed wick on both sides."""
    bars, previous, w = [], Decimal(closes[0]), Decimal(wick)
    for index, text in enumerate(closes):
        close = Decimal(text)
        bars.append(PriceBar(start + index * HOUR, previous, max(previous, close) + w, min(previous, close) - w, close, 0))
        previous = close
    return bars


# --- independent reference (exact fractions) ---


def ref_ema(values: Sequence[Decimal], period: int) -> Fraction:
    """seed * (1 - a)^k + sum of a * (1 - a)^(k - j) * value_j."""
    a = Fraction(2, period + 1)
    seed = sum((Fraction(v) for v in values[:period]), Fraction(0)) / period
    rest = [Fraction(v) for v in values[period:]]
    k = len(rest)
    return seed * (1 - a) ** k + sum((a * (1 - a) ** (k - j) * v for j, v in enumerate(rest, start=1)), Fraction(0))


def _ref_wilder(values: Sequence[Fraction], period: int) -> Fraction:
    average = sum(values[:period], Fraction(0)) / period
    for value in values[period:]:
        average = average - average / period + value / period
    return average


def ref_rsi(closes: Sequence[Decimal], period: int) -> Fraction:
    ups, downs = [], []
    for index in range(1, len(closes)):
        move = Fraction(closes[index]) - Fraction(closes[index - 1])
        ups.append(move if move > 0 else Fraction(0))
        downs.append(-move if move < 0 else Fraction(0))
    gain, loss = _ref_wilder(ups, period), _ref_wilder(downs, period)
    if loss == 0:
        return Fraction(100) if gain > 0 else Fraction(50)
    return 100 * gain / (gain + loss)  # the same number as 100 - 100 / (1 + gain / loss)


def ref_atr(bars: Sequence[PriceBar], period: int) -> Fraction:
    ranges = []
    for index in range(1, len(bars)):
        high, low, before = Fraction(bars[index].high), Fraction(bars[index].low), Fraction(bars[index - 1].close)
        ranges.append(max(high - low, abs(high - before), abs(low - before)))
    return _ref_wilder(ranges, period)


def ref_outcome(window: Sequence[PriceBar]) -> tuple[str, Fraction, Fraction, Fraction]:
    """(outcome, EMA20, EMA50, RSI14) of the last bar of a 300-bar window."""
    closes = [b.close for b in window]
    fast, slow = ref_ema(closes, 20), ref_ema(closes, 50)
    fast_before, slow_before = ref_ema(closes[:-1], 20), ref_ema(closes[:-1], 50)
    rsi = ref_rsi(closes, 14)
    if fast_before <= slow_before and fast > slow:
        return ("LONG" if rsi > 50 else "CRUCE_SIN_CONFIRMACION"), fast, slow, rsi
    if fast_before >= slow_before and fast < slow:
        return ("SHORT" if rsi < 50 else "CRUCE_SIN_CONFIRMACION"), fast, slow, rsi
    return "SIN_CRUCE", fast, slow, rsi


# --- ema-3-9-20-50-120-forex (simulation/ema_trend_cross.py) ---


def moves_to_bars(moves: Sequence[int], sign: int = 1, start: datetime = START) -> list[PriceBar]:
    """Closes that start at 1.10000 and change by `moves` (hundred-thousandths, times `sign`)."""
    level, closes = 110000, []
    for move in moves:
        level += sign * move
        closes.append(f"{level / 100000:.5f}")
    return closes_to_bars(closes, start)


def ref_trend_outcome(window: Sequence[PriceBar]) -> tuple[str, tuple[str, ...], dict[int, Fraction], Fraction]:
    """(outcome, reasons, {period: EMA}, RSI14) of the last bar of a window,
    for the rule EMA3 x EMA9 with EMA20 / EMA50 / EMA120 in order and RSI14."""
    closes = [b.close for b in window]
    averages = {n: ref_ema(closes, n) for n in (3, 9, 20, 50, 120)}
    fast_before, slow_before = ref_ema(closes[:-1], 3), ref_ema(closes[:-1], 9)
    rsi = ref_rsi(closes, 14)
    if fast_before <= slow_before and averages[3] > averages[9]:
        side, cross = "LONG", "CRUCE_ALCISTA"
        aligned, confirmed = averages[20] > averages[50] > averages[120], rsi > 50
    elif fast_before >= slow_before and averages[3] < averages[9]:
        side, cross = "SHORT", "CRUCE_BAJISTA"
        aligned, confirmed = averages[20] < averages[50] < averages[120], rsi < 50
    else:
        return "SIN_CRUCE", ("SIN_CRUCE",), averages, rsi
    reasons = (cross, "TENDENCIA_ALINEADA" if aligned else "TENDENCIA_NO_ALINEADA", "RSI_CONFIRMA" if confirmed else "RSI_NO_CONFIRMA")
    return (side if aligned and confirmed else "CRUCE_SIN_CONFIRMACION"), reasons, averages, rsi
