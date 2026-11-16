"""Tests for simulation.ema_trend_cross: signal detection of
ema-3-9-20-50-120-forex (strategy B).

Synthetic bars only (QuoteKind.SYNTHETIC unless a test is about kinds).
Expected outcomes come from an independent exact-fraction reference
(tests/simulation/_ema_cross_support.py) and from a series built so that
each case happens at a known bar."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from itertools import permutations
from typing import Sequence

import pytest

from investment_committee.domain import PriceBar
from investment_committee.market_intelligence.quotes import QuoteKind
from investment_committee.simulation import ema_cross, ema_trend_cross
from investment_committee.simulation.ema_trend_cross import (
    STRATEGY_ID,
    EmaTrendCrossParameters,
    Reason,
    TrendCrossEvaluation,
    classify,
    evaluate,
    fingerprint,
)
from investment_committee.simulation.sessions import is_week_open
from investment_committee.simulation.signals import Outcome
from investment_committee.simulation.strategies import all_strategies
from investment_committee.simulation.wilder import ema

from ._ema_cross_support import HOUR, SOURCE, always_open, moves_to_bars, ref_atr, ref_trend_outcome, walk

UTC = timezone.utc
D = Decimal
TOLERANCE = Fraction(1, 10**18)
R = Reason
UP, DOWN = R.CROSS_UP, R.CROSS_DOWN
ALIGNED, NOT_ALIGNED, RSI_OK, RSI_NO = R.TREND_ALIGNED, R.TREND_NOT_ALIGNED, R.RSI_CONFIRMS, R.RSI_DOES_NOT_CONFIRM


def run(bars: Sequence[PriceBar], as_of: datetime | None = None, **kwargs) -> TrendCrossEvaluation:  # type: ignore[no-untyped-def]
    kwargs.setdefault("is_open", always_open)
    return evaluate(bars, as_of or bars[-1].timestamp + HOUR, symbol="EURUSD", data_kind=QuoteKind.SYNTHETIC, data_source=SOURCE, **kwargs)


def at(bars: Sequence[PriceBar], index: int) -> TrendCrossEvaluation:
    """The evaluation of the candle at `index`, with the history up to it."""
    return run(bars[: index + 1])


def pullbacks(sign: int = 1) -> list[PriceBar]:
    """A steady trend of 1 pip a bar, pullbacks inside it, then a reversal
    (moves in tenths of a pip, times `sign`). With sign = +1, 0-based bars:
      801  EMA3 falls below EMA9 in an uptrend, RSI 51.06  -> not confirmed: trend and RSI
      810  EMA3 rises above EMA9, EMA20 > EMA50 > EMA120, RSI 56.80 -> LONG
      834  cross down in the uptrend, RSI 59.38            -> not confirmed: trend and RSI
      847  cross up, trend aligned, but RSI 48.15          -> not confirmed: RSI only
      848  EMA3 still above EMA9                           -> no cross: never confirmed later
      941  cross up after the reversal, RSI 37.85          -> not confirmed: trend and RSI
      949  cross down, EMA20 < EMA50 < EMA120, RSI 42.68   -> SHORT
    and the mirror image with sign = -1."""
    moves = [10] * 800 + [-60] * 4 + [10] * 6 + [60] * 3 + [10] * 20 + [-40] * 5 + [4] * 12 + [10] * 30 + [-40] * 60 + [150] * 4 + [-40] * 20
    return moves_to_bars(moves, sign)


# --- identity and parameters ---


def test_the_strategy_has_its_own_identity_and_is_not_among_the_frozen_ones() -> None:
    p = EmaTrendCrossParameters()

    assert STRATEGY_ID == "ema-3-9-20-50-120-forex@0.1.0" != ema_cross.STRATEGY_ID
    assert (p.ema_cross_fast, p.ema_cross_slow, p.ema_trend_short, p.ema_trend_mid, p.ema_trend_long) == (3, 9, 20, 50, 120)
    assert (p.rsi_period, p.atr_period, p.rsi_threshold) == (14, 14, D("50"))
    assert (p.timeframe, p.window_bars, p.max_missing_in_window, p.contiguous_tail) == (HOUR, 720, 7, 50)
    assert "ema-3-9-20-50-120-forex" not in {s.name for s in all_strategies()}


def test_the_fingerprint_is_literal_and_changes_with_any_parameter() -> None:
    assert fingerprint() == "834a7a0fdedbc1b8253f481d4d7e6709be6cee4ff4d0c01a1c33eec51e60d45d" != ema_cross.fingerprint()
    assert fingerprint(dataclasses.replace(EmaTrendCrossParameters(), ema_trend_long=121)) != fingerprint()
    assert fingerprint(dataclasses.replace(EmaTrendCrossParameters(), window_bars=721)) != fingerprint()
    assert run(walk(720)).parameters_sha256 == fingerprint()


@pytest.mark.parametrize(
    "changes",
    [
        {"ema_cross_fast": 9, "ema_cross_slow": 3},
        {"ema_trend_short": 50, "ema_trend_mid": 20},
        {"ema_trend_mid": 120},
        {"window_bars": 121},
        {"contiguous_tail": 0},
        {"contiguous_tail": 721},
        {"max_missing_in_window": -1},
    ],
)
def test_parameters_that_make_no_sense_are_refused(changes: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        EmaTrendCrossParameters(**changes)  # type: ignore[arg-type]


# --- the rule on its numbers ---

UPTREND, DOWNTREND = ("1.3", "1.2", "1.1"), ("1.1", "1.2", "1.3")


@pytest.mark.parametrize(
    "before_fast, before_slow, fast, slow, trend, rsi, outcome, reasons",
    [
        ("1.0", "1.1", "1.2", "1.1", UPTREND, "60", Outcome.LONG, (UP, ALIGNED, RSI_OK)),
        ("1.1", "1.1", "1.2", "1.1", UPTREND, "60", Outcome.LONG, (UP, ALIGNED, RSI_OK)),  # from exact equality
        ("1.0", "1.1", "1.10000000000000000001", "1.1", UPTREND, "50.0001", Outcome.LONG, (UP, ALIGNED, RSI_OK)),  # however small
        ("1.0", "1.1", "1.1", "1.1", UPTREND, "60", Outcome.NO_CROSS, (R.NO_CROSS,)),  # reaching equality is not a cross
        ("1.2", "1.1", "1.3", "1.1", UPTREND, "60", Outcome.NO_CROSS, (R.NO_CROSS,)),  # already above
        ("1.0", "1.1", "1.2", "1.1", UPTREND, "50", Outcome.NOT_CONFIRMED, (UP, ALIGNED, RSI_NO)),  # RSI exactly 50
        ("1.0", "1.1", "1.2", "1.1", UPTREND, "49.9999", Outcome.NOT_CONFIRMED, (UP, ALIGNED, RSI_NO)),
        ("1.0", "1.1", "1.2", "1.1", DOWNTREND, "60", Outcome.NOT_CONFIRMED, (UP, NOT_ALIGNED, RSI_OK)),  # the trend only
        ("1.0", "1.1", "1.2", "1.1", DOWNTREND, "40", Outcome.NOT_CONFIRMED, (UP, NOT_ALIGNED, RSI_NO)),
        ("1.2", "1.1", "1.0", "1.1", DOWNTREND, "40", Outcome.SHORT, (DOWN, ALIGNED, RSI_OK)),
        ("1.1", "1.1", "1.0", "1.1", DOWNTREND, "40", Outcome.SHORT, (DOWN, ALIGNED, RSI_OK)),
        ("1.2", "1.1", "1.1", "1.1", DOWNTREND, "40", Outcome.NO_CROSS, (R.NO_CROSS,)),
        ("1.2", "1.1", "1.0", "1.1", DOWNTREND, "50", Outcome.NOT_CONFIRMED, (DOWN, ALIGNED, RSI_NO)),
        ("1.2", "1.1", "1.0", "1.1", DOWNTREND, "50.0001", Outcome.NOT_CONFIRMED, (DOWN, ALIGNED, RSI_NO)),
        ("1.2", "1.1", "1.0", "1.1", DOWNTREND, "49.9999", Outcome.SHORT, (DOWN, ALIGNED, RSI_OK)),
        ("1.2", "1.1", "1.0", "1.1", UPTREND, "40", Outcome.NOT_CONFIRMED, (DOWN, NOT_ALIGNED, RSI_OK)),
        ("1.1", "1.1", "1.1", "1.1", UPTREND, "60", Outcome.NO_CROSS, (R.NO_CROSS,)),  # flat: nothing crosses
    ],
)
def test_classify(before_fast: str, before_slow: str, fast: str, slow: str, trend: tuple[str, str, str], rsi: str, outcome: Outcome,
                  reasons: tuple[Reason, ...]) -> None:
    short, mid, long_ = (D(v) for v in trend)

    assert classify(D(before_fast), D(before_slow), D(fast), D(slow), short, mid, long_, D(rsi)) == (outcome, reasons)


@pytest.mark.parametrize("order", list(permutations(("1.1", "1.2", "1.3"))) + [("1.2", "1.2", "1.1"), ("1.3", "1.2", "1.2"), ("1.2", "1.2", "1.2")])
def test_only_the_strict_order_of_the_three_trend_averages_gives_a_signal(order: tuple[str, str, str]) -> None:
    short, mid, long_ = (D(v) for v in order)
    up, _ = classify(D("1.0"), D("1.1"), D("1.2"), D("1.1"), short, mid, long_, D("60"))
    down, _ = classify(D("1.2"), D("1.1"), D("1.0"), D("1.1"), short, mid, long_, D("40"))

    assert (up is Outcome.LONG) == (order == ("1.3", "1.2", "1.1"))
    assert (down is Outcome.SHORT) == (order == ("1.1", "1.2", "1.3"))
    assert up in (Outcome.LONG, Outcome.NOT_CONFIRMED) and down in (Outcome.SHORT, Outcome.NOT_CONFIRMED)


# --- the rule on series ---


@pytest.mark.parametrize("sign, long_, short, up, down", [(1, Outcome.LONG, Outcome.SHORT, UP, DOWN), (-1, Outcome.SHORT, Outcome.LONG, DOWN, UP)])
def test_pullbacks_in_a_trend_and_a_reversal(sign: int, long_: Outcome, short: Outcome, up: Reason, down: Reason) -> None:
    bars = pullbacks(sign)
    seen = {index: at(bars, index) for index in (801, 810, 834, 847, 848, 941, 949)}

    assert {i: (e.outcome, tuple(e.reasons)) for i, e in seen.items()} == {
        801: (Outcome.NOT_CONFIRMED, (down, NOT_ALIGNED, RSI_NO)),
        810: (long_, (up, ALIGNED, RSI_OK)),
        834: (Outcome.NOT_CONFIRMED, (down, NOT_ALIGNED, RSI_NO)),
        847: (Outcome.NOT_CONFIRMED, (up, ALIGNED, RSI_NO)),
        848: (Outcome.NO_CROSS, (R.NO_CROSS,)),  # the cross of 847 is not confirmed afterwards
        941: (Outcome.NOT_CONFIRMED, (up, NOT_ALIGNED, RSI_NO)),
        949: (short, (down, ALIGNED, RSI_OK)),
    }
    assert seen[810].is_signal and seen[810].signal_id == seen[810].evaluation_key
    assert not seen[847].is_signal and seen[847].signal_id is None
    e = seen[810]
    assert e.ema_trend_short is not None and e.ema_trend_mid is not None and e.ema_trend_long is not None
    assert (e.ema_trend_short - e.ema_trend_mid) * sign > 0 and (e.ema_trend_mid - e.ema_trend_long) * sign > 0


def test_sampled_candles_of_a_long_series_match_the_independent_reference() -> None:
    bars = walk(1100)
    outcomes = set()
    for index in list(range(719, 1100, 9)) + list(range(900, 930)):
        e = at(bars, index)
        outcome, reasons, averages, rsi = ref_trend_outcome(bars[index - 719: index + 1])
        outcomes.add(outcome)

        assert (e.outcome.value, tuple(r.value for r in e.reasons)) == (outcome, reasons), index
        mine = {3: e.ema_cross_fast, 9: e.ema_cross_slow, 20: e.ema_trend_short, 50: e.ema_trend_mid, 120: e.ema_trend_long}
        for period, expected in averages.items():
            assert abs(Fraction(mine[period]) - expected) < TOLERANCE, (index, period)  # type: ignore[arg-type]
        assert e.rsi is not None and abs(Fraction(e.rsi) - rsi) < TOLERANCE
        assert e.atr is not None and abs(Fraction(e.atr) - ref_atr(bars[index - 719: index + 1], 14)) < TOLERANCE
    assert "CRUCE_SIN_CONFIRMACION" in outcomes and "SIN_CRUCE" in outcomes


def test_an_evaluation_reports_its_indicators_and_its_candle() -> None:
    bars = pullbacks()[:811]
    e = run(bars)

    assert (e.strategy_id, e.symbol, e.data_kind, e.data_source) == (STRATEGY_ID, "EURUSD", QuoteKind.SYNTHETIC, SOURCE)
    assert (e.candle_open, e.candle_close, e.as_of) == (bars[-1].timestamp, bars[-1].timestamp + HOUR, bars[-1].timestamp + HOUR)
    assert (e.bars_received, e.forming_bars_excluded, e.bars_used, e.window_start) == (811, 0, 720, bars[91].timestamp)
    assert e.last_close == bars[-1].close and e.atr is not None and e.atr > 0 and e.rsi is not None and e.rsi > 50
    assert e.previous_ema_cross_fast <= e.previous_ema_cross_slow and e.ema_cross_fast > e.ema_cross_slow  # type: ignore[operator]
    assert e.signal_id == f"ema-3-9-20-50-120-forex@0.1.0|EURUSD|{e.candle_close.isoformat()}" == e.evaluation_key


def test_this_module_only_detects_signals() -> None:
    fields = {f.name for f in dataclasses.fields(TrendCrossEvaluation)}

    assert not {"entry", "entry_price", "stop", "stop_loss", "target", "take_profit", "units", "plan"} & fields
    source = open(ema_trend_cross.__file__, encoding="utf-8").read().split('"""', 2)[2]
    assert "execution" not in source and "trade_proposal" not in source and "ema_cross " not in source and "import ema_cross" not in source


