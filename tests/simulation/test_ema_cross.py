"""Tests for simulation.ema_cross: signal detection of ema-rsi-atr-forex.

Synthetic bars only (QuoteKind.SYNTHETIC). Expected outcomes come from an
independent exact-fraction reference (tests/simulation/_ema_cross_support.py)
and from series built so that a specific case happens at a known bar."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from typing import Sequence

import pytest

from investment_committee.domain import PriceBar
from investment_committee.market_intelligence.aggregation import aggregate, to_price_bars
from investment_committee.market_intelligence.quotes import Quote, QuoteKind
from investment_committee.simulation import ema_cross
from investment_committee.simulation.ema_cross import (
    STRATEGY_ID,
    EmaCrossParameters,
    Outcome,
    Reason,
    SignalEvaluation,
    classify_cross,
    evaluate,
    fingerprint,
)
from investment_committee.simulation.sessions import is_week_open
from investment_committee.simulation.strategies import all_strategies

from ._ema_cross_support import HOUR, SOURCE, START, always_open, closes_to_bars, ref_atr, ref_outcome, walk

UTC = timezone.utc
TOLERANCE = Fraction(1, 10**18)


def run(bars: Sequence[PriceBar], as_of: datetime | None = None, **kwargs) -> SignalEvaluation:  # type: ignore[no-untyped-def]
    kwargs.setdefault("is_open", always_open)
    return evaluate(bars, as_of or bars[-1].timestamp + HOUR, symbol="EURUSD", data_kind=QuoteKind.SYNTHETIC, data_source=SOURCE, **kwargs)


def whipsaw(sign: int = 1) -> list[PriceBar]:
    """330 flat bars, then (in tenths of a pip, times `sign`): +1191, 30 flat
    bars, -1180, 4 flat bars, +718, 3 flat bars. With sign = +1:
      bar 331  EMA20 rises above EMA50 from exact equality, RSI 100 -> LONG
      bar 366  EMA20 falls below EMA50, RSI 9.2                     -> SHORT
      bar 367  EMA20 rises above EMA50 again by 0.1 pip, but RSI14 is 49.57:
               the last 14 bars lost more than they gained -> not confirmed
    and the mirror image with sign = -1."""
    moves = [0] * 330 + [sign * 1191] + [0] * 30 + [-sign * 1180] + [0] * 4 + [sign * 718] + [0] * 3
    level, closes = 110000, []
    for move in moves:
        level += move
        closes.append(f"{level / 100000:.5f}")
    return closes_to_bars(closes)


# --- identity and parameters ---


def test_the_strategy_has_its_own_identity_and_is_not_among_the_frozen_ones() -> None:
    p = EmaCrossParameters()

    assert STRATEGY_ID == "ema-rsi-atr-forex@0.1.0"
    assert (p.ema_fast, p.ema_slow, p.rsi_period, p.atr_period, p.rsi_threshold) == (20, 50, 14, 14, Decimal("50"))
    assert (p.timeframe, p.window_bars, p.max_missing_in_window, p.contiguous_tail) == (HOUR, 300, 3, 50)
    assert "ema-rsi-atr-forex" not in {s.name for s in all_strategies()}  # the earlier protocol's monitor never sees it


def test_the_fingerprint_changes_with_any_parameter() -> None:
    assert len(fingerprint()) == 64 and fingerprint() == fingerprint(EmaCrossParameters())
    assert fingerprint(dataclasses.replace(EmaCrossParameters(), ema_fast=21)) != fingerprint()
    assert run(walk(300)).parameters_sha256 == fingerprint()
    with pytest.raises(ValueError):
        EmaCrossParameters(ema_fast=50, ema_slow=20)
    with pytest.raises(ValueError):
        EmaCrossParameters(window_bars=40)


# --- the rule ---


@pytest.mark.parametrize(
    ("before_fast", "before_slow", "fast", "slow", "rsi", "outcome", "reasons"),
    [
        ("1.0990", "1.1000", "1.1001", "1.1000", "60", Outcome.LONG, (Reason.CROSS_UP, Reason.RSI_CONFIRMS)),
        ("1.1000", "1.1000", "1.1001", "1.1000", "50.01", Outcome.LONG, (Reason.CROSS_UP, Reason.RSI_CONFIRMS)),  # equal before counts
        ("1.0990", "1.1000", "1.1001", "1.1000", "50", Outcome.NOT_CONFIRMED, (Reason.CROSS_UP, Reason.RSI_DOES_NOT_CONFIRM)),  # exactly 50
        ("1.0990", "1.1000", "1.1001", "1.1000", "42", Outcome.NOT_CONFIRMED, (Reason.CROSS_UP, Reason.RSI_DOES_NOT_CONFIRM)),
        ("1.1010", "1.1000", "1.0999", "1.1000", "40", Outcome.SHORT, (Reason.CROSS_DOWN, Reason.RSI_CONFIRMS)),
        ("1.1000", "1.1000", "1.0999", "1.1000", "49.99", Outcome.SHORT, (Reason.CROSS_DOWN, Reason.RSI_CONFIRMS)),
        ("1.1010", "1.1000", "1.0999", "1.1000", "50", Outcome.NOT_CONFIRMED, (Reason.CROSS_DOWN, Reason.RSI_DOES_NOT_CONFIRM)),
        ("1.1010", "1.1000", "1.0999", "1.1000", "58", Outcome.NOT_CONFIRMED, (Reason.CROSS_DOWN, Reason.RSI_DOES_NOT_CONFIRM)),
        ("1.0990", "1.1000", "1.1000", "1.1000", "70", Outcome.NO_CROSS, (Reason.NO_CROSS,)),  # touches, does not cross
        ("1.0990", "1.1000", "1.09999", "1.1000", "70", Outcome.NO_CROSS, (Reason.NO_CROSS,)),  # comes close
        ("1.1010", "1.1000", "1.1000", "1.1000", "30", Outcome.NO_CROSS, (Reason.NO_CROSS,)),
        ("1.1010", "1.1000", "1.1020", "1.1000", "70", Outcome.NO_CROSS, (Reason.NO_CROSS,)),  # already above: no new signal
        ("1.0990", "1.1000", "1.0980", "1.1000", "30", Outcome.NO_CROSS, (Reason.NO_CROSS,)),  # already below
        ("1.1000", "1.1000", "1.1000", "1.1000", "50", Outcome.NO_CROSS, (Reason.NO_CROSS,)),
    ],
)
def test_classify_cross(before_fast: str, before_slow: str, fast: str, slow: str, rsi: str, outcome: Outcome, reasons: tuple[Reason, ...]) -> None:
    assert classify_cross(Decimal(before_fast), Decimal(before_slow), Decimal(fast), Decimal(slow), Decimal(rsi)) == (outcome, reasons)


# --- confirmed and unconfirmed crosses on whole series ---


def test_confirmed_and_unconfirmed_crosses_upwards_first() -> None:
    bars = whipsaw()

    quiet, long_, short, unconfirmed, after = (run(bars[:n]) for n in (330, 331, 366, 367, 368))

    assert quiet.outcome is Outcome.NO_CROSS and quiet.ema_fast == quiet.ema_slow == Decimal("1.10000") and quiet.rsi == Decimal("50")
    assert long_.outcome is Outcome.LONG and long_.reasons == (Reason.CROSS_UP, Reason.RSI_CONFIRMS)
    assert long_.previous_ema_fast == long_.previous_ema_slow and long_.ema_fast > long_.ema_slow and long_.rsi == Decimal("100")  # type: ignore[operator]
    assert short.outcome is Outcome.SHORT and short.reasons == (Reason.CROSS_DOWN, Reason.RSI_CONFIRMS) and short.rsi < 10  # type: ignore[operator]
    assert unconfirmed.outcome is Outcome.NOT_CONFIRMED and unconfirmed.reasons == (Reason.CROSS_UP, Reason.RSI_DOES_NOT_CONFIRM)
    assert unconfirmed.ema_fast > unconfirmed.ema_slow and Decimal("49") < unconfirmed.rsi < Decimal("50")  # type: ignore[operator]
    assert unconfirmed.signal_id is None and not unconfirmed.is_signal
    assert after.outcome is Outcome.NO_CROSS  # an unconfirmed cross is not confirmed by a later candle


def test_confirmed_and_unconfirmed_crosses_downwards_first() -> None:
    bars = whipsaw(-1)

    short, long_, unconfirmed = (run(bars[:n]) for n in (331, 366, 367))

    assert short.outcome is Outcome.SHORT and short.rsi == Decimal("0")
    assert long_.outcome is Outcome.LONG and long_.rsi > 90  # type: ignore[operator]
    assert unconfirmed.outcome is Outcome.NOT_CONFIRMED and unconfirmed.reasons == (Reason.CROSS_DOWN, Reason.RSI_DOES_NOT_CONFIRM)
    assert unconfirmed.ema_fast < unconfirmed.ema_slow and Decimal("50") < unconfirmed.rsi < Decimal("51")  # type: ignore[operator]


def test_every_candle_of_a_long_series_matches_the_independent_reference() -> None:
    bars = walk(700)
    outcomes: dict[str, int] = {}

    for n in range(300, 701):
        evaluation = run(bars[:n])
        expected, fast, slow, rsi = ref_outcome(bars[n - 300:n])
        assert evaluation.outcome.value == expected, n
        outcomes[expected] = outcomes.get(expected, 0) + 1
        if evaluation.is_signal:
            assert abs(Fraction(evaluation.ema_fast) - fast) < TOLERANCE and abs(Fraction(evaluation.ema_slow) - slow) < TOLERANCE  # type: ignore[arg-type]
            assert abs(Fraction(evaluation.rsi) - rsi) < TOLERANCE  # type: ignore[arg-type]
            assert abs(Fraction(evaluation.atr) - ref_atr(bars[n - 300:n], 14)) < TOLERANCE  # type: ignore[arg-type]
            assert run(bars[:n + 1]).outcome is not evaluation.outcome  # the next candle does not repeat it

    assert outcomes["LONG"] >= 1 and outcomes["SHORT"] >= 1 and outcomes["SIN_CRUCE"] > 350


def test_an_evaluation_reports_its_indicators_and_its_candle() -> None:
    bars = whipsaw()[:331]

    e = run(bars)

    assert (e.strategy_id, e.symbol, e.data_kind, e.data_source) == (STRATEGY_ID, "EURUSD", QuoteKind.SYNTHETIC, SOURCE)
    assert (e.candle_open, e.candle_close, e.as_of) == (bars[-1].timestamp, bars[-1].timestamp + HOUR, bars[-1].timestamp + HOUR)
    assert (e.bars_received, e.forming_bars_excluded, e.bars_used, e.window_start) == (331, 0, 300, bars[31].timestamp)
    assert e.last_close == Decimal("1.11191") and e.atr is not None and e.atr > 0
    assert e.signal_id == f"ema-rsi-atr-forex@0.1.0|EURUSD|{e.candle_close.isoformat()}" == e.evaluation_key


def test_this_module_only_detects_signals() -> None:
    fields = {f.name for f in dataclasses.fields(SignalEvaluation)}

    assert not {"entry", "entry_price", "stop", "stop_loss", "target", "take_profit", "units", "plan"} & fields
    source = open(ema_cross.__file__, encoding="utf-8").read()
    assert "execution" not in source.split('"""', 2)[2] and "import _plan" not in source


