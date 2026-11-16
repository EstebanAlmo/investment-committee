"""Tests of simulation.forex_backtest (engine ema-forex-backtest@1.0.0) on
made-up bars with results computed by hand (_forex_backtest_support.py).
No historical data is read here."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from investment_committee.market_intelligence.quotes import QuoteKind
from investment_committee.simulation import ema_cross, ema_trend_cross
from investment_committee.simulation.forex_backtest import (
    BASE,
    COSTS_X1_5,
    COSTS_X2,
    DATA_HOLE,
    ENGINE_ID,
    ILLUSTRATIVE_FINANCING,
    OPTIMISTIC,
    SAME_BAR,
    STRATEGY_A,
    STRATEGY_B,
    TradeStatus,
    evaluate_signals,
    hypothetical_commission,
    run,
    run_account,
)
from investment_committee.simulation.opportunities import RiskLimits
from investment_committee.simulation.signals import Outcome
from investment_committee.simulation.trade_proposal import ExitReason, FinancingStatus, Rejection

from ._ema_cross_support import SOURCE, always_open, walk
from ._forex_backtest_support import GBP_MID, HOUR, MID, SHA, STRATEGY, T0, UTC, D, bar, play, sig, tape
from .test_ema_trend_cross import pullbacks

LONG, SHORT = Outcome.LONG, Outcome.SHORT
QUIET = ("1.08495", "1.08505", "1.08485", "1.08495")


def only(run_):  # type: ignore[no-untyped-def]
    assert len(run_.trades) == 1
    return run_.trades[0]


# --- timestamps and entry ---


def test_a_bar_is_labelled_by_its_open_and_the_entry_is_the_open_of_the_bar_after_the_signal_candle() -> None:
    """The signal candle is the bar labelled T0 - 1h, closed at T0. The first
    price after that close is the OPEN of the bar labelled T0: 1.08505 here
    (bid 1.08500, ask 1.08510, entry 1.08512), not the close 1.08495."""
    trade = only(play([sig(0)], tape({0: ("1.08505", "1.08515", "1.08495", "1.08505")})))

    assert (trade.signal_candle_close_at, trade.quote_available_at, trade.entry_at) == (T0, T0, T0)
    assert (trade.proposal.bid, trade.proposal.ask, trade.proposal.entry) == (D("1.08500"), D("1.08510"), D("1.08512"))
    assert trade.proposal.quote_time == T0 and trade.proposal.data_kind is QuoteKind.SYNTHETIC


def test_the_high_low_and_close_of_the_entry_bar_do_not_move_the_entry() -> None:
    quiet = only(play([sig(0)], tape()))
    busy = only(play([sig(0)], tape({0: ("1.08495", "1.08900", "1.08400", "1.08880")})))

    assert quiet.proposal == busy.proposal and quiet.proposal.entry == D("1.08502")
    assert (quiet.proposal.stop, quiet.proposal.target, quiet.proposal.units) == (D("1.08352"), D("1.08958"), 32000)


def test_without_the_next_bar_there_is_no_entry_and_the_signal_is_not_retried() -> None:
    result = play([sig(0)], tape(missing=[0]))

    assert not result.trades and len(result.decisions) == 1
    decision = result.decisions[0]
    assert decision.proposal.rejections == (Rejection.NO_QUOTE,) and decision.quote_available_at is None and decision.at == T0


def test_an_open_too_far_from_the_signal_candles_close_is_rejected() -> None:
    result = play([sig(0)], tape({0: ("1.08521", "1.08530", "1.08510", "1.08521")}))  # 2.6 pips away; the limit is 0.25 ATR = 2.5

    assert not result.trades and result.decisions[0].proposal.rejections == (Rejection.PRICE_MOVED,)


def test_a_signal_keyed_at_another_instant_than_its_candle_close_is_refused() -> None:
    with pytest.raises(ValueError):
        run_account(STRATEGY, SHA, {"EURUSD": {T0 + HOUR: sig(0)}}, {"EURUSD": tape()}, start=T0, end=T0 + 5 * HOUR)


# --- stop and target, LONG and SHORT ---


@pytest.mark.parametrize("outcome, touched, reason, pnl, fill", [
    (LONG, ("1.08495", "1.08505", "1.08357", "1.08400"), ExitReason.STOP, "-48.64", "1.08350"),
    (LONG, ("1.08495", "1.08963", "1.08485", "1.08900"), ExitReason.TARGET, "145.92", "1.08958"),
    (SHORT, ("1.08495", "1.08633", "1.08485", "1.08600"), ExitReason.STOP, "-48.64", "1.08640"),
    (SHORT, ("1.08495", "1.08505", "1.08027", "1.08100"), ExitReason.TARGET, "145.92", "1.08032"),
])
def test_a_level_touched_exactly_on_the_exit_side_closes_the_trade_at_the_bars_close(outcome, touched, reason, pnl, fill) -> None:  # type: ignore[no-untyped-def]
    trade = only(play([sig(0, outcome)], tape({3: touched})))

    assert trade.status is TradeStatus.CLOSED and trade.result.reason is reason and not trade.result.gap
    assert (trade.result.exit_price, trade.result.net_pnl, trade.booked_pnl) == (D(fill), D(pnl), D(pnl))
    assert (trade.exit_at, trade.recorded_at, trade.bars_held) == (T0 + 4 * HOUR, T0 + 4 * HOUR, 4)
    assert trade.result.r_multiple == (D("-1.0000") if reason is ExitReason.STOP else D("3.0000"))


@pytest.mark.parametrize("outcome, almost", [
    (LONG, ("1.08495", "1.08962", "1.08358", "1.08495")),  # one tick short of both levels
    (SHORT, ("1.08495", "1.08632", "1.08028", "1.08495")),
])
def test_one_tick_short_of_a_level_does_not_trigger(outcome, almost) -> None:  # type: ignore[no-untyped-def]
    trade = only(play([sig(0, outcome)], tape({3: almost}), last=10))

    assert trade.status is TradeStatus.OPEN_AT_END


@pytest.mark.parametrize("entry_bar, reason, pnl", [
    (("1.08495", "1.08505", "1.08357", "1.08400"), ExitReason.STOP, "-48.64"),
    (("1.08495", "1.08963", "1.08485", "1.08900"), ExitReason.TARGET, "145.92"),
])
def test_a_level_touched_inside_the_entry_bar_counts_because_the_open_came_first(entry_bar, reason, pnl) -> None:  # type: ignore[no-untyped-def]
    trade = only(play([sig(0)], tape({0: entry_bar})))

    assert trade.result.reason is reason and trade.result.net_pnl == D(pnl)
    assert (trade.entry_at, trade.exit_at, trade.bars_held) == (T0, T0 + HOUR, 1)


# --- gaps ---


def test_an_open_beyond_the_stop_fills_at_that_open_and_loses_more_than_the_budget() -> None:
    """Open 1.08205 -> bid 1.08200 -> fill 1.08198: 32 000 x (1.08198 - 1.08502) = -97.28."""
    trade = only(play([sig(0)], tape({3: ("1.08205", "1.09100", "1.08195", "1.09000")})))  # the later high reaches the target: irrelevant

    assert trade.result.reason is ExitReason.STOP and trade.result.gap and trade.result.exit_price == D("1.08198")
    assert trade.result.net_pnl == D("-97.28") and trade.result.exceeded_budget and trade.result.loss_beyond_estimate == D("48.64")
    assert (trade.exit_at, trade.recorded_at, trade.exit_price_source, trade.bars_held) == (T0 + 3 * HOUR, T0 + 3 * HOUR, "APERTURA", 3)


def test_an_open_beyond_the_target_fills_at_the_target_without_improvement() -> None:
    trade = only(play([sig(0)], tape({3: ("1.09005", "1.09015", "1.08300", "1.08400")})))  # the later low reaches the stop: irrelevant

    assert trade.result.reason is ExitReason.TARGET and trade.result.exit_price == D("1.08958") and trade.result.net_pnl == D("145.92")
    assert trade.exit_at == T0 + 3 * HOUR and trade.exit_price_source == "NIVEL"


def test_a_short_gapped_through_its_stop_fills_at_the_ask_of_that_open() -> None:
    """Open 1.08795 -> ask 1.08800 -> fill 1.08802: 32 000 x (1.08488 - 1.08802) = -100.48."""
    trade = only(play([sig(0, SHORT)], tape({3: ("1.08795", "1.08805", "1.08785", "1.08795")})))

    assert trade.result.gap and trade.result.exit_price == D("1.08802") and trade.result.net_pnl == D("-100.48")


# --- stop and target inside one bar ---


@pytest.mark.parametrize("offset", [0, 3])
def test_stop_and_target_inside_one_bar_is_indeterminate_and_the_account_books_the_stop(offset: int) -> None:
    result = play([sig(0)], tape({offset: ("1.08495", "1.08963", "1.08357", "1.08495")}))
    trade = only(result)

    assert trade.status is TradeStatus.INDETERMINATE and trade.result is None and trade.exit_at is None
    assert not trade.indeterminate.valid_for_metrics
    assert (trade.indeterminate.conservative.net_pnl, trade.indeterminate.optimistic.net_pnl) == (D("-48.64"), D("145.92"))
    assert trade.booked_pnl == D("-48.64") and result.final_balance == D("9951.36")
    u = trade.uncertainty
    assert (u.kind, u.start, u.end, u.unobserved_hours) == (SAME_BAR, T0 + offset * HOUR, T0 + (offset + 1) * HOUR, ())
    assert trade.recorded_at == T0 + (offset + 1) * HOUR


def test_under_the_optimistic_policy_the_same_trade_books_the_target_in_a_separate_account() -> None:
    bars = tape({0: ("1.08495", "1.08963", "1.08357", "1.08495")})
    main, optimistic = play([sig(0)], bars), play([sig(0)], bars, scenario=OPTIMISTIC)

    assert (main.final_balance, optimistic.final_balance) == (D("9951.36"), D("10145.92"))
    assert optimistic.trades[0].status is TradeStatus.INDETERMINATE and optimistic.trades[0].booked_pnl == D("145.92")
    assert play([sig(0)], bars) == main  # the conservative account is untouched by the other run


# --- data holes while a position is open ---


def test_a_missing_hour_makes_the_trade_indeterminate_when_prices_return() -> None:
    result = play([sig(0)], tape(missing=[2]))
    trade = only(result)

    assert trade.status is TradeStatus.INDETERMINATE and trade.exit_at is None and trade.recorded_at == T0 + 3 * HOUR
    u = trade.uncertainty
    assert (u.kind, u.start, u.end, u.unobserved_hours) == (DATA_HOLE, T0 + 2 * HOUR, T0 + 3 * HOUR, (T0 + 2 * HOUR,))
    assert (u.last_price_before, u.last_price_before_at, u.first_price_after) == (MID, T0 + 2 * HOUR, MID)
    assert (trade.indeterminate.conservative.net_pnl, trade.indeterminate.optimistic.net_pnl) == (D("-48.64"), D("145.92"))
    assert not trade.indeterminate.conservative.gap and result.final_balance == D("9951.36")


def test_a_hole_that_ends_beyond_the_stop_books_the_gap_as_the_conservative_result() -> None:
    trade = only(play([sig(0)], tape({4: ("1.08205", "1.08215", "1.08195", "1.08205")}, missing=[2, 3])))

    assert trade.uncertainty.unobserved_hours == (T0 + 2 * HOUR, T0 + 3 * HOUR) and trade.uncertainty.first_price_after == D("1.08205")
    assert trade.indeterminate.conservative.gap and trade.booked_pnl == D("-97.28") and trade.indeterminate.optimistic.net_pnl == D("145.92")
    assert trade.recorded_at == T0 + 4 * HOUR


def test_a_hole_is_not_survived_even_if_the_levels_look_untouched_afterwards() -> None:
    """Without the hole the same bars leave the trade open: the hole alone
    makes it indeterminate, and nothing is assumed about the missing hour."""
    assert only(play([sig(0)], tape(), last=10)).status is TradeStatus.OPEN_AT_END
    assert only(play([sig(0)], tape(missing=[5]), last=10)).status is TradeStatus.INDETERMINATE


def test_after_an_uncertainty_the_account_goes_on_from_the_conservative_balance() -> None:
    """The next trade is sized on 9 951.36: budget 49.7568 -> floor(49.7568 / 0.00152 / 1000) = 32 -> 32 000;
    with the optimistic balance 10 145.92 it would be floor(50.7296 / 0.00152 / 1000) = 33 -> 33 000."""
    bars = tape(missing=[2])
    main, optimistic = play([sig(0), sig(6)], bars, last=10), play([sig(0), sig(6)], bars, last=10, scenario=OPTIMISTIC)

    assert [t.status for t in main.trades] == [TradeStatus.INDETERMINATE, TradeStatus.OPEN_AT_END]
    assert (main.trades[1].proposal.equity, main.trades[1].proposal.units) == (D("9951.36"), 32000)
    assert (optimistic.trades[1].proposal.equity, optimistic.trades[1].proposal.units) == (D("10145.92"), 33000)


def test_a_hole_with_no_prices_after_it_leaves_the_trade_open_and_its_valuation_stale() -> None:
    result = play([sig(0)], tape(last=1), last=6)
    trade = only(result)

    assert trade.status is TradeStatus.OPEN_AT_END and trade.uncertainty.kind == DATA_HOLE and len(trade.uncertainty.unobserved_hours) == 4
    assert (trade.valuation_price, trade.valuation_at, trade.valuation_stale) == (MID, T0 + 2 * HOUR, True)
    assert [p.stale for p in result.equity if p.at >= T0] == [False, False, False, True, True, True, True]


def test_an_hour_the_calendar_does_not_expect_is_not_a_hole() -> None:
    def closed_at_two(moment: datetime) -> bool:
        return not (T0 + 2 * HOUR <= moment < T0 + 3 * HOUR)

    trade = only(play([sig(0)], tape({4: ("1.08495", "1.08963", "1.08485", "1.08900")}, missing=[2]), calendar=closed_at_two))

    assert trade.status is TradeStatus.CLOSED and trade.result.reason is ExitReason.TARGET and trade.uncertainty is None


# --- time exit, weekly close, daylight saving ---


def test_after_72_bars_the_trade_leaves_at_the_market_at_the_open_of_the_next_bar() -> None:
    """Open 1.08495 -> bid 1.08490 -> fill 1.08488: 32 000 x (1.08488 - 1.08502) = -4.48."""
    trade = only(play([sig(0)], tape()))

    assert trade.result.reason is ExitReason.TIME and trade.result.exit_price == D("1.08488") and trade.result.net_pnl == D("-4.48")
    assert (trade.exit_at, trade.bars_held, trade.exit_price_source) == (T0 + 72 * HOUR, 72, "APERTURA")


def test_a_time_exit_without_the_next_bar_uses_the_close_of_the_bar_that_just_ended() -> None:
    trade = only(play([sig(0)], tape({71: ("1.08495", "1.08605", "1.08485", "1.08595")}, missing=[72])))

    assert trade.status is TradeStatus.CLOSED and trade.exit_at == T0 + 72 * HOUR and trade.exit_price_source == "CIERRE_VELA_ANTERIOR"
    assert trade.result.exit_price == D("1.08588") and trade.result.net_pnl == D("27.52")  # 1.08595 - 0.00005 - 0.00002


def test_a_trade_opened_on_thursday_leaves_at_the_weekly_close() -> None:
    trade = only(play([sig(48)], tape()))  # Thursday 14:00 UTC; Friday 16:00 New York is 21:00 UTC in winter

    assert trade.result.reason is ExitReason.WEEKLY_CLOSE and trade.exit_at == datetime(2026, 12, 4, 21, 0, tzinfo=UTC)
    assert trade.result.net_pnl == D("-4.48")


@pytest.mark.parametrize("thursday, friday_close", [
    (datetime(2026, 10, 29, 14, 0, tzinfo=UTC), datetime(2026, 10, 30, 20, 0, tzinfo=UTC)),  # summer time until 1 November 2026
    (datetime(2026, 11, 5, 14, 0, tzinfo=UTC), datetime(2026, 11, 6, 21, 0, tzinfo=UTC)),
    (datetime(2027, 3, 11, 14, 0, tzinfo=UTC), datetime(2027, 3, 12, 21, 0, tzinfo=UTC)),  # winter time until 14 March 2027
    (datetime(2027, 3, 18, 14, 0, tzinfo=UTC), datetime(2027, 3, 19, 20, 0, tzinfo=UTC)),
])
def test_the_weekly_close_follows_new_yorks_daylight_saving(thursday: datetime, friday_close: datetime) -> None:
    trade = only(play([sig(0, origin=thursday)], tape(origin=thursday, last=40), origin=thursday, last=40))

    assert trade.result.reason is ExitReason.WEEKLY_CLOSE and trade.exit_at == friday_close


@pytest.mark.parametrize("friday_16_utc, rejections", [
    (datetime(2026, 10, 30, 16, 0, tzinfo=UTC), (Rejection.BLOCKED_FRIDAY,)),  # 12:00 in New York
    (datetime(2026, 11, 6, 16, 0, tzinfo=UTC), ()),  # 11:00 in New York
])
def test_the_friday_entry_block_follows_new_yorks_daylight_saving(friday_16_utc: datetime, rejections: tuple) -> None:  # type: ignore[type-arg]
    result = play([sig(0, origin=friday_16_utc)], tape(origin=friday_16_utc, last=10), origin=friday_16_utc, last=10)

    assert result.decisions[0].proposal.rejections == rejections


# --- one position per pair, two pairs, shared limits ---


def test_a_second_signal_on_a_pair_with_an_open_position_is_rejected_and_counted() -> None:
    result = play([sig(0), sig(2, SHORT), sig(10)], tape({5: ("1.08495", "1.08963", "1.08485", "1.08900")}), last=12)

    assert [d.proposal.rejections for d in result.decisions] == [(), (Rejection.POSITION_OPEN,), ()]
    assert [t.entry_at for t in result.trades] == [T0, T0 + 10 * HOUR]


def test_a_position_closed_at_an_open_frees_the_pair_for_a_signal_at_that_same_instant() -> None:
    result = play([sig(0), sig(3, last_close=D("1.08205"))], tape({3: ("1.08205", "1.08215", "1.08195", "1.08205")}), last=6)

    assert result.trades[0].exit_at == T0 + 3 * HOUR and result.trades[0].result.gap
    assert result.decisions[1].proposal.rejections == () and result.trades[1].entry_at == T0 + 3 * HOUR


def both(eur=LONG, gbp=LONG, limits: RiskLimits | None = None, reverse: bool = False):  # type: ignore[no-untyped-def]
    signals = [sig(0, eur), sig(0, gbp, "GBPUSD")]
    bars = {"GBPUSD": tape(mid=GBP_MID), "EURUSD": tape()} if reverse else {"EURUSD": tape(), "GBPUSD": tape(mid=GBP_MID)}
    return play(signals[::-1] if reverse else signals, bars, last=6, limits=limits)


def test_simultaneous_signals_are_processed_eurusd_first_whatever_the_input_order() -> None:
    """GBP/USD: open 1.27000, spread 1.5 pips, slippage 0.3: ask 1.270075 + 0.00003 -> 1.27011 (rounded against
    the trader); l = 0.00150 + 0.00003 = 0.00153; units = floor(50 / 0.00153 / 1000) x 1000 = 32 000."""
    for result in (both(), both(reverse=True)):
        assert [(d.order, d.symbol) for d in result.decisions] == [(1, "EURUSD"), (2, "GBPUSD")]
        assert [(t.symbol, t.proposal.entry, t.proposal.units) for t in result.trades] == [("EURUSD", D("1.08502"), 32000), ("GBPUSD", D("1.27011"), 32000)]
    assert both() == both(reverse=True)


def test_the_second_pair_sees_the_first_ones_risk_on_the_same_side_of_the_dollar() -> None:
    tight = replace(RiskLimits(), max_usd_direction_risk=D("0.005"))

    assert both(limits=tight).decisions[1].proposal.rejections == (Rejection.USD_EXPOSURE,)
    assert both(LONG, SHORT, limits=tight).decisions[1].proposal.rejections == ()  # opposite sides of the dollar
    assert both().decisions[1].proposal.rejections == ()  # 48.64 + 48.96 <= 1 % of 10 000


def test_margin_is_checked_against_the_positions_already_open() -> None:
    """Leverage 5: EUR/USD needs 32 000 x 1.08502 / 5 = 6 944.13 of the 7 500 allowed (75 % of 10 000); GBP/USD does not fit."""
    result = both(limits=replace(RiskLimits(), leverage=D("5")))

    assert result.trades[0].proposal.margin_required == D("6944.13") and len(result.trades) == 1
    assert result.decisions[1].proposal.rejections == (Rejection.INSUFFICIENT_MARGIN,)


def test_the_daily_loss_limit_stops_entries_until_the_next_utc_day() -> None:
    """Three stops on one UTC day cost 3 x 48.64 = 145.92 of the 150.00 allowed (1.5 % of 10 000); what is left,
    4.08, does not fit a fourth trade: rejected. The first signal after 00:00 UTC is accepted, against the new
    day's starting equity. (32 000 units each time: floor(0.005 x 9 902.72 / 0.00152 / 1000) is still 32.)"""
    stopped = ("1.08495", "1.08505", "1.08357", "1.08400")
    result = play([sig(0), sig(1), sig(2), sig(3), sig(11)], tape({0: stopped, 1: stopped, 2: stopped, 11: QUIET}), last=14)

    assert [d.proposal.rejections for d in result.decisions] == [(), (), (), (Rejection.DAILY_LOSS_LIMIT,), ()]
    assert [t.booked_pnl for t in result.trades[:3]] == [D("-48.64"), D("-48.64"), D("-48.64")]
    assert [t.proposal.equity for t in result.trades] == [D("10000"), D("9951.36"), D("9902.72"), D("9854.08")]
    assert result.trades[3].entry_at == datetime(2026, 12, 2, 1, 0, tzinfo=UTC)