def test_the_two_strategies_never_share_a_signal_identifier() -> None:
    bars = walk(720)
    mine = run(bars)
    other = ema_cross.evaluate(bars, bars[-1].timestamp + HOUR, symbol="EURUSD", data_kind=QuoteKind.SYNTHETIC, data_source=SOURCE, is_open=always_open)

    assert mine.candle_close == other.candle_close and mine.evaluation_key != other.evaluation_key
    assert mine.evaluation_key.split("|")[1:] == other.evaluation_key.split("|")[1:]


# --- closed candles, one signal per candle ---


def test_a_forming_candle_is_never_used() -> None:
    bars = pullbacks()[:811]
    closed = run(bars)
    forming = PriceBar(bars[-1].timestamp + HOUR, bars[-1].close, D("1.20000"), D("1.00000"), D("1.00000"), 0)  # would turn everything over
    during = run(bars + [forming], forming.timestamp + timedelta(minutes=59, seconds=59))

    assert closed.outcome is Outcome.LONG
    assert during.forming_bars_excluded == 1 and during.candle_close == closed.candle_close
    assert (during.outcome, during.ema_cross_fast, during.rsi, during.atr) == (closed.outcome, closed.ema_cross_fast, closed.rsi, closed.atr)
    after = run(bars + [forming], forming.timestamp + HOUR)
    assert after.candle_open == forming.timestamp and after.forming_bars_excluded == 0