# --- closed candles, one signal per candle ---


def test_a_forming_candle_is_never_used() -> None:
    bars = whipsaw()[:331]
    forming = PriceBar(bars[-1].timestamp + HOUR, Decimal("1.11191"), Decimal("1.50000"), Decimal("0.90000"), Decimal("0.95000"), 0)
    at = bars[-1].timestamp + HOUR + timedelta(minutes=25)  # 25 minutes into the forming candle

    alone, with_forming = run(bars, at), run([*bars, forming], at)

    assert with_forming.forming_bars_excluded == 1 and with_forming.bars_received == 332
    assert dataclasses.replace(with_forming, forming_bars_excluded=0, bars_received=331) == alone
    assert with_forming.outcome is Outcome.LONG and with_forming.candle_open == bars[-1].timestamp


def test_the_same_candle_gives_the_same_signal_whenever_and_however_it_is_evaluated() -> None:
    bars = whipsaw()[:331]
    closed_at = bars[-1].timestamp + HOUR

    on_time, later, restarted, exact_window, shuffled = (
        run(bars, closed_at), run(bars, closed_at + timedelta(minutes=59, seconds=59)), run(list(bars), closed_at + timedelta(minutes=7)),
        run(bars[-300:], closed_at), run([*bars[150:], *bars[:150]], closed_at),
    )

    ids = {e.signal_id for e in (on_time, later, restarted, exact_window, shuffled)}
    assert ids == {f"ema-rsi-atr-forex@0.1.0|EURUSD|{closed_at.isoformat()}"}
    for other in (later, restarted, exact_window, shuffled):  # more history, another order: the 300-bar window is the same
        assert (other.outcome, other.ema_fast, other.ema_slow, other.rsi, other.atr) == (on_time.outcome, on_time.ema_fast, on_time.ema_slow, on_time.rsi, on_time.atr)
    assert run(bars, closed_at + HOUR).outcome is Outcome.DATA_REJECTED  # an hour later it is another candle, and that one is missing