# --- balance, estimated equity, stale prices ---


def test_the_balance_changes_only_when_a_trade_closes_and_the_equity_marks_the_open_one_at_the_exit_side() -> None:
    """At T0 + 2h the last close is 1.08595: bid 1.08590, 32 000 x (1.08590 - 1.08502) = +28.16."""
    result = play([sig(0)], tape({1: ("1.08495", "1.08605", "1.08485", "1.08595"), 3: ("1.08495", "1.08505", "1.08357", "1.08400")}), last=6)
    points = {p.at: p for p in result.equity}

    assert [p.at for p in result.equity] == [T0 + n * HOUR for n in range(-4, 7)]
    assert (points[T0 - HOUR].balance, points[T0 - HOUR].equity, points[T0 - HOUR].open_positions) == (D("10000"), D("10000"), 0)
    assert (points[T0].balance, points[T0].equity, points[T0].open_positions) == (D("10000"), D("9996.16"), 1)  # 32 000 x (1.08490 - 1.08502): spread and entry slippage at once
    assert (points[T0 + 2 * HOUR].balance, points[T0 + 2 * HOUR].equity) == (D("10000"), D("10028.16"))
    assert (points[T0 + 4 * HOUR].balance, points[T0 + 4 * HOUR].equity, points[T0 + 4 * HOUR].open_positions) == (D("9951.36"), D("9951.36"), 0)
    assert result.final_balance == D("9951.36") and not any(p.stale for p in result.equity)