def test_the_same_candle_gives_the_same_signal_whenever_and_however_it_is_evaluated() -> None:
    bars = pullbacks()
    first = run(bars[:811])
    close = first.candle_close

    def numbers(e: TrendCrossEvaluation) -> tuple[object, ...]:
        return (e.signal_id, e.outcome, tuple(e.reasons), e.ema_cross_fast, e.ema_cross_slow, e.previous_ema_cross_fast, e.previous_ema_cross_slow,
                e.ema_trend_short, e.ema_trend_mid, e.ema_trend_long, e.rsi, e.atr, e.window_start, e.bars_used)

    later = run(bars[:811], close + timedelta(minutes=41))
    reversed_ = run(bars[:811][::-1], close)
    less_history = run(bars[91:811])
    duplicated = run(bars[:811] + bars[700:811], close)
    with_later_bars = run(bars, close)

    assert first.outcome is Outcome.LONG
    assert numbers(later) == numbers(reversed_) == numbers(less_history) == numbers(duplicated) == numbers(with_later_bars) == numbers(first)


# --- data gates ---


def test_too_little_history_is_rejected() -> None:
    bars = walk(800)

    for count in (1, 121, 719):
        e = run(bars[:count])
        assert (e.outcome, tuple(e.reasons), e.bars_used, e.ema_trend_long) == (Outcome.DATA_REJECTED, (R.INSUFFICIENT_DATA,), 0, None)
    assert run(bars[:720]).outcome is not Outcome.DATA_REJECTED