def test_the_previous_candle_is_read_from_the_same_window() -> None:
    """EMA50 of one candle, seen as 'current' at its close and as 'previous'
    an hour later, comes from windows one bar apart: not identical, but the
    difference is far below a tenth of a pip."""
    bars = walk(301)

    first, second = run(bars[:300]), run(bars)

    assert first.ema_slow != second.previous_ema_slow
    assert abs(first.ema_slow - second.previous_ema_slow) < Decimal("0.000001")  # type: ignore[operator]
    assert abs(first.ema_fast - second.previous_ema_fast) < Decimal("1e-12")  # type: ignore[operator]


# --- rejected data ---


def rejected(evaluation: SignalEvaluation, reason: Reason) -> bool:
    return (evaluation.outcome is Outcome.DATA_REJECTED and evaluation.reasons == (reason,) and evaluation.signal_id is None
            and evaluation.ema_fast is None and evaluation.rsi is None and evaluation.bars_used == 0 and evaluation.detail != "")


def test_too_little_history_is_rejected() -> None:
    bars = walk(320)

    assert rejected(run(bars[:299]), Reason.INSUFFICIENT_DATA) and "299" in run(bars[:299]).detail
    assert rejected(run([], START + HOUR), Reason.INSUFFICIENT_DATA)
    assert run(bars[:300]).outcome is not Outcome.DATA_REJECTED