def test_initial_capital_plus_every_booked_result_is_the_final_balance() -> None:
    stopped, won = ("1.08495", "1.08505", "1.08357", "1.08400"), ("1.08495", "1.08963", "1.08485", "1.08900")
    result = play([sig(0), sig(3), sig(6, SHORT)], tape({0: stopped, 4: won}), last=90)

    assert len(result.trades) == 3 and all(t.status is TradeStatus.CLOSED for t in result.trades)
    assert result.final_balance == D("10000") + sum(t.booked_pnl for t in result.trades)
    assert result.equity[-1].balance == result.equity[-1].equity == result.final_balance


# --- cost scenarios ---


def test_costs_x1_5_and_x2_move_the_entry_the_target_and_the_exit_side() -> None:
    """x2: spread 2.0 pips, slippage 0.4: ask 1.08505 -> E 1.08509, SL 1.08359, l 0.00154, T 0.00462, TP 1.08971;
           units floor(50 / 0.00154 / 1000) = 32 -> 32 000; stop touched when low - 0.00010 <= 1.08359; fill 1.08355: -49.28.
    x1.5: spread 1.5, slippage 0.3: ask 1.085025 + 0.00003 = 1.085055 -> E 1.08506 (against the trader), SL 1.08356,
           l 0.00153, T 0.00459, TP 1.08965; 32 000 units; exit side = price - 0.000075; fill 1.08353: -48.96."""
    touched_x2, touched_x1_5 = tape({3: ("1.08495", "1.08505", "1.08369", "1.08400")}), tape({3: ("1.08495", "1.08505", "1.08363", "1.08400")})
    double, half_more = only(play([sig(0)], touched_x2, scenario=COSTS_X2)), only(play([sig(0)], touched_x1_5, scenario=COSTS_X1_5))

    assert (double.proposal.spread, double.proposal.slippage) == (D("0.00020"), D("0.00004"))
    assert (double.proposal.entry, double.proposal.stop, double.proposal.target, double.proposal.units) == (D("1.08509"), D("1.08359"), D("1.08971"), 32000)
    assert (double.result.reason, double.result.exit_price, double.result.net_pnl) == (ExitReason.STOP, D("1.08355"), D("-49.28"))
    assert (half_more.proposal.entry, half_more.proposal.stop, half_more.proposal.target) == (D("1.08506"), D("1.08356"), D("1.08965"))
    assert (half_more.result.exit_price, half_more.result.net_pnl) == (D("1.08353"), D("-48.96"))
    assert only(play([sig(0)], touched_x2, last=10)).status is TradeStatus.OPEN_AT_END  # at base costs that low does not reach the stop