def test_data_that_is_not_recent_enough_is_rejected() -> None:
    bars = walk(800)

    e = run(bars[:-1], bars[-1].timestamp + HOUR)

    assert (e.outcome, tuple(e.reasons)) == (Outcome.DATA_REJECTED, (R.STALE_DATA,))
    assert bars[-2].timestamp.isoformat() in e.detail


def test_missing_candles_are_rejected_by_position_and_number() -> None:
    bars = walk(800)
    old = (100, 101, 200, 300, 301, 400, 500, 600)

    def without(*indexes: int) -> list[PriceBar]:
        return [b for i, b in enumerate(bars) if i not in indexes]

    seven = run(without(*old[:7]))
    eight = run(without(*old))
    recent = run(without(750))  # the last 50 are 750..799
    just_outside = run(without(749))

    assert (seven.outcome is not Outcome.DATA_REJECTED, seven.missing_in_window, seven.bars_used) == (True, 7, 713)
    assert (eight.outcome, tuple(eight.reasons)) == (Outcome.DATA_REJECTED, (R.INCOMPLETE_DATA,)) and "8" in eight.detail
    assert (recent.outcome, tuple(recent.reasons)) == (Outcome.DATA_REJECTED, (R.INCOMPLETE_DATA,))
    assert (just_outside.outcome is not Outcome.DATA_REJECTED, just_outside.missing_in_window) == (True, 1)