def test_data_that_is_not_recent_enough_is_rejected() -> None:
    bars = walk(320)

    late = run(bars, bars[-1].timestamp + 2 * HOUR)  # the candle that closed an hour ago never arrived

    assert rejected(late, Reason.STALE_DATA) and late.candle_open == bars[-1].timestamp + HOUR
    assert bars[-1].timestamp.isoformat() in late.detail


def test_missing_candles_are_rejected_by_position_and_number() -> None:
    bars = walk(330)

    def without(*positions: int) -> list[PriceBar]:
        return [bar for index, bar in enumerate(bars) if index not in positions]

    assert rejected(run(without(320)), Reason.INCOMPLETE_DATA)  # one missing among the last 50
    assert run(without(100, 150, 200)).outcome is not Outcome.DATA_REJECTED  # three, all older: tolerated
    assert rejected(run(without(100, 150, 200, 250)), Reason.INCOMPLETE_DATA)  # four are too many
    assert run(without(5)).outcome is not Outcome.DATA_REJECTED  # before the window: irrelevant


def test_inconsistent_candles_are_rejected() -> None:
    bars = walk(330)
    other_price = dataclasses.replace(bars[200], close=bars[200].close + Decimal("0.0001"), high=bars[200].high + Decimal("0.0001"))
    off_grid = dataclasses.replace(bars[210], timestamp=bars[210].timestamp + timedelta(minutes=20))
    worthless = PriceBar(bars[220].timestamp, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), 0)
    old_conflict = dataclasses.replace(bars[3], close=bars[3].close + Decimal("0.0001"), high=bars[3].high + Decimal("0.0001"))

    at = bars[-1].timestamp + HOUR

    assert rejected(run([*bars, other_price], at), Reason.INCONSISTENT_DATA)  # two different candles for one hour
    assert rejected(run([*bars, off_grid], at), Reason.INCONSISTENT_DATA)
    assert rejected(run([*bars[:220], worthless, *bars[221:]], at), Reason.INCONSISTENT_DATA)
    assert run([*bars, bars[200]], at).outcome is not Outcome.DATA_REJECTED  # the very same candle twice is harmless
    assert run([*bars, old_conflict], at).outcome is not Outcome.DATA_REJECTED  # before the window: irrelevant
    assert "DUPLICADO_CONTRADICTORIO" in run([*bars, other_price], at).detail and "FUERA_DE_REJILLA" in run([*bars, off_grid], at).detail