def test_a_hypothetical_commission_is_charged_outside_the_prices_and_moves_target_and_size() -> None:
    """7 USD per 100 000 round trip: c = 0.00007; l = 0.00152 + 0.00007 = 0.00159;
    T >= 3 x 0.00152 + 4 x 0.00007 = 0.00484 -> TP 1.08986; units floor(50 / 0.00159 / 1000) = 31 -> 31 000;
    stop: 31 000 x -0.00152 = -47.12 gross, 2.17 commission, -49.29 net. At x2 the commission doubles too."""
    scenario = hypothetical_commission(D("7"))
    trade = only(play([sig(0)], tape({0: ("1.08495", "1.08505", "1.08357", "1.08400")}), scenario=scenario))

    assert "HIPOTETICA" in scenario.name and scenario.commission_per_100k == D("7")
    assert (trade.proposal.target, trade.proposal.units, trade.proposal.commission_per_unit) == (D("1.08986"), 31000, D("0.00007"))
    assert (trade.result.gross_pnl, trade.result.commission_cost, trade.result.net_pnl) == (D("-47.12"), D("2.17"), D("-49.29"))
    assert replace(scenario, cost_multiple=D("2")).execution().commission_per_100k == D("14")
    assert BASE.execution().commission_per_100k == D("0")


