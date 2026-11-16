"""Tests for simulation.trade_proposal: entry, stop, target, size, costs,
financing, risk controls, and the settlement arithmetic.

Prices are made up (QuoteKind.SYNTHETIC unless a test is about kinds).
Expected numbers come from hand calculations written in the tests and from
an independent reference in exact fractions that finds the target by
walking tick by tick, not with the formula under test."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from itertools import product

import pytest

from investment_committee.market_intelligence.quotes import Quote, QuoteKind, estimated_from_mid
from investment_committee.simulation.ema_cross import STRATEGY_ID, Outcome, SignalEvaluation, fingerprint
from investment_committee.simulation.opportunities import AccountState, OpenPosition, RiskLimits
from investment_committee.simulation.trade_proposal import (
    ESTIMATED_PRICE,
    EURUSD_RULES,
    FINANCING_NOT_VERIFIED,
    GBPUSD_RULES,
    SYNTHETIC_DATA,
    ExecutionParameters,
    ExitReason,
    FinancingAssumption,
    FinancingStatus,
    ProposalStatus,
    Rejection,
    TradeProposal,
    build_proposal,
    indeterminate,
    instrument_for,
    settle,
    weakest,
)

UTC = timezone.utc
HOUR = timedelta(hours=1)
D = Decimal
TUESDAY = datetime(2026, 12, 1, 14, 0, tzinfo=UTC)  # 09:00 New York; 72 bars end Friday 09:00: 5 financing nights
THURSDAY = datetime(2026, 12, 3, 13, 0, tzinfo=UTC)  # 08:00 New York; the weekly close comes first: 1 night
ILLUSTRATIVE = FinancingAssumption(D("0.03"), "escenario ilustrativo, no es una tasa real")
VERIFIED = FinancingAssumption(D("0.03"), "fuente de prueba", verified=True)
FRESH = AccountState.fresh(D("10000"))
TICK = D("0.00001")


def signal(direction: str = "LONG", symbol: str = "EURUSD", close: datetime = TUESDAY, atr: str = "0.00100", last: str = "1.08495",
           kind: QuoteKind = QuoteKind.SYNTHETIC) -> SignalEvaluation:
    return SignalEvaluation(
        strategy_id=STRATEGY_ID, parameters_sha256=fingerprint(), symbol=symbol, as_of=close, candle_open=close - HOUR, candle_close=close,
        outcome=Outcome[direction], reasons=(), detail="", data_kind=kind, data_source="prueba", bars_received=300, forming_bars_excluded=0,
        bars_used=300, last_close=D(last), atr=D(atr), rsi=D("60"),
    )


def quote(bid: str = "1.08490", ask: str = "1.08500", at: datetime = TUESDAY, symbol: str = "EURUSD", kind: QuoteKind = QuoteKind.SYNTHETIC) -> Quote:
    return Quote(symbol, at, D(bid), D(ask), kind, "prueba")


def propose(sig: SignalEvaluation | None = None, q: Quote | None = None, *, delay: timedelta = timedelta(seconds=2), **kwargs) -> TradeProposal:  # type: ignore[no-untyped-def]
    sig = sig or signal()
    q = q if q is not None else quote(at=sig.candle_close)
    kwargs.setdefault("account", FRESH)
    return build_proposal(sig, q, q.timestamp + delay, **kwargs)


def eurusd_long(**kwargs) -> TradeProposal:  # type: ignore[no-untyped-def]
    return propose(signal("LONG"), quote("1.08490", "1.08500"), **kwargs)


def gbpusd_short(**kwargs) -> TradeProposal:  # type: ignore[no-untyped-def]
    return propose(signal("SHORT", "GBPUSD", THURSDAY, atr="0.00140", last="1.27407"), quote("1.27400", "1.27415", THURSDAY, "GBPUSD"), **kwargs)


# --- independent reference ---


def reference(long_: bool, bid: str, ask: str, atr: str, slip: str, commission_per_100k: str, rate: str | None, nights: int, equity: str = "10000"):  # type: ignore[no-untyped-def]
    """(entry, stop, target, loss per unit, gain per unit, units) in exact fractions."""
    tick, sign = Fraction(TICK), 1 if long_ else -1
    entry = Fraction(ask) + Fraction(slip) if long_ else Fraction(bid) - Fraction(slip)
    raw_stop = entry - sign * Fraction(3, 2) * Fraction(atr)
    stop = Fraction(round(raw_stop / tick)) * tick  # round(): half to even
    distance = abs(entry - stop)
    commission = Fraction(commission_per_100k) / 100000
    financing = Fraction(0) if rate is None else nights * entry * Fraction(rate) / 360
    loss = distance + Fraction(slip) + commission + financing
    ticks = 0
    while ticks * tick - commission - financing < 3 * loss:  # walk outwards one tick at a time
        ticks += 1
    target = entry + sign * ticks * tick
    units = int(Fraction(equity) * Fraction(5, 1000) / loss // 1000) * 1000
    return entry, stop, target, loss, ticks * tick - commission - financing, units


# --- the two worked examples ---


def test_worked_example_eurusd_long() -> None:
    """bid 1.08490 / ask 1.08500, slip 0.2 pips, ATR 10 pips, 5 nights at an
    ILLUSTRATIVE 3 % a year (phi = 1.08502 x 0.03 / 360 = 0.0000904183 per unit and night).
      E  = 1.08500 + 0.00002                      = 1.08502
      SL = 1.08502 - 1.5 x 0.00100                = 1.08352      D = 0.00150
      F  = 5 x 0.0000904183                       = 0.0004520917
      l  = 0.00150 + 0.00002 + 0 + 0.0004520917   = 0.0019720917
      T >= 3 x 0.00152 + 4 x 0.0004520917         = 0.0063683667 -> 0.00637;  TP = 1.09139
      g  = 0.00637 - 0.0004520917                 = 0.0059179083   (g / l = 3.0008)
      units = floor(50 / 0.0019720917 / 1000) x 1000 = 25 000"""
    p = eurusd_long(financing=ILLUSTRATIVE)

    assert p.status is ProposalStatus.ACCEPTED and p.direction == "LONG" and p.hypothetical
    assert (p.entry, p.stop, p.target) == (D("1.08502"), D("1.08352"), D("1.09139"))
    assert (p.stop_distance, p.target_distance, p.planned_nights) == (D("0.00150"), D("0.00637"), 5)
    assert (p.stop_distance_pips, p.target_distance_pips, p.target_distance_atr) == (D("15.0"), D("63.7"), D("6.37"))
    assert p.horizon_end == datetime(2026, 12, 4, 14, 0, tzinfo=UTC)
    assert p.units == 25000 and p.risk_budget == D("50.00")
    assert p.estimated_loss_at_stop == D("49.30") and p.expected_net_profit == D("147.95")
    assert (p.planned_financing_cost, p.stop_slippage_cost, p.commission_cost) == (D("11.30"), D("0.50"), D("0.00"))
    assert (p.spread_cost, p.entry_slippage_cost) == (D("2.50"), D("0.50"))  # information only
    assert (p.notional, p.margin_required) == (D("27125.50"), D("904.18"))
    assert round(p.net_reward_risk, 4) == D("3.0008") and round(p.net_reward_risk_before_financing, 2) == D("4.19")  # type: ignore[arg-type]
    assert round(p.gross_reward_risk, 2) == D("4.25")  # type: ignore[arg-type]
    assert p.financing_status is FinancingStatus.ILLUSTRATIVE and FINANCING_NOT_VERIFIED in p.flags and not p.net_3r_verified


def test_worked_example_gbpusd_short() -> None:
    """bid 1.27400 / ask 1.27415, slip 0.3 pips, ATR 14 pips, 1 night at the same illustrative rate.
      E  = 1.27400 - 0.00003 = 1.27397;  SL = 1.27397 + 0.00210 = 1.27607
      F  = 1.27397 x 0.03 / 360 = 0.0001061642
      l  = 0.00210 + 0.00003 + 0.0001061642 = 0.0022361642
      T >= 3 x 0.00213 + 4 x 0.0001061642 = 0.0068146567 -> 0.00682;  TP = 1.26715
      units = floor(50 / 0.0022361642 / 1000) x 1000 = 22 000"""
    p = gbpusd_short(financing=ILLUSTRATIVE)

    assert p.accepted and p.direction == "SHORT"
    assert (p.entry, p.stop, p.target) == (D("1.27397"), D("1.27607"), D("1.26715"))
    assert (p.planned_nights, p.horizon_end) == (1, datetime(2026, 12, 4, 21, 0, tzinfo=UTC))
    assert p.units == 22000 and p.estimated_loss_at_stop == D("49.20") and p.expected_net_profit == D("147.70")
    assert (p.planned_financing_cost, p.spread_cost, p.stop_slippage_cost) == (D("2.34"), D("3.30"), D("0.66"))
    assert (p.notional, p.margin_required) == (D("28027.34"), D("934.24"))
    assert round(p.net_reward_risk, 3) == D("3.002") and round(p.net_reward_risk_before_financing, 2) == D("3.20")  # type: ignore[arg-type]
    assert (p.stop_distance_pips, p.target_distance_pips) == (D("21.0"), D("68.2")) and round(p.target_distance_atr, 2) == D("4.87")  # type: ignore[arg-type]


def test_without_a_financing_rate_the_target_ignores_financing_and_says_so() -> None:
    p = eurusd_long()

    assert p.accepted and p.target == D("1.08958")  # T = 3 x (0.00150 + 0.00002) = 0.00456
    assert p.financing_status is FinancingStatus.NOT_VERIFIED and FINANCING_NOT_VERIFIED in p.flags
    assert p.net_reward_risk is None and p.planned_financing_cost is None and p.financing_per_unit_night is None
    assert p.net_reward_risk_before_financing == D("3") and not p.net_3r_verified
    assert p.planned_nights == 5  # the nights are known; their cost is not


def test_only_a_verified_rate_supports_the_net_3r_claim() -> None:
    verified = eurusd_long(financing=VERIFIED)

    assert verified.net_3r_verified and FINANCING_NOT_VERIFIED not in verified.flags
    assert (verified.entry, verified.stop, verified.target) == (D("1.08502"), D("1.08352"), D("1.09139"))
    with pytest.raises(ValueError, match="source"):
        FinancingAssumption(D("0.03"))
    with pytest.raises(ValueError):
        FinancingAssumption(D("-0.01"), "x")
    with pytest.raises(ValueError):
        FinancingAssumption(verified=True)


# --- properties over a grid, against the independent reference ---


GRID = list(product(
    ("LONG", "SHORT"), (EURUSD_RULES, GBPUSD_RULES), ("0.00040", "0.00100", "0.00137", "0.00310"), ("0.00000", "0.00003", "0.00012", "0.00025"),
    ("0", "7"), (None, "0.03", "0.08"), (TUESDAY, THURSDAY, datetime(2026, 12, 7, 2, 0, tzinfo=UTC)),
))


@pytest.mark.parametrize(("direction", "rules", "atr", "spread", "commission", "rate", "when"), GRID)
def test_levels_costs_and_size_hold_everywhere(direction: str, rules, atr: str, spread: str, commission: str, rate: str | None, when: datetime) -> None:  # type: ignore[no-untyped-def]
    bid = D("1.08490") if rules is EURUSD_RULES else D("1.27400")
    ask = bid + D(spread)
    financing = FinancingAssumption() if rate is None else FinancingAssumption(D(rate), "escenario ilustrativo")
    p = build_proposal(
        signal(direction, rules.symbol, when, atr=atr, last=str((bid + ask) / 2)), Quote(rules.symbol, when, bid, ask, QuoteKind.SYNTHETIC, "prueba"),
        when, account=AccountState.fresh(D("9873.41")), execution=ExecutionParameters(commission_per_100k=D(commission)), financing=financing,
    )
    assert p.accepted, p.detail
    sign = 1 if direction == "LONG" else -1
    entry, stop, target, loss, gain, units = reference(direction == "LONG", str(bid), str(ask), atr, str(rules.slippage), commission, rate,
                                                       p.planned_nights, "9873.41")  # type: ignore[arg-type]

    assert (Fraction(p.entry), Fraction(p.stop), Fraction(p.target)) == (entry, stop, target)  # type: ignore[arg-type]
    assert abs(Fraction(p.loss_per_unit) - loss) < Fraction(1, 10**20) and abs(Fraction(p.gain_per_unit) - gain) < Fraction(1, 10**20)  # type: ignore[arg-type]
    assert p.units == units
    # at least 3R net under the configured assumptions, and one tick less would not be
    assert p.gain_per_unit >= 3 * p.loss_per_unit and p.gain_per_unit - TICK < 3 * p.loss_per_unit  # type: ignore[operator]
    # the stop and the target are on the right sides and on the tick grid
    assert (p.entry - p.stop) * sign > 0 and (p.target - p.entry) * sign > 0  # type: ignore[operator]
    assert all(level % TICK == 0 for level in (p.entry, p.stop, p.target))  # type: ignore[operator]
    # the size never exceeds the budget after rounding, and one more lot would
    assert p.units * p.loss_per_unit <= D("9873.41") * D("0.005") < (p.units + 1000) * p.loss_per_unit  # type: ignore[operator]
    assert p.estimated_loss_at_stop <= p.risk_budget  # type: ignore[operator]
    assert p.net_reward_risk_before_financing >= 3  # type: ignore[operator]
    assert p.net_reward_risk is None or p.net_reward_risk >= 3


def test_without_any_cost_the_target_is_exactly_three_stop_distances() -> None:
    free = dataclasses.replace(EURUSD_RULES, slippage=D("0"))

    p = propose(signal(), quote("1.08500", "1.08500"), rules=free)

    assert (p.entry, p.stop, p.target) == (D("1.08500"), D("1.08350"), D("1.08950"))
    assert p.gross_reward_risk == p.net_reward_risk_before_financing == D("3") and p.loss_per_unit == D("0.00150")


def test_long_and_short_are_mirror_images() -> None:
    long_ = propose(signal("LONG"), quote("1.08490", "1.08500"))
    short = propose(signal("SHORT"), quote("1.08490", "1.08500"))

    assert (long_.stop_distance, long_.target_distance, long_.loss_per_unit, long_.gain_per_unit, long_.units) == (
        short.stop_distance, short.target_distance, short.loss_per_unit, short.gain_per_unit, short.units)
    assert long_.entry - D("1.08500") == D("1.08490") - short.entry == D("0.00002")  # each pays the slippage on its own side
    assert long_.stop < long_.entry < long_.target and short.target < short.entry < short.stop  # type: ignore[operator]


def test_more_cost_means_a_farther_target_and_no_more_units() -> None:
    cheap, dear = eurusd_long(financing=ILLUSTRATIVE), eurusd_long(financing=FinancingAssumption(D("0.08"), "escenario ilustrativo"))
    costly = propose(signal(), quote("1.08490", "1.08500"), rules=dataclasses.replace(EURUSD_RULES, slippage=D("0.00004")), financing=ILLUSTRATIVE)

    assert dear.target > cheap.target and dear.units <= cheap.units  # type: ignore[operator]
    assert costly.target_distance > cheap.target_distance and costly.units <= cheap.units  # type: ignore[operator]
    with pytest.raises(ValueError):
        propose(signal(), quote(), rules=GBPUSD_RULES)  # rules of another instrument


# --- accounting: nothing is counted twice ---


def test_a_normal_stop_loses_exactly_the_estimated_loss() -> None:
    for build in (eurusd_long, gbpusd_short):
        p = build(financing=ILLUSTRATIVE)

        result = settle(p, ExitReason.STOP, p.horizon_end, financing=ILLUSTRATIVE)  # held to the planned horizon: the planned nights

        assert result.nights == p.planned_nights and not result.gap
        assert abs(result.net_pnl + p.estimated_loss_at_stop) <= D("0.01")  # cents are rounded item by item
        assert result.loss_beyond_estimate <= D("0.01") and not result.exceeded_budget
        assert abs(result.r_multiple + 1) < D("0.001")


def test_a_target_held_to_the_horizon_makes_exactly_the_planned_profit() -> None:
    for build in (eurusd_long, gbpusd_short):
        p = build(financing=ILLUSTRATIVE)

        result = settle(p, ExitReason.TARGET, p.horizon_end, financing=ILLUSTRATIVE)

        assert result.exit_price == p.target and abs(result.net_pnl - p.expected_net_profit) <= D("0.01")
        assert result.r_multiple >= D("3.00")


def test_the_spread_and_the_entry_slippage_are_paid_once_through_the_prices() -> None:
    """LONG from bid 1.08490 / ask 1.08500 to its target, the spread still 1
    pip there. The mid price moves from 1.08495 to 1.08963 (bid = TP =
    1.08958): 46.8 pips. The trade keeps 45.6: the mid move minus one spread
    (1.0) and one entry slippage (0.2) - and nothing else."""
    p = eurusd_long()

    result = settle(p, ExitReason.TARGET, TUESDAY + HOUR)

    mid_move = (p.target + D("0.00005")) - D("1.08495")
    assert result.gross_pnl == p.units * (mid_move - p.spread - p.slippage) == p.units * D("0.00456")
    assert result.net_pnl == result.gross_pnl == D("145.92")  # no commission, no financing configured: nothing subtracted again
    assert result.commission_cost == result.financing_cost == D("0.00")


def test_commission_is_subtracted_once_from_the_gain_and_added_once_to_the_loss() -> None:
    execution = ExecutionParameters(commission_per_100k=D("7"))

    p = eurusd_long(execution=execution)

    assert p.commission_per_unit == D("0.00007") and p.loss_per_unit == D("0.00159")  # 0.00150 + 0.00002 + 0.00007
    assert p.target_distance == D("0.00484") and p.gain_per_unit == D("0.00477")  # 3 x 0.00152 + 4 x 0.00007; 0.00477 = 3 x 0.00159
    win, loss = settle(p, ExitReason.TARGET, TUESDAY + HOUR), settle(p, ExitReason.STOP, TUESDAY + HOUR)
    assert win.commission_cost == loss.commission_cost == p.commission_cost == D("2.17")  # 31 000 units x 0.00007
    assert win.net_pnl == p.units * D("0.00484") - D("2.17") and loss.net_pnl == -(p.units * D("0.00152")) - D("2.17")


# --- planned and realized: financing for the nights actually crossed ---


def test_a_target_reached_sooner_pays_fewer_nights_and_the_target_does_not_move() -> None:
    p = eurusd_long(financing=ILLUSTRATIVE)

    early = settle(p, ExitReason.TARGET, TUESDAY + 3 * HOUR, financing=ILLUSTRATIVE)  # before any rollover
    one_night = settle(p, ExitReason.TARGET, TUESDAY + 10 * HOUR, financing=ILLUSTRATIVE)

    assert early.exit_price == one_night.exit_price == p.target == D("1.09139")
    assert (early.nights, early.financing_cost) == (0, D("0.00")) and (one_night.nights, one_night.financing_cost) == (1, D("2.26"))
    assert early.net_pnl == D("159.25") and one_night.net_pnl == D("156.99")  # 25 000 x 0.00637, minus the nights crossed
    assert early.planned_net_profit == one_night.planned_net_profit == D("147.95")  # what was planned stays on record


def test_settling_needs_the_same_financing_assumption() -> None:
    p = eurusd_long(financing=ILLUSTRATIVE)

    with pytest.raises(ValueError, match="financing"):
        settle(p, ExitReason.TARGET, TUESDAY + HOUR)


# --- losses larger than planned ---


def test_a_gap_through_the_stop_loses_more_than_estimated_and_than_the_budget() -> None:
    p = eurusd_long()  # stop 1.08352, 32 000 units, estimated loss 48.64, budget 50.00

    gapped = settle(p, ExitReason.STOP, TUESDAY + 5 * HOUR, market_price=D("1.08200"))  # the bid reopens 15.2 pips below the stop
    normal = settle(p, ExitReason.STOP, TUESDAY + 5 * HOUR, market_price=D("1.08352"))

    assert (p.units, p.estimated_loss_at_stop, p.risk_budget) == (32000, D("48.64"), D("50.00"))
    assert normal.exit_price == D("1.08350") and normal.net_pnl == D("-48.64") and not normal.gap and not normal.exceeded_budget
    assert gapped.gap and gapped.exit_price == D("1.08198")  # the reopening bid, minus the slippage
    assert gapped.net_pnl == D("-97.28") and gapped.loss_beyond_estimate == D("48.64") and gapped.exceeded_budget
    assert gapped.r_multiple == D("-2.0000")


def test_a_short_gap_and_a_price_that_did_not_reach_the_stop() -> None:
    p = gbpusd_short()

    gapped = settle(p, ExitReason.STOP, THURSDAY + 2 * HOUR, market_price=D("1.27800"))  # the ask reopens above the stop
    inside = settle(p, ExitReason.STOP, THURSDAY + 2 * HOUR, market_price=D("1.27500"))  # a better price never improves a stop fill

    assert gapped.gap and gapped.exit_price == D("1.27803") and gapped.net_pnl < -p.estimated_loss_at_stop  # type: ignore[operator]
    assert not inside.gap and inside.exit_price == p.stop + D("0.00003")  # type: ignore[operator]


def test_market_exits_fill_at_the_exit_side_with_slippage() -> None:
    long_, short = eurusd_long(), gbpusd_short()

    timed = settle(long_, ExitReason.TIME, long_.horizon_end, market_price=D("1.08700"))  # the bid then
    weekly = settle(short, ExitReason.WEEKLY_CLOSE, short.horizon_end, market_price=D("1.27300"))  # the ask then

    assert timed.exit_price == D("1.08698") and timed.net_pnl == long_.units * D("0.00196")
    assert weekly.exit_price == D("1.27303") and weekly.net_pnl == short.units * D("0.00094")
    with pytest.raises(ValueError, match="market exit"):
        settle(long_, ExitReason.TIME, long_.horizon_end)
    with pytest.raises(ValueError):
        settle(long_, ExitReason.TARGET, TUESDAY - HOUR)  # before the entry


def test_stop_and_target_order_unknown_is_indeterminate_with_both_scenarios() -> None:
    p = eurusd_long(financing=ILLUSTRATIVE)

    unknown = indeterminate(p, TUESDAY + 6 * HOUR, "el monitor no tuvo datos entre las 15:00 y las 20:00", financing=ILLUSTRATIVE)

    assert unknown.valid_for_metrics is False and "monitor" in unknown.detail
    assert unknown.conservative.reason is ExitReason.STOP and unknown.optimistic.reason is ExitReason.TARGET
    assert unknown.conservative.net_pnl == D("-38.00") and unknown.optimistic.net_pnl == D("159.25")  # no rollover crossed yet
    assert unknown.equity_pnl == unknown.conservative.net_pnl  # the account books the conservative one
    with pytest.raises(ValueError):
        settle(build_proposal(signal(), None, None, account=FRESH), ExitReason.STOP, TUESDAY)  # a rejected proposal has nothing to settle


# --- rejections ---


def rejected(p: TradeProposal, *reasons: Rejection) -> bool:
    return p.status is ProposalStatus.REJECTED and tuple(p.rejections) == reasons and p.detail != "" and not p.accepted


def test_signal_and_quote_must_be_usable() -> None:
    no_cross = dataclasses.replace(signal(), outcome=Outcome.NO_CROSS)

    assert rejected(propose(no_cross), Rejection.NOT_A_SIGNAL)
    assert rejected(build_proposal(signal(), None, None, account=FRESH), Rejection.NO_QUOTE)
    assert rejected(propose(signal(symbol="USDJPY"), quote(symbol="USDJPY")), Rejection.UNSUPPORTED_INSTRUMENT)
    assert rejected(propose(signal(), quote(symbol="GBPUSD")), Rejection.WRONG_INSTRUMENT)
    assert rejected(propose(signal(), quote("1.08500", "1.08490")), Rejection.INVALID_QUOTE)
    assert rejected(propose(signal(), quote("0", "1.08490")), Rejection.INVALID_QUOTE)
    assert rejected(propose(dataclasses.replace(signal(), atr=D("0"))), Rejection.RATIO_NOT_COMPUTABLE)
    assert propose(signal(symbol="EURUSD=X"), quote(symbol="EUR/USD")).accepted  # the same instrument under two spellings
    assert instrument_for("eur_usd") is EURUSD_RULES and instrument_for("XAUUSD") is None


def test_the_quote_must_come_after_the_close_and_soon() -> None:
    def at(seconds: int, delay: int = 2) -> TradeProposal:
        return propose(signal(), quote(at=TUESDAY + timedelta(seconds=seconds)), delay=timedelta(seconds=delay))

    assert rejected(at(-1), Rejection.QUOTE_BEFORE_CLOSE)  # never a price from before the candle closed
    assert at(0).accepted and at(300).accepted
    assert rejected(at(301), Rejection.SIGNAL_EXPIRED)
    assert at(10, delay=30).accepted and rejected(at(10, delay=31), Rejection.STALE_QUOTE)
    assert rejected(at(10, delay=-1), Rejection.STALE_QUOTE)  # received before it existed: a clock is wrong


def test_spread_and_price_drift_limits() -> None:
    assert propose(signal(), quote("1.08490", "1.08515")).accepted  # 2.5 pips: the limit for EUR/USD
    assert rejected(propose(signal(), quote("1.08490", "1.08516")), Rejection.SPREAD_TOO_WIDE)
    assert propose(signal(last="1.08470"), quote("1.08490", "1.08500")).accepted  # mid 2.5 pips from the close: 0.25 ATR
    assert rejected(propose(signal(last="1.08469"), quote("1.08490", "1.08500")), Rejection.PRICE_MOVED)
    assert rejected(propose(signal(last="1.08521"), quote("1.08490", "1.08500")), Rejection.PRICE_MOVED)


def test_entries_are_blocked_by_the_session_rules() -> None:
    def at(text: str) -> TradeProposal:
        when = datetime.fromisoformat(text).replace(tzinfo=UTC)
        return propose(signal(close=when), quote(at=when))

    assert rejected(at("2026-12-05 12:00"), Rejection.MARKET_CLOSED)
    assert rejected(at("2026-12-04 17:00"), Rejection.BLOCKED_FRIDAY) and at("2026-12-04 16:00").accepted
    assert rejected(at("2026-12-02 21:00"), Rejection.BLOCKED_ROLLOVER) and at("2026-12-02 20:00").accepted
    assert rejected(at("2026-10-30 16:00"), Rejection.BLOCKED_FRIDAY) and at("2026-10-30 15:00").accepted  # summer time


# --- risk, margin and exposure ---


def test_position_size_is_rounded_down_and_can_be_too_small() -> None:
    assert eurusd_long().units == 32000  # 50 / 0.00152 = 32 894.7
    small = eurusd_long(account=AccountState.fresh(D("300")))  # 1.50 / 0.00152 = 986 units
    just_enough = eurusd_long(account=AccountState.fresh(D("304")))  # 1.52 / 0.00152 = 1 000 units exactly

    assert rejected(small, Rejection.POSITION_TOO_SMALL) and small.units == 0
    assert just_enough.accepted and just_enough.units == 1000 and just_enough.estimated_loss_at_stop == just_enough.risk_budget == D("1.52")


def test_one_position_per_pair() -> None:
    account = AccountState(D("10000"), D("10000"), open_positions=[OpenPosition("EURUSD=X", D("40"), D("900"), usd_direction=-1)])

    assert rejected(eurusd_long(account=account), Rejection.POSITION_OPEN)
    assert rejected(propose(signal("SHORT"), quote(), account=account), Rejection.POSITION_OPEN)  # the opposite signal is rejected too
    assert gbpusd_short(account=account).accepted


def test_shared_usd_exposure_is_capped() -> None:
    """LONG EUR/USD and LONG GBP/USD are both short dollars: together they may
    risk at most 1 % of equity (100 USD)."""
    def account(open_risk: str, side: int) -> AccountState:
        return AccountState(D("10000"), D("10000"), open_positions=[OpenPosition("GBPUSD=X", D(open_risk), D("900"), usd_direction=side)])

    assert eurusd_long(account=account("51.36", -1)).accepted  # 51.36 + 48.64 = 100.00: allowed
    assert rejected(eurusd_long(account=account("51.37", -1)), Rejection.USD_EXPOSURE)
    assert eurusd_long(account=account("51.37", +1)).accepted  # the other position is long dollars: a different bet


def test_the_daily_loss_limit_counts_losses_already_taken_and_open_risk() -> None:
    def account(realized: str, open_risk: str = "0") -> AccountState:
        positions = [OpenPosition("GBPUSD=X", D(open_risk), D("900"), usd_direction=+1)] if D(open_risk) else []
        return AccountState(D("10000"), D("10000"), realized_pnl_today=D(realized), open_positions=positions)

    assert eurusd_long(account=account("-101.36")).accepted  # 150 - 101.36 = 48.64 left: exactly enough
    assert rejected(eurusd_long(account=account("-101.37")), Rejection.DAILY_LOSS_LIMIT)
    assert rejected(eurusd_long(account=account("-60", "45")), Rejection.DAILY_LOSS_LIMIT)  # 90 left, 45 at risk, 48.64 more
    assert eurusd_long(account=account("+500", "45")).accepted  # gains do not enlarge the limit, and are not needed here


def test_margin_is_checked_with_the_positions_already_open() -> None:
    def account(used: str) -> AccountState:
        return AccountState(D("10000"), D("10000"), open_positions=[OpenPosition("GBPUSD=X", D("10"), D(used), usd_direction=+1)])

    p = eurusd_long()
    assert p.margin_required == D("1157.35")  # 32 000 x 1.08502 / 30
    assert eurusd_long(account=account("6342.65")).accepted  # 6 342.65 + 1 157.35 = 7 500 = 75 % of equity
    both = eurusd_long(account=account("6342.66"))
    assert rejected(both, Rejection.INSUFFICIENT_MARGIN) and both.units == 32000  # the figures stay on the rejected proposal


def test_every_failed_control_is_reported() -> None:
    account = AccountState(D("10000"), D("10000"), realized_pnl_today=D("-140"),
                           open_positions=[OpenPosition("EURUSD=X", D("60"), D("7000"), usd_direction=-1)])

    p = eurusd_long(account=account)

    assert set(p.rejections) == {Rejection.POSITION_OPEN, Rejection.INSUFFICIENT_MARGIN, Rejection.DAILY_LOSS_LIMIT, Rejection.USD_EXPOSURE}
    assert RiskLimits().risk_per_trade == D("0.005") and RiskLimits().leverage == D("30")  # the existing limits, reused unchanged


# --- precision and rounding ---


def test_an_off_tick_quote_is_rounded_against_the_trader() -> None:
    estimated = estimated_from_mid("EURUSD", TUESDAY, D("1.084955"), D("0.00010"), "Yahoo H1")  # bid 1.084905, ask 1.085005

    long_ = propose(signal("LONG", kind=QuoteKind.ESTIMATED), estimated)
    short = propose(signal("SHORT", kind=QuoteKind.ESTIMATED), estimated)

    assert long_.entry == D("1.08503")  # 1.085005 + 0.00002 = 1.085025, bought no cheaper than 1.08503
    assert short.entry == D("1.08488")  # 1.084905 - 0.00002 = 1.084885, sold no dearer than 1.08488


def test_the_stop_rounds_to_the_nearest_tick_and_the_target_away_from_the_entry() -> None:
    p = propose(signal(atr="0.00137"), quote("1.08490", "1.08500"))  # 1.5 x 0.00137 = 0.002055

    assert p.stop == D("1.08296") and p.stop_distance == D("0.00206")  # 1.082965 -> 1.08296 (half to even)
    assert p.target_distance == D("0.00624") and p.target == D("1.09126")  # 3 x 0.00208
    odd = propose(signal(), quote("1.08490", "1.08500"), execution=ExecutionParameters(commission_per_100k=D("0.3")))
    assert odd.target_distance == D("0.00458")  # 0.00456 + 4 x 0.000003 = 0.004572 -> up to 0.00458


# --- observed, estimated, synthetic ---


def test_a_proposal_takes_the_weakest_kind_of_its_inputs() -> None:
    observed_signal = signal(kind=QuoteKind.OBSERVED)
    observed = propose(observed_signal, quote(kind=QuoteKind.OBSERVED))
    estimated_quote = propose(observed_signal, estimated_from_mid("EURUSD", TUESDAY, D("1.08495"), D("0.00010"), "Yahoo H1"))
    estimated_signal = propose(signal(kind=QuoteKind.ESTIMATED), quote(kind=QuoteKind.OBSERVED))
    synthetic = propose(signal(kind=QuoteKind.SYNTHETIC), quote(kind=QuoteKind.OBSERVED))

    assert observed.data_kind is QuoteKind.OBSERVED and ESTIMATED_PRICE not in observed.flags and SYNTHETIC_DATA not in observed.flags
    assert estimated_quote.data_kind is estimated_signal.data_kind is QuoteKind.ESTIMATED
    assert ESTIMATED_PRICE in estimated_quote.flags and ESTIMATED_PRICE in estimated_signal.flags
    assert synthetic.data_kind is QuoteKind.SYNTHETIC and SYNTHETIC_DATA in synthetic.flags
    assert "spread asumido" in estimated_quote.quote_source  # type: ignore[operator]
    assert all(p.hypothetical for p in (observed, estimated_quote, estimated_signal, synthetic))
    assert weakest(QuoteKind.OBSERVED, QuoteKind.ESTIMATED, QuoteKind.SYNTHETIC) is QuoteKind.SYNTHETIC


def test_the_kind_and_its_flags_reach_the_result() -> None:
    p = propose(signal(kind=QuoteKind.ESTIMATED), estimated_from_mid("EURUSD", TUESDAY, D("1.08495"), D("0.00010"), "Yahoo H1"))

    result = settle(p, ExitReason.STOP, TUESDAY + HOUR)

    assert result.data_kind is QuoteKind.ESTIMATED and ESTIMATED_PRICE in result.flags and FINANCING_NOT_VERIFIED in result.flags
    assert result.hypothetical and result.financing_status is FinancingStatus.NOT_VERIFIED


def test_one_proposal_per_signal() -> None:
    sig = signal()

    assert propose(sig).proposal_id == propose(sig, quote(at=TUESDAY + timedelta(seconds=40))).proposal_id == sig.signal_id