def test_timestamps_need_a_time_zone_and_are_compared_as_instants() -> None:
    bars = walk(300)
    bogota = timezone(timedelta(hours=-5))
    relabelled = [dataclasses.replace(bar, timestamp=bar.timestamp.astimezone(bogota)) for bar in bars]

    assert run(relabelled, (bars[-1].timestamp + HOUR).astimezone(bogota)) == run(bars)
    with pytest.raises(ValueError, match="zona horaria"):
        run(bars, datetime(2026, 1, 17, 12, 0))


# --- market hours ---


def on_calendar(count: int, last_open: datetime) -> list[PriceBar]:
    """`count` synthetic bars on the FX-week grid, the last one labelled `last_open`."""
    labels, moment = [], last_open
    while len(labels) < count:
        if is_week_open(moment) and is_week_open(moment + HOUR - timedelta(microseconds=1)):
            labels.append(moment)
        moment -= HOUR
    return [dataclasses.replace(bar, timestamp=label) for bar, label in zip(walk(count), reversed(labels))]


def test_a_weekend_inside_the_window_is_not_a_gap() -> None:
    monday_morning = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
    bars = on_calendar(300, monday_morning - HOUR)

    evaluation = run(bars, monday_morning, is_open=is_week_open)

    assert evaluation.outcome is not Outcome.DATA_REJECTED and evaluation.bars_used == 300
    assert (bars[-1].timestamp - bars[0].timestamp) > timedelta(days=14)  # the window crosses weekends


def test_no_evaluation_while_the_market_is_closed() -> None:
    saturday = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
    bars = on_calendar(300, datetime(2026, 11, 13, 21, 0, tzinfo=UTC))  # up to the Friday close

    assert rejected(run(bars, saturday, is_open=is_week_open), Reason.MARKET_CLOSED)


def test_the_weekly_close_follows_new_york_daylight_saving() -> None:
    """Friday 21:00-22:00 UTC is after the close while New York is on summer
    time (close at 21:00 UTC) and the last open hour once it is on winter
    time (close at 22:00 UTC). In 2026 the clocks go back on 1 November."""
    summer_friday, winter_friday = datetime(2026, 10, 30, 22, 0, tzinfo=UTC), datetime(2026, 11, 6, 22, 0, tzinfo=UTC)

    summer = run(on_calendar(300, summer_friday - 2 * HOUR), summer_friday, is_open=is_week_open)
    winter = run(on_calendar(300, winter_friday - HOUR), winter_friday, is_open=is_week_open)

    assert rejected(summer, Reason.MARKET_CLOSED)
    assert winter.outcome is not Outcome.DATA_REJECTED and winter.candle_open == datetime(2026, 11, 6, 21, 0, tzinfo=UTC)