def test_illustrative_financing_moves_the_target_and_charges_the_nights_actually_crossed() -> None:
    """The worked example of docs/ema-rsi-atr-proposals.md: 5 planned nights at an ILLUSTRATIVE 3 %:
    TP 1.09139, 25 000 units. Leaving after 72 bars (5 nights): 25 000 x (1.08488 - 1.08502) = -3.50 gross,
    25 000 x 5 x 1.08502 x 0.03 / 360 = 11.30 financing, -14.80 net. Stopped in the entry bar: no night, -38.00."""
    timed = only(play([sig(0)], tape(), scenario=ILLUSTRATIVE_FINANCING))
    stopped = only(play([sig(0)], tape({0: ("1.08495", "1.08505", "1.08357", "1.08400")}), scenario=ILLUSTRATIVE_FINANCING))

    assert ILLUSTRATIVE_FINANCING.financing.annual_rate == D("0.03") and not ILLUSTRATIVE_FINANCING.financing.verified
    assert (timed.proposal.target, timed.proposal.units, timed.proposal.planned_nights) == (D("1.09139"), 25000, 5)
    assert timed.proposal.financing_status is FinancingStatus.ILLUSTRATIVE and not timed.proposal.net_3r_verified
    assert (timed.result.nights, timed.result.gross_pnl, timed.result.financing_cost, timed.result.net_pnl) == (5, D("-3.50"), D("11.30"), D("-14.80"))
    assert (stopped.result.nights, stopped.result.net_pnl) == (0, D("-38.00"))
    assert only(play([sig(0)], tape())).result.financing_status is FinancingStatus.NOT_VERIFIED  # the base run charges none


