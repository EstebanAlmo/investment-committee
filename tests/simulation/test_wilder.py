"""Tests for simulation.wilder: EMA and Wilder's RSI and ATR.

Expected values come from hand calculations written out in each test and
from an independent exact-fraction reference (tests/simulation/_ema_cross_support.py)."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest

from investment_committee.domain import PriceBar
from investment_committee.simulation.wilder import ema, ema_series, true_ranges, wilder_atr, wilder_rsi

from ._ema_cross_support import START, HOUR, ref_atr, ref_ema, ref_rsi, walk

TOLERANCE = Fraction(1, 10**20)  # Decimal works with 28 significant digits


def d(*values: str) -> list[Decimal]:
    return [Decimal(v) for v in values]


def close_to(value: Decimal | None, expected: Fraction) -> bool:
    return value is not None and abs(Fraction(value) - expected) < TOLERANCE


def hlc(index: int, high: str, low: str, close: str) -> PriceBar:
    return PriceBar(START + index * HOUR, Decimal(close), Decimal(high), Decimal(low), Decimal(close), 0)


# --- EMA ---


def test_ema_by_hand() -> None:
    # period 3: alpha = 1/2; seed = (1 + 2 + 3) / 3 = 2; then (4 + 2) / 2 = 3; then (5 + 3) / 2 = 4
    assert ema_series(d("1", "2", "3", "4", "5"), 3) == [None, None, Decimal("2"), Decimal("3"), Decimal("4")]
    # period 2: alpha = 2/3; seed = 10.5; then 2/3 * 13 + 1/3 * 10.5 = 73/6; then 2/3 * 12 + 1/3 * 73/6 = 217/18
    values = d("10", "11", "13", "12")
    assert close_to(ema(values[:3], 2), Fraction(73, 6)) and close_to(ema(values, 2), Fraction(217, 18))


def test_ema_with_exactly_one_period_is_the_simple_average() -> None:
    assert ema(d("1.1000", "1.1010", "1.1030"), 3) == Decimal("3.3040") / 3


def test_ema_needs_a_full_period() -> None:
    assert ema(d("1", "2"), 3) is None and ema([], 3) is None
    assert ema_series(d("1", "2"), 3) == [None, None] and ema_series([], 3) == []
    with pytest.raises(ValueError):
        ema(d("1"), 0)


def test_ema_matches_the_independent_reference() -> None:
    closes = [bar.close for bar in walk(300)]

    assert close_to(ema(closes, 20), ref_ema(closes, 20)) and close_to(ema(closes, 50), ref_ema(closes, 50))


# --- RSI ---


def test_wilder_rsi_by_hand() -> None:
    # changes: +1, -0.5, +1 | -0.5, +1        (period 3: the first three seed the averages)
    # seed:  gain 2/3, loss 1/6
    # -0.5:  gain (2 * 2/3 + 0) / 3 = 4/9;    loss (2 * 1/6 + 1/2) / 3 = 5/18
    # +1:    gain (2 * 4/9 + 1) / 3 = 17/27;  loss (2 * 5/18 + 0) / 3 = 5/27
    # RSI = 100 - 100 / (1 + 17/5) = 850/11 = 77.27...
    closes = d("10", "11", "10.5", "11.5", "11.0", "12.0")

    assert close_to(wilder_rsi(closes, 3), Fraction(850, 11))
    assert close_to(wilder_rsi(closes[:4], 3), Fraction(80))  # the seed alone: 100 - 100 / (1 + 4)


def test_wilder_rsi_extremes_and_missing_data() -> None:
    assert wilder_rsi(d("1", "2", "3", "4"), 3) == Decimal("100")  # only gains
    assert wilder_rsi(d("4", "3", "2", "1"), 3) == Decimal("0")  # only losses
    assert wilder_rsi(d("2", "2", "2", "2"), 3) == Decimal("50")  # no movement at all
    assert wilder_rsi(d("1", "2", "3"), 3) is None  # three closes are two changes
    with pytest.raises(ValueError):
        wilder_rsi(d("1", "2"), 0)


def test_wilder_rsi_is_not_the_simple_average_rsi_of_the_frozen_strategies() -> None:
    from investment_committee.simulation.technical import rsi as simple_rsi

    closes = [bar.close for bar in walk(300)]

    assert close_to(wilder_rsi(closes, 14), ref_rsi(closes, 14))
    assert abs(wilder_rsi(closes, 14) - simple_rsi(closes, 14)) > Decimal("0.01")  # type: ignore[operator]


# --- ATR ---


def test_wilder_atr_by_hand() -> None:
    bars = [
        hlc(0, "10.5", "9.5", "10.0"),
        hlc(1, "11.0", "10.0", "10.8"),  # TR = max(1.0, |11.0 - 10.0|, |10.0 - 10.0|) = 1.0
        hlc(2, "10.9", "10.1", "10.2"),  # TR = max(0.8, 0.1, 0.7) = 0.8
        hlc(3, "12.0", "11.0", "11.9"),  # a gap up: TR = max(1.0, |12.0 - 10.2|, |11.0 - 10.2|) = 1.8
        hlc(4, "10.5", "9.8", "10.0"),  # a gap down: TR = max(0.7, |10.5 - 11.9|, |9.8 - 11.9|) = 2.1
    ]

    assert true_ranges(bars) == d("1.0", "0.8", "1.8", "2.1")
    assert wilder_atr(bars[:3], 2) == Decimal("0.9")  # the seed: (1.0 + 0.8) / 2
    assert wilder_atr(bars[:4], 2) == Decimal("1.35")  # (1 * 0.9 + 1.8) / 2
    assert wilder_atr(bars, 2) == Decimal("1.725")  # (1 * 1.35 + 2.1) / 2


def test_wilder_atr_needs_period_plus_one_bars_and_matches_the_reference() -> None:
    bars = walk(300)

    assert wilder_atr(bars[:14], 14) is None and wilder_atr(bars[:15], 14) is not None
    assert close_to(wilder_atr(bars, 14), ref_atr(bars, 14))


# --- the window ---


def test_the_same_values_always_give_the_same_result() -> None:
    bars = walk(300)
    closes = [bar.close for bar in bars]

    assert ema(closes, 50) == ema(list(closes), 50) and wilder_rsi(closes, 14) == wilder_rsi(tuple(closes), 14)
    assert wilder_atr(bars, 14) == wilder_atr(tuple(bars), 14)


def test_moving_the_window_changes_the_value_slightly() -> None:
    """The value at one and the same bar, from two 300-bar windows that start
    one bar apart: different seeds, so not identical - but far below a tenth
    of a pip (0.00001) for EMA50, and negligible for EMA20, RSI14, ATR14."""
    bars = walk(301)
    closes = [bar.close for bar in bars]

    slow = abs(ema(closes[:300], 50) - ema_series(closes[1:], 50)[-2])  # type: ignore[operator]
    fast = abs(ema(closes[:300], 20) - ema_series(closes[1:], 20)[-2])  # type: ignore[operator]
    rsi = abs(wilder_rsi(closes[:300], 14) - wilder_rsi(closes[1:300], 14))  # type: ignore[operator]
    atr = abs(wilder_atr(bars[:300], 14) - wilder_atr(bars[1:300], 14))  # type: ignore[operator]

    assert Decimal("0") < slow < Decimal("0.000001")
    assert fast < Decimal("1e-12") and rsi < Decimal("1e-6") and atr < Decimal("1e-12")