def test_a_missing_hour_is_never_replaced_by_an_older_candle() -> None:
    bars = walk(800)
    e = run([b for i, b in enumerate(bars) if i != 300])

    assert e.window_start == bars[80].timestamp  # 720 expected hours, not 720 available bars
    assert (e.bars_used, e.missing_in_window) == (719, 1)


def test_inconsistent_candles_are_rejected() -> None:
    bars = walk(800)
    b = bars[700]
    contradictory = PriceBar(b.timestamp, b.open, b.high + D("0.00010"), b.low, b.close, 0)
    off_grid = PriceBar(b.timestamp + timedelta(minutes=30), b.open, b.high, b.low, b.close, 0)
    exact_duplicate = run(bars + [b], bars[-1].timestamp + HOUR)
    before_the_window = PriceBar(bars[10].timestamp, bars[10].open, bars[10].high + D("0.00010"), bars[10].low, bars[10].close, 0)

    for extra in (contradictory, off_grid):
        e = run(bars + [extra], bars[-1].timestamp + HOUR)
        assert (e.outcome, tuple(e.reasons)) == (Outcome.DATA_REJECTED, (R.INCONSISTENT_DATA,))
    assert exact_duplicate.outcome is not Outcome.DATA_REJECTED and exact_duplicate.bars_used == 720
    assert run(bars + [before_the_window], bars[-1].timestamp + HOUR).outcome is not Outcome.DATA_REJECTED