# --- no lookahead ---


def busy_story():  # type: ignore[no-untyped-def]
    signals = [sig(0), sig(1, SHORT), sig(5, SHORT), sig(9), sig(12, symbol="GBPUSD"), sig(20)]
    eur = tape({3: ("1.08495", "1.08505", "1.08357", "1.08400"), 7: ("1.08495", "1.08505", "1.08027", "1.08100"),
                10: ("1.08495", "1.08963", "1.08357", "1.08495")}, missing=[22])
    return signals, {"EURUSD": eur, "GBPUSD": tape(mid=GBP_MID)}


@pytest.mark.parametrize("cut", [0, 3, 4, 7, 10, 13, 22, 23, 30])
def test_nothing_decided_up_to_an_instant_depends_on_what_comes_after_it(cut: int) -> None:
    signals, bars = busy_story()
    at = T0 + cut * HOUR
    full = play(signals, bars, last=40)

    truncated = {s: [b for b in series if b.timestamp <= at] for s, series in bars.items()}
    wild = {s: [b if b.timestamp < at else bar(b.timestamp, b.open, b.open + D("0.05"), b.open - D("0.05"), b.open + D("0.03")) if b.timestamp == at
                else bar(b.timestamp, "2.5", "2.6", "2.4", "2.5") for b in series] for s, series in bars.items()}
    for other in (play(signals, truncated, last=cut), play(signals, wild, last=40)):
        assert [d for d in other.decisions if d.at <= at] == [d for d in full.decisions if d.at <= at]
        assert [t for t in other.trades if t.recorded_at and t.recorded_at <= at] == [t for t in full.trades if t.recorded_at and t.recorded_at <= at]
        assert [p for p in other.equity if p.at <= at] == [p for p in full.equity if p.at <= at]
        assert [t.proposal for t in other.trades if t.entry_at <= at] == [t.proposal for t in full.trades if t.entry_at <= at]