# --- with the data layer ---


def test_bars_built_from_quotes_keep_their_kind_up_to_the_signal() -> None:
    quotes = []
    for bar in whipsaw()[:331]:  # four synthetic quotes an hour, the last at the bar's close, 1 pip wide
        for minute, mid in ((0, bar.open), (20, bar.high), (40, bar.low), (59, bar.close)):
            quotes.append(Quote("EURUSD", bar.timestamp + timedelta(minutes=minute), mid - Decimal("0.00005"), mid + Decimal("0.00005"), QuoteKind.SYNTHETIC, SOURCE))
    built = aggregate(quotes, HOUR)

    evaluation = evaluate(to_price_bars(built, "mid"), built[-1].timestamp + HOUR, symbol="EURUSD", data_kind=built[-1].kind,
                          data_source=SOURCE, is_open=always_open)

    assert evaluation.outcome is Outcome.LONG and evaluation.data_kind is QuoteKind.SYNTHETIC
    assert evaluation.last_close == Decimal("1.11191") and evaluation.bars_used == 300


# --- continuity is established before the window is built ---


def test_the_window_is_300_expected_hours_and_never_reaches_back_past_a_hole() -> None:
    bars = walk(330)
    holes = [bar for index, bar in enumerate(bars) if index not in (100, 150, 200)]

    complete, with_holes = run(bars), run(holes)

    assert (complete.bars_used, complete.missing_in_window, complete.window_start) == (300, 0, bars[30].timestamp)
    assert (with_holes.bars_used, with_holes.missing_in_window) == (297, 3)
    assert with_holes.window_start == complete.window_start  # not extended to bars[27] to make up 300 bars
    assert with_holes.outcome is not Outcome.DATA_REJECTED and with_holes.candle_open == bars[-1].timestamp


def test_the_two_candles_of_a_cross_are_always_consecutive_market_hours() -> None:
    bars = whipsaw()[:331]  # the last candle crosses upwards
    without_previous = [*bars[:-2], bars[-1]]

    evaluation = run(without_previous)

    assert run(bars).outcome is Outcome.LONG
    assert rejected(evaluation, Reason.INCOMPLETE_DATA) and bars[-2].timestamp.isoformat() in evaluation.detail


def test_a_weekend_is_a_normal_closure_and_a_missing_hour_is_an_anomalous_gap() -> None:
    monday_morning = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
    bars = on_calendar(300, monday_morning - HOUR)
    friday_last, sunday_first = datetime(2026, 11, 13, 21, 0, tzinfo=UTC), datetime(2026, 11, 15, 22, 0, tzinfo=UTC)

    whole = run(bars, monday_morning, is_open=is_week_open)
    no_sunday_open = run([b for b in bars if b.timestamp != sunday_first], monday_morning, is_open=is_week_open)

    assert {friday_last, sunday_first} <= {b.timestamp for b in bars}  # neighbours across the weekend
    assert (whole.missing_in_window, whole.market_closures_in_window, whole.bars_used) == (0, 3, 300)  # 11 + 120 + 120 + 49 hours: three weekends
    assert rejected(no_sunday_open, Reason.INCOMPLETE_DATA) and sunday_first.isoformat() in no_sunday_open.detail


def test_history_that_does_not_reach_the_window_start_is_insufficient_not_incomplete() -> None:
    bars = walk(330)

    assert rejected(run(bars[31:]), Reason.INSUFFICIENT_DATA)  # 299 hours of history
    assert run(bars[30:]).outcome is not Outcome.DATA_REJECTED