def test_timestamps_need_a_time_zone() -> None:
    bars = walk(720)

    with pytest.raises(ValueError):
        run(bars, datetime(2026, 2, 4, 0, 0))


def test_a_weekend_is_a_normal_closure_and_a_missing_hour_is_an_anomalous_gap() -> None:
    """Hourly bars of the New York FX week over seven weeks: the feed skips
    the weekends, and they must not count as missing."""
    start = datetime(2026, 1, 4, 22, 0, tzinfo=UTC)  # Sunday 17:00 New York
    hours = [start + i * HOUR for i in range(7 * 24 * 7)]
    labels = [h for h in hours if is_week_open(h) and is_week_open(h + HOUR - timedelta(microseconds=1))]
    bars = [PriceBar(label, b.open, b.high, b.low, b.close, 0) for label, b in zip(labels, walk(len(labels)))]
    assert len(bars) >= 800

    e = run(bars, is_open=is_week_open)
    holed = run(bars[:400] + bars[401:], is_open=is_week_open)
    saturday = run(bars, datetime(2026, 2, 14, 12, 0, tzinfo=UTC), is_open=is_week_open)

    assert e.outcome is not Outcome.DATA_REJECTED
    assert (e.bars_used, e.missing_in_window, e.market_closures_in_window) == (720, 0, 5)  # six weeks of 120 hours
    assert (holed.bars_used, holed.missing_in_window, holed.market_closures_in_window) == (719, 1, 5)
    assert (saturday.outcome, tuple(saturday.reasons)) == (Outcome.DATA_REJECTED, (R.MARKET_CLOSED,))


def test_the_kind_of_the_prices_travels_with_the_evaluation() -> None:
    bars = walk(720)
    e = evaluate(bars, bars[-1].timestamp + HOUR, symbol="GBPUSD", data_kind=QuoteKind.ESTIMATED, data_source="Yahoo Finance H1", is_open=always_open)

    assert (e.data_kind, e.data_source, e.symbol) == (QuoteKind.ESTIMATED, "Yahoo Finance H1", "GBPUSD")


# --- the 720-hour window and the seed of EMA120 ---


def test_the_seed_of_ema120_weighs_little_in_720_hours_and_much_in_300() -> None:
    """The window fixes where EMA120 starts. Against the same average run
    over 1 700 bars: with 720 bars the seed keeps (119/121)^600, about
    4.5e-5, of its weight; with 300 bars it keeps (119/121)^180, about 5 %,
    a thousand times more. (A perfectly steady trend hides the effect: the
    seed, a simple mean, then lags exactly as much as the average itself.)"""
    bars = walk(1700)
    closes = [b.close for b in bars]
    long_history, in_300 = ema(closes, 120), ema(closes[-300:], 120)
    e = run(bars)
    assert long_history is not None and in_300 is not None and e.ema_trend_long is not None

    assert abs((Fraction(119, 121) ** 600) - Fraction(45, 10**6)) < Fraction(1, 10**6)
    assert Fraction(4, 100) < Fraction(119, 121) ** 180 < Fraction(6, 100)
    assert e.ema_trend_long == ema(closes[-720:], 120)
    assert abs(e.ema_trend_long - long_history) < D("0.00000001")  # a thousandth of a tick
    assert abs(in_300 - long_history) > D("0.000001")
    assert abs(in_300 - long_history) > 500 * abs(e.ema_trend_long - long_history)
    for period, value in ((3, e.ema_cross_fast), (9, e.ema_cross_slow)):  # the crossing pair does not depend on the window at all
        assert abs(value - ema(closes, period)) < D("1e-24")  # type: ignore[operator]


def test_the_previous_candle_is_read_from_the_same_window() -> None:
    bars = pullbacks()
    e = at(bars, 810)
    window = [b.close for b in bars[91:811]]

    assert e.previous_ema_cross_fast == ema(window[:-1], 3) and e.previous_ema_cross_slow == ema(window[:-1], 9)
    assert e.ema_cross_fast == ema(window, 3) and e.ema_trend_long == ema(window, 120)