def test_the_signal_pass_hands_each_evaluation_only_the_bars_closed_by_then() -> None:
    seen: list[tuple[datetime, datetime]] = []

    def spy(bars, as_of, **_):  # type: ignore[no-untyped-def]
        seen.append((as_of, max(b.timestamp for b in bars)))
        return sig(0, Outcome.NO_CROSS, origin=as_of)

    evaluate_signals(replace(STRATEGY_A, evaluate=spy, lookback_hours=5), {"EURUSD": tape()}, start=T0, end=T0 + 20 * HOUR,
                     data_kind=QuoteKind.SYNTHETIC, data_source=SOURCE)

    assert len(seen) == 21 and all(newest + HOUR == as_of for as_of, newest in seen)


# --- the two real strategies on synthetic series ---


def test_strategy_a_runs_end_to_end_and_every_decision_is_its_own_signal_at_the_next_open() -> None:
    bars = walk(420)
    start, end = bars[300].timestamp, bars[-1].timestamp
    result = run(STRATEGY_A, {"EURUSD": bars}, start=start, end=end, data_kind=QuoteKind.SYNTHETIC, data_source=SOURCE, calendar=always_open)

    assert (result.engine, result.strategy_id, result.parameters_sha256) == (ENGINE_ID, ema_cross.STRATEGY_ID, ema_cross.fingerprint())
    assert result.decisions and sum(result.coverage["EURUSD"].values()) == 120
    by_label = {b.timestamp: b for b in bars}
    for decision in result.decisions:
        own = ema_cross.evaluate([b for b in bars if b.timestamp < decision.at], decision.at, symbol="EURUSD", data_kind=QuoteKind.SYNTHETIC,
                                 data_source=SOURCE, is_open=always_open)
        assert own.is_signal and own.signal_id == decision.signal_id == decision.proposal.signal_id and own.candle_close == decision.at
        if decision.proposal.bid is not None:
            assert (decision.proposal.bid + decision.proposal.ask) / 2 == by_label[decision.at].open
    assert result.final_balance == D("10000") + sum(t.booked_pnl for t in result.trades if t.booked_pnl is not None)


