"""Integration of ema-3-9-20-50-120-forex (strategy B) with the proposal
engine of simulation/trade_proposal.py: signal -> proposal -> settlement.

The signals are real evaluations of synthetic bars (the `pullbacks` series
of test_ema_trend_cross.py), not hand-built objects. Expected levels come
from the independent exact-fraction reference of test_trade_proposal.py,
which finds the target by walking tick by tick. Prices are made up."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from investment_committee.market_intelligence.quotes import Quote, QuoteKind, estimated_from_mid
from investment_committee.simulation import ema_cross
from investment_committee.simulation.ema_trend_cross import STRATEGY_ID, TrendCrossEvaluation, evaluate
from investment_committee.simulation.opportunities import AccountState, OpenPosition
from investment_committee.simulation.signals import Outcome
from investment_committee.simulation.trade_proposal import (
    ESTIMATED_PRICE,
    EURUSD_RULES,
    FINANCING_NOT_VERIFIED,
    SYNTHETIC_DATA,
    ExitReason,
    FinancingAssumption,
    FinancingStatus,
    ProposalStatus,
    Rejection,
    TradeProposal,
    build_proposal,
    indeterminate,
    settle,
)

from ._ema_cross_support import HOUR, SOURCE, always_open
from .test_ema_trend_cross import pullbacks
from .test_trade_proposal import reference

UTC = timezone.utc
D = Decimal
TUESDAY = datetime(2026, 12, 1, 14, 0, tzinfo=UTC)  # 09:00 New York; 72 bars end Friday 09:00: 5 financing nights
ILLUSTRATIVE = FinancingAssumption(D("0.03"), "escenario ilustrativo, no es una tasa real")
VERIFIED = FinancingAssumption(D("0.03"), "fuente de prueba", verified=True)
SPREAD = D("0.00010")
SLIP = "0.00002"


def fresh() -> AccountState:
    return AccountState.fresh(D("10000"))


def evaluation(index: int = 810, sign: int = 1, close: datetime = TUESDAY, kind: QuoteKind = QuoteKind.SYNTHETIC, symbol: str = "EURUSD") -> TrendCrossEvaluation:
    """The candle at `index` of the pullbacks series, placed so that it closes at `close`."""
    bars = pullbacks(sign)[: index + 1]
    shift = close - (bars[-1].timestamp + HOUR)
    bars = [type(b)(b.timestamp + shift, b.open, b.high, b.low, b.close, 0) for b in bars]
    return evaluate(bars, close, symbol=symbol, data_kind=kind, data_source=SOURCE, is_open=always_open)


def quote_for(e: TrendCrossEvaluation, kind: QuoteKind = QuoteKind.SYNTHETIC, after: timedelta = timedelta(seconds=20), symbol: str = "EURUSD") -> Quote:
    assert e.last_close is not None
    return Quote(symbol, e.candle_close + after, e.last_close - SPREAD / 2, e.last_close + SPREAD / 2, kind, "prueba")


def propose(e: TrendCrossEvaluation, quote: Quote | None = None, **kwargs) -> TradeProposal:  # type: ignore[no-untyped-def]
    quote = quote or quote_for(e)
    kwargs.setdefault("account", fresh())
    return build_proposal(e, quote, quote.timestamp + timedelta(seconds=2), **kwargs)


# --- signal -> proposal ---


@pytest.mark.parametrize("sign, direction, financing, rate", [(1, "LONG", None, None), (-1, "SHORT", None, None), (1, "LONG", ILLUSTRATIVE, "0.03"), (-1, "SHORT", ILLUSTRATIVE, "0.03")])
def test_a_confirmed_signal_becomes_a_proposal_with_the_reference_levels(sign: int, direction: str, financing: FinancingAssumption | None, rate: str | None) -> None:
    e = evaluation(sign=sign)
    quote = quote_for(e)
    p = propose(e, quote, financing=financing)
    assert e.outcome is Outcome[direction] and e.atr is not None
    entry, stop, target, loss, gain, units = reference(sign == 1, str(quote.bid), str(quote.ask), str(e.atr), SLIP, "0", rate, 5)

    assert (p.status, p.direction, p.hypothetical, p.planned_nights) == (ProposalStatus.ACCEPTED, direction, True, 5)
    assert (p.proposal_id, p.signal_id, p.candle_close) == (e.evaluation_key, e.signal_id, TUESDAY)
    assert p.proposal_id == f"{STRATEGY_ID}|EURUSD|2026-12-01T14:00:00+00:00"
    assert (Fraction(p.entry), Fraction(p.stop), Fraction(p.target), p.units) == (entry, stop, target, units)  # type: ignore[arg-type]
    close_to = Fraction(1, 10**18)  # the financing per night is a Decimal division (28 digits), the reference an exact fraction
    assert abs(Fraction(p.loss_per_unit) - loss) < close_to and abs(Fraction(p.gain_per_unit) - gain) < close_to  # type: ignore[arg-type]
    assert p.atr == e.atr and abs(Fraction(p.stop_distance) - Fraction(3, 2) * Fraction(e.atr)) <= Fraction(1, 200000)  # type: ignore[arg-type]
    assert (p.stop - p.entry) * sign < 0 < (p.target - p.entry) * sign  # type: ignore[operator]


@pytest.mark.parametrize("sign", [1, -1])
def test_risk_and_rounding(sign: int) -> None:
    p = propose(evaluation(sign=sign), financing=ILLUSTRATIVE)
    tick = EURUSD_RULES.tick
    assert p.entry is not None and p.stop is not None and p.target is not None and p.units is not None and p.loss_per_unit is not None

    assert all(price % tick == 0 for price in (p.entry, p.stop, p.target))
    assert p.units % 1000 == 0 and p.units >= 1000
    assert p.risk_budget == D("50.00")  # 0.5 % of 10 000
    assert p.estimated_loss_at_stop is not None and D("0") < p.estimated_loss_at_stop <= p.risk_budget
    assert (p.units + 1000) * p.loss_per_unit > D("50")  # one more lot would not fit the budget
    assert p.estimated_loss_at_stop == (p.units * p.loss_per_unit).quantize(D("0.01"))
    assert p.gain_per_unit is not None and p.gain_per_unit >= 3 * p.loss_per_unit  # compared exactly, not after rounding
    assert p.margin_required == (p.units * p.entry / 30).quantize(D("0.01"))


def test_costs_and_financing_follow_the_configured_assumption() -> None:
    e = evaluation()
    none, illustrative, verified = propose(e), propose(e, financing=ILLUSTRATIVE), propose(e, financing=VERIFIED)

    assert (none.financing_status, none.net_reward_risk, none.net_3r_verified) == (FinancingStatus.NOT_VERIFIED, None, False)
    assert FINANCING_NOT_VERIFIED in none.flags and none.planned_financing_cost is None
    assert (illustrative.financing_status, illustrative.net_3r_verified) == (FinancingStatus.ILLUSTRATIVE, False)
    assert FINANCING_NOT_VERIFIED in illustrative.flags and illustrative.net_reward_risk is not None and illustrative.net_reward_risk >= 3
    assert (verified.financing_status, verified.net_3r_verified) == (FinancingStatus.VERIFIED, True) and FINANCING_NOT_VERIFIED not in verified.flags
    assert illustrative.target > none.target and illustrative.units <= none.units  # type: ignore[operator]
    assert none.stop == illustrative.stop and none.entry == illustrative.entry
    assert none.spread_cost == (none.units * SPREAD).quantize(D("0.01"))  # type: ignore[operator]
    assert none.net_reward_risk_before_financing is not None and none.net_reward_risk_before_financing >= 3


# --- proposal -> settlement ---


@pytest.mark.parametrize("sign", [1, -1])
def test_a_proposal_settles_at_its_stop_its_target_and_the_market(sign: int) -> None:
    p = propose(evaluation(sign=sign), financing=ILLUSTRATIVE)
    assert p.quote_time is not None and p.horizon_end is not None and p.entry is not None and p.stop is not None and p.target is not None

    at_stop = settle(p, ExitReason.STOP, p.horizon_end, financing=ILLUSTRATIVE)
    at_target = settle(p, ExitReason.TARGET, p.horizon_end, financing=ILLUSTRATIVE)
    soon = settle(p, ExitReason.TARGET, p.quote_time + 3 * HOUR, financing=ILLUSTRATIVE)
    in_time = settle(p, ExitReason.TIME, p.horizon_end, market_price=p.entry, financing=ILLUSTRATIVE)

    assert (at_stop.net_pnl, at_stop.nights, at_stop.exceeded_budget, at_stop.loss_beyond_estimate) == (-p.estimated_loss_at_stop, 5, False, D("0"))  # type: ignore[operator]
    assert at_stop.exit_price == p.stop - sign * D(SLIP)
    assert (at_target.net_pnl, at_target.exit_price) == (p.expected_net_profit, p.target)
    assert (soon.nights, soon.exit_price) == (0, p.target) and soon.net_pnl > at_target.net_pnl  # fewer nights; the target did not move
    assert in_time.exit_price == p.entry - sign * D(SLIP) and in_time.net_pnl < 0
    assert all(r.proposal_id == p.proposal_id and r.hypothetical for r in (at_stop, at_target, soon, in_time))


@pytest.mark.parametrize("sign", [1, -1])
def test_a_gap_through_the_stop_loses_more_than_the_estimate_and_can_exceed_the_budget(sign: int) -> None:
    p = propose(evaluation(sign=sign))
    assert p.stop is not None and p.quote_time is not None and p.estimated_loss_at_stop is not None and p.risk_budget is not None
    beyond = p.stop - sign * D("0.00300")

    gapped = settle(p, ExitReason.STOP, p.quote_time + 50 * HOUR, market_price=beyond)
    not_reached = settle(p, ExitReason.STOP, p.quote_time + 50 * HOUR, market_price=p.stop + sign * D("0.00010"))

    assert gapped.gap and gapped.exit_price == beyond - sign * D(SLIP)
    assert -gapped.net_pnl > p.risk_budget >= p.estimated_loss_at_stop and gapped.exceeded_budget
    assert gapped.loss_beyond_estimate == -gapped.net_pnl - p.estimated_loss_at_stop > 0
    assert not not_reached.gap and not_reached.net_pnl == -p.estimated_loss_at_stop


def test_an_unknown_order_of_stop_and_target_is_indeterminate() -> None:
    p = propose(evaluation())
    assert p.quote_time is not None

    unknown = indeterminate(p, p.quote_time + 4 * HOUR, "el stop y el objetivo caben en la misma vela")

    assert not unknown.valid_for_metrics and unknown.proposal_id == p.proposal_id
    assert (unknown.conservative.reason, unknown.optimistic.reason) == (ExitReason.STOP, ExitReason.TARGET)
    assert unknown.conservative.net_pnl < 0 < unknown.optimistic.net_pnl and unknown.equity_pnl == unknown.conservative.net_pnl


# --- estimated and observed data ---


def test_the_proposal_takes_the_weakest_kind_and_the_result_inherits_it() -> None:
    observed_signal, estimated_signal = evaluation(kind=QuoteKind.OBSERVED), evaluation(kind=QuoteKind.ESTIMATED)
    assert observed_signal.last_close is not None
    yahoo = estimated_from_mid("EURUSD", TUESDAY + timedelta(seconds=20), observed_signal.last_close, EURUSD_RULES.assumed_spread, "Yahoo Finance H1")

    observed = propose(observed_signal, quote_for(observed_signal, QuoteKind.OBSERVED))
    estimated_bars = propose(estimated_signal, quote_for(estimated_signal, QuoteKind.OBSERVED))
    estimated_quote = propose(observed_signal, yahoo)
    synthetic = propose(evaluation())

    assert observed.data_kind is QuoteKind.OBSERVED and ESTIMATED_PRICE not in observed.flags and SYNTHETIC_DATA not in observed.flags and observed.hypothetical
    assert estimated_bars.data_kind is QuoteKind.ESTIMATED and ESTIMATED_PRICE in estimated_bars.flags
    assert estimated_quote.data_kind is QuoteKind.ESTIMATED and ESTIMATED_PRICE in estimated_quote.flags and "spread asumido" in (estimated_quote.quote_source or "")
    assert synthetic.data_kind is QuoteKind.SYNTHETIC and SYNTHETIC_DATA in synthetic.flags
    result = settle(estimated_quote, ExitReason.TARGET, yahoo.timestamp + HOUR)
    assert result.data_kind is QuoteKind.ESTIMATED and ESTIMATED_PRICE in result.flags


# --- what is not a signal, and the rules of the engine ---


def test_only_a_confirmed_signal_is_proposed() -> None:
    not_confirmed = evaluation(index=847)  # cross up, trend in order, RSI 48.15
    no_cross = evaluation(index=848)
    rejected_data = evaluate(pullbacks()[:100], TUESDAY, symbol="EURUSD", data_kind=QuoteKind.SYNTHETIC, data_source=SOURCE, is_open=always_open)

    for e in (not_confirmed, no_cross, rejected_data):
        p = build_proposal(e, None, None, account=fresh())
        assert (p.status, tuple(p.rejections), p.signal_id, p.direction, p.entry) == (ProposalStatus.REJECTED, (Rejection.NOT_A_SIGNAL,), None, None, None)
    assert not_confirmed.outcome is Outcome.NOT_CONFIRMED and rejected_data.outcome is Outcome.DATA_REJECTED


def test_session_quote_and_drift_rules_apply_to_this_strategy_too() -> None:
    e = evaluation()
    friday = evaluation(close=datetime(2026, 12, 4, 18, 0, tzinfo=UTC))  # Friday 13:00 New York
    assert e.last_close is not None and e.atr is not None
    moved = Quote("EURUSD", TUESDAY + timedelta(seconds=20), e.last_close + e.atr, e.last_close + e.atr + SPREAD, QuoteKind.SYNTHETIC, "prueba")

    assert tuple(propose(e, quote_for(e, after=timedelta(minutes=6))).rejections) == (Rejection.SIGNAL_EXPIRED,)
    assert tuple(propose(e, quote_for(e, after=-timedelta(seconds=1))).rejections) == (Rejection.QUOTE_BEFORE_CLOSE,)
    assert tuple(propose(e, quote_for(e, symbol="GBPUSD")).rejections) == (Rejection.WRONG_INSTRUMENT,)
    assert tuple(propose(e, moved).rejections) == (Rejection.PRICE_MOVED,)
    assert tuple(propose(friday).rejections) == (Rejection.BLOCKED_FRIDAY,)


def test_one_position_per_pair_within_the_strategys_own_account() -> None:
    """Each strategy has its own hypothetical account: a position of the
    other strategy is not in it and blocks nothing. Inside the account, a
    second signal in the pair - opposite or not - is recorded as rejected."""
    long_, short = evaluation(sign=1), evaluation(sign=-1)
    holding = AccountState(D("10000"), D("10000"), open_positions=[OpenPosition("EURUSD", D("40"), D("900"), usd_direction=-1)])

    assert propose(long_, account=fresh()).accepted and propose(short, account=fresh()).accepted
    for e in (long_, short):
        p = propose(e, account=holding)
        assert p.status is ProposalStatus.REJECTED and Rejection.POSITION_OPEN in p.rejections and p.signal_id == e.signal_id


# --- next to strategy A ---


def test_the_engine_treats_both_strategies_alike_and_keeps_their_proposals_apart() -> None:
    """The same candle, direction, ATR and quote give the same levels and
    money whichever strategy raised the signal; only the identifiers differ."""
    b = evaluation()
    a = ema_cross.SignalEvaluation(
        strategy_id=ema_cross.STRATEGY_ID, parameters_sha256=ema_cross.fingerprint(), symbol=b.symbol, as_of=b.as_of, candle_open=b.candle_open,
        candle_close=b.candle_close, outcome=Outcome.LONG, reasons=(), detail="", data_kind=b.data_kind, data_source=b.data_source, bars_received=300,
        forming_bars_excluded=0, bars_used=300, last_close=b.last_close, atr=b.atr, rsi=b.rsi,
    )
    quote = quote_for(b)
    from_a, from_b = propose(a, quote, financing=ILLUSTRATIVE), propose(b, quote, financing=ILLUSTRATIVE)

    def numbers(p: TradeProposal) -> tuple[object, ...]:
        return (p.status, p.direction, p.entry, p.stop, p.target, p.units, p.estimated_loss_at_stop, p.expected_net_profit, p.margin_required, tuple(p.flags))

    assert numbers(from_a) == numbers(from_b) and from_b.accepted
    assert from_a.proposal_id.startswith("ema-rsi-atr-forex@0.1.0|") and from_b.proposal_id.startswith("ema-3-9-20-50-120-forex@0.1.0|")
    assert from_a.proposal_id != from_b.proposal_id and from_a.proposal_id.split("|")[1:] == from_b.proposal_id.split("|")[1:]