def test_strategy_b_runs_end_to_end_on_its_own_signals() -> None:
    bars = pullbacks(1)
    start, end = bars[805].timestamp, bars[850].timestamp
    result = run(STRATEGY_B, {"EURUSD": bars}, start=start, end=end, data_kind=QuoteKind.SYNTHETIC, data_source=SOURCE, calendar=always_open)

    assert (result.strategy_id, result.parameters_sha256) == (ema_trend_cross.STRATEGY_ID, ema_trend_cross.fingerprint())
    assert [d.at for d in result.decisions] == [bars[810].timestamp + HOUR] and result.decisions[0].direction == "LONG"
    assert result.coverage["EURUSD"]["LONG"] == 1 and result.coverage["EURUSD"]["CRUCE_SIN_CONFIRMACION"] >= 1


def test_the_two_strategies_keep_separate_accounts() -> None:
    """A's run is the same whether or not B has run, and neither run object shares state with the other."""
    bars = {"EURUSD": walk(420)}
    options = dict(start=bars["EURUSD"][300].timestamp, end=bars["EURUSD"][-1].timestamp, data_kind=QuoteKind.SYNTHETIC, data_source=SOURCE, calendar=always_open)
    alone = run(STRATEGY_A, bars, **options)  # type: ignore[arg-type]
    other = run(replace(STRATEGY_A, strategy_id="otra@0.0.0"), bars, scenario=COSTS_X2, **options)  # type: ignore[arg-type]
    again = run(STRATEGY_A, bars, **options)  # type: ignore[arg-type]

    assert alone == again and other.final_balance != alone.final_balance
    assert alone.limits.capital == other.limits.capital == D("10000")


def test_only_supported_pairs_are_accepted() -> None:
    with pytest.raises(ValueError):
        run_account(STRATEGY, SHA, {}, {"USDJPY": tape()}, start=T0, end=T0 + HOUR)


def test_a_position_due_inside_a_hole_is_indeterminate_too() -> None:
    trade = only(play([sig(0)], tape(missing=[71, 72])))

    assert trade.status is TradeStatus.INDETERMINATE and trade.recorded_at == T0 + 73 * HOUR
    assert trade.uncertainty.unobserved_hours == (T0 + 71 * HOUR, T0 + 72 * HOUR) and trade.uncertainty.last_price_before_at == T0 + 71 * HOUR


def test_a_position_due_with_no_price_at_all_waits_and_is_indeterminate_when_prices_return() -> None:
    def closed_then(moment: datetime) -> bool:  # the calendar expects no bar at 71 and 72: not a hole, but no price to leave at
        return not (T0 + 71 * HOUR <= moment < T0 + 73 * HOUR)

    trade = only(play([sig(0)], tape(missing=[71, 72]), calendar=closed_then))

    assert trade.status is TradeStatus.INDETERMINATE and trade.recorded_at == T0 + 73 * HOUR
    assert trade.uncertainty.unobserved_hours == (T0 + 72 * HOUR,) and trade.booked_pnl == D("-48.64")


@pytest.mark.parametrize("strategy, own, count", [(STRATEGY_A, ema_cross.evaluate, 700), (STRATEGY_B, ema_trend_cross.evaluate, 1250)])  # both end on a weekday
def test_bars_outside_the_calendar_do_not_starve_the_window(strategy, own, count) -> None:  # type: ignore[no-untyped-def]
    """A series with a bar for every hour, weekends included, read with a
    calendar that expects no weekend bar: the signal pass must hand each
    evaluation enough history to fill its window of EXPECTED hours, and give
    exactly what the strategy gives when handed the whole history."""
    from investment_committee.simulation.sessions import is_week_open

    bars = walk(count)
    last = bars[-1].timestamp + HOUR
    result = evaluate_signals(strategy, {"EURUSD": bars}, start=last - 3 * HOUR, end=last, data_kind=QuoteKind.SYNTHETIC, data_source=SOURCE,
                              calendar=is_week_open)["EURUSD"]

    for moment, evaluation in result.items():
        assert evaluation.outcome is not Outcome.DATA_REJECTED
        whole = own([b for b in bars if b.timestamp < moment], moment, symbol="EURUSD", data_kind=QuoteKind.SYNTHETIC, data_source=SOURCE,
                    is_open=is_week_open)
        assert replace(evaluation, bars_received=0) == replace(whole, bars_received=0)  # only the count of bars handed over differs
