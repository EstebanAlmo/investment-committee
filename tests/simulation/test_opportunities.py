"""Tests for simulation.opportunities - the deterministic core of the
Opportunity Scanner. Synthetic bars only; no network."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Sequence

import pytest

from investment_committee.domain import PriceBar
from investment_committee.simulation import (
    DEFAULT_UNIVERSE,
    EURUSD,
    EURUSD_PROFILE,
    GBPUSD_PROFILE,
    NOT_VALIDATED,
    USDJPY_PROFILE,
    AccountAssumptions,
    AccountState,
    AIReview,
    Category,
    Conversion,
    HistoricalValidation,
    InstrumentProfile,
    InstrumentSpec,
    OpenPosition,
    RiskLimits,
    ScanStrategy,
    SignalReason,
    TradeDirection,
    TradeStatus,
    TradingCosts,
    TrendMomentumVolatilityStrategy,
    ValidationStatus,
    apply_ai_objection,
    assess_instrument,
    assess_segmented_backtest,
    conversion_for,
    daily_loss_budget,
    hypothetical_outcome,
    plan_in_account_currency,
    prepare_bars,
    rank_and_allocate,
    run_segmented_backtest,
    StrategyParameters,
)

from ._support import HOUR, bars_from_moves, closing_time, downtrend, sideways, uptrend

STRATEGY = TrendMomentumVolatilityStrategy()
LIMITS = RiskLimits()
FRESH = AccountState.fresh(LIMITS.capital)
VALIDATED = HistoricalValidation(ValidationStatus.VALIDADA, "prueba", "fixture")


def jpy_bars(direction: int = 1, count: int = 70) -> list[PriceBar]:
    big, small = Decimal("0.10") * direction, Decimal("-0.08") * direction
    return bars_from_moves([str(big) if i % 2 == 0 else str(small) for i in range(count)], first_close="150.000")


def assess(profile: InstrumentProfile, bars: Sequence[PriceBar], *, account: AccountState = FRESH, now: datetime | None = None,
           validation: HistoricalValidation = NOT_VALIDATED, strategy: ScanStrategy = STRATEGY, limits: RiskLimits = LIMITS):
    now = now or closing_time(bars)
    closed, data = prepare_bars(bars, strategy.timeframe, now, "fixture")
    return assess_instrument(profile, closed, data, now, strategy=strategy, limits=limits, account=account, validation=validation, signal_id="s-1")


# --- universe, symbols, pips ---


def test_default_universe_uses_canonical_yahoo_forex_symbols() -> None:
    from investment_committee.conversation.symbol_resolver import canonical_symbol

    assert [p.symbol for p in DEFAULT_UNIVERSE] == ["EURUSD=X", "GBPUSD=X", "USDJPY=X"]
    for profile in DEFAULT_UNIVERSE:
        assert canonical_symbol(profile.spec.display_name) == profile.symbol
        assert canonical_symbol(profile.symbol.lower()) == profile.symbol


def test_pip_rules_and_currencies_differ_per_pair() -> None:
    assert (EURUSD_PROFILE.spec.pip_size, EURUSD_PROFILE.quote_currency, EURUSD_PROFILE.base_currency) == (Decimal("0.0001"), "USD", "EUR")
    assert (GBPUSD_PROFILE.spec.pip_size, GBPUSD_PROFILE.quote_currency) == (Decimal("0.0001"), "USD")
    assert (USDJPY_PROFILE.spec.pip_size, USDJPY_PROFILE.quote_currency, USDJPY_PROFILE.base_currency) == (Decimal("0.01"), "JPY", "USD")
    assert USDJPY_PROFILE.spec.price_places == 3
    assert GBPUSD_PROFILE.costs.spread_pips > EURUSD_PROFILE.costs.spread_pips  # assumptions are per pair


def test_conversion_kind_per_pair() -> None:
    cross = InstrumentProfile(InstrumentSpec("EURGBP=X", "EUR/GBP", Decimal("0.0001"), "GBP"), "EUR", TradingCosts())
    assert conversion_for(EURUSD_PROFILE) is Conversion.DIRECT
    assert conversion_for(USDJPY_PROFILE) is Conversion.INVERSE
    assert conversion_for(cross) is None


def test_a_cross_pair_is_not_eligible_instead_of_getting_wrong_figures() -> None:
    cross = InstrumentProfile(InstrumentSpec("EURGBP=X", "EUR/GBP", Decimal("0.0001"), "GBP"), "EUR", TradingCosts())

    scan = assess(cross, uptrend())

    assert scan.category is Category.NOT_ELIGIBLE and scan.signal is None and scan.money is None
    assert "tipo de cambio cruzado" in scan.notes[0]


# --- money ---


def test_usd_quoted_pair_money_is_already_usd() -> None:
    scan = assess(EURUSD_PROFILE, uptrend())

    assert scan.category is Category.EXPLORATORY
    plan, money = scan.signal.plan, scan.money  # type: ignore[union-attr]
    assert money.conversion is Conversion.DIRECT  # type: ignore[union-attr]
    assert (money.risk_amount, money.reward_amount, money.margin_required) == (plan.risk_amount, plan.reward_amount, plan.margin_required)  # type: ignore[union-attr]


@pytest.mark.parametrize("direction", [1, -1])
def test_usd_jpy_loss_at_stop_is_converted_conservatively_and_stays_within_the_limit(direction: int) -> None:
    scan = assess(USDJPY_PROFILE, jpy_bars(direction))

    assert scan.category is Category.EXPLORATORY
    plan, money = scan.signal.plan, scan.money  # type: ignore[union-attr]
    assert scan.signal.direction is (TradeDirection.LONG if direction == 1 else TradeDirection.SHORT)  # type: ignore[union-attr]
    slippage = USDJPY_PROFILE.costs.slippage_pips * USDJPY_PROFILE.spec.pip_size
    loss_jpy = plan.units * (abs(plan.entry_price - plan.stop_loss) + slippage)  # type: ignore[union-attr]
    worst_usd = max(loss_jpy / plan.stop_loss, loss_jpy / plan.entry_price)  # type: ignore[union-attr]
    assert money.risk_amount >= worst_usd.quantize(Decimal("0.01")) - Decimal("0.01")  # type: ignore[union-attr]
    assert money.risk_amount <= LIMITS.risk_per_trade * LIMITS.capital  # type: ignore[union-attr]
    assert money.margin_required == (Decimal(plan.units) / LIMITS.leverage).quantize(Decimal("0.01"))  # type: ignore[union-attr]
    assert "conversión conservadora" in money.conversion_note  # type: ignore[union-attr]


def test_inverse_conversion_arithmetic() -> None:
    signal = assess(USDJPY_PROFILE, jpy_bars()).signal
    plan = signal.plan  # type: ignore[union-attr]

    money = plan_in_account_currency(plan, Conversion.INVERSE)  # type: ignore[arg-type]

    assert money.risk_amount == (plan.risk_amount / min(plan.entry_price, plan.stop_loss)).quantize(Decimal("0.01"))  # type: ignore[union-attr]
    assert money.reward_amount == (plan.reward_amount / max(plan.entry_price, plan.take_profit)).quantize(Decimal("0.01"))  # type: ignore[union-attr]


def test_spread_and_slippage_set_the_simulated_entry_and_the_costs() -> None:
    bars = uptrend()
    scan = assess(GBPUSD_PROFILE, bars)

    plan, costs, pip = scan.signal.plan, GBPUSD_PROFILE.costs, GBPUSD_PROFILE.spec.pip_size  # type: ignore[union-attr]
    expected_entry = (bars[-1].close + costs.spread_pips * pip / 2 + costs.slippage_pips * pip).quantize(Decimal("0.00001"), rounding=ROUND_HALF_EVEN)
    assert plan.entry_price == expected_entry > bars[-1].close  # type: ignore[union-attr]  # a buyer pays above the mid
    assert plan.spread_cost == (plan.units * costs.spread_pips * pip).quantize(Decimal("0.01"))  # type: ignore[union-attr]
    assert plan.slippage_cost == (plan.units * costs.slippage_pips * pip * 2).quantize(Decimal("0.01"))  # type: ignore[union-attr]
    assert scan.money.total_costs == plan.spread_cost + plan.slippage_cost + plan.commission  # type: ignore[union-attr]


def test_costs_too_high_is_a_discarded_technical_signal_not_silence() -> None:
    expensive = replace(EURUSD_PROFILE, costs=TradingCosts(spread_pips=Decimal("6")))

    scan = assess(expensive, uptrend())

    assert scan.category is Category.SIGNAL_REJECTED and scan.technical_direction is TradeDirection.LONG
    assert SignalReason.COSTS_TOO_HIGH in scan.signal.reasons  # type: ignore[union-attr]


# --- closed bars only ---


def test_future_and_forming_bars_are_never_used() -> None:
    bars = uptrend()
    now = closing_time(bars) + timedelta(minutes=20)
    last = bars[-1]
    forming = PriceBar(last.timestamp + HOUR, last.close, last.close + Decimal("0.01"), last.close, last.close + Decimal("0.01"), 0)
    future = replace(forming, timestamp=last.timestamp + 5 * HOUR)

    closed, data = prepare_bars([*bars, forming, future], HOUR, now, "fixture")

    assert closed == bars
    assert (data.future_bars_dropped, data.forming_bars_excluded, data.closed_bars) == (1, 1, len(bars))
    assert data.last_closed_bar_close == closing_time(bars) <= now
    scan = assess(EURUSD_PROFILE, [*bars, forming, future], now=now)
    assert scan.signal.data_as_of == closing_time(bars)  # type: ignore[union-attr]
    assert scan.signal.reading.quote_price == last.close  # type: ignore[union-attr]  # not the forming bar's price


def test_incomplete_history_is_no_signal_with_a_reason() -> None:
    scan = assess(EURUSD_PROFILE, uptrend(30))

    assert scan.category is Category.NO_SIGNAL
    assert scan.signal.reasons == (SignalReason.INSUFFICIENT_DATA,)  # type: ignore[union-attr]


# --- per-trade, daily and aggregate limits ---


class OversizedStrategy(TrendMomentumVolatilityStrategy):
    """Ignores the account it is given and sizes for twice the capital - the
    per-trade control must catch it, whatever the strategy does."""

    def evaluate(self, bars, as_of, *, signal_id, profile, account):  # type: ignore[no-untyped-def]
        return super().evaluate(bars, as_of, signal_id=signal_id, profile=profile, account=replace(account, capital=account.capital * 2))


def test_per_trade_risk_is_enforced_by_python_not_by_the_strategy() -> None:
    scan = assess(EURUSD_PROFILE, uptrend(), strategy=OversizedStrategy())

    assert scan.category is Category.SIGNAL_REJECTED
    check = next(c for c in scan.checks if c.name == "riesgo por operación")
    assert not check.passed and "máximo de 50,00 USD" in check.detail


def test_risk_limits_are_validated() -> None:
    with pytest.raises(ValueError):
        RiskLimits(risk_per_trade=Decimal("0.05"))
    with pytest.raises(ValueError):
        RiskLimits(max_daily_loss=Decimal("0.001"))
    with pytest.raises(ValueError):
        RiskLimits(max_visible=4)


def test_daily_loss_limit_reached_blocks_every_candidate() -> None:
    account = AccountState(equity=Decimal("9850"), day_start_equity=Decimal("10000"), realized_pnl_today=Decimal("-150"))
    scans = [assess(EURUSD_PROFILE, uptrend(), account=account), assess(GBPUSD_PROFILE, downtrend(), account=account)]

    ranked = rank_and_allocate(scans, LIMITS, account)

    assert daily_loss_budget(LIMITS, account) == 0
    assert all(s.category is Category.SIGNAL_REJECTED and s.rank is None for s in ranked)
    assert all("límite de pérdida diaria" in s.notes[-1] for s in ranked)


def test_gains_today_do_not_enlarge_the_daily_budget() -> None:
    account = AccountState(equity=Decimal("10300"), day_start_equity=Decimal("10000"), realized_pnl_today=Decimal("300"))

    assert daily_loss_budget(LIMITS, account) == Decimal("150.000")


def test_aggregate_risk_counts_open_positions_and_earlier_candidates() -> None:
    account = AccountState(
        equity=Decimal("10000"), day_start_equity=Decimal("10000"), open_positions=(OpenPosition("USDCAD=X", Decimal("60"), Decimal("500"), usd_direction=1),)
    )
    scans = [assess(EURUSD_PROFILE, uptrend(), account=account), assess(GBPUSD_PROFILE, downtrend(), account=account), assess(USDJPY_PROFILE, jpy_bars(), account=account)]

    ranked = rank_and_allocate(scans, LIMITS, account)

    admitted = [s for s in ranked if s.rank is not None]
    rejected = [s for s in ranked if s.category is Category.SIGNAL_REJECTED]
    assert len(admitted) == 1  # 60 open + ~49 = ~109; a second ~49 would pass 150
    assert len(rejected) == 2 and all("riesgo agregado" in s.notes[-1] for s in rejected)
    assert account.open_risk + sum(s.money.risk_amount for s in admitted) <= daily_loss_budget(LIMITS, account)  # type: ignore[union-attr]


def test_an_open_position_on_the_same_instrument_blocks_a_new_one() -> None:
    account = AccountState(
        equity=Decimal("10000"), day_start_equity=Decimal("10000"), open_positions=(OpenPosition("EURUSD=X", Decimal("40"), Decimal("1000")),)
    )

    scan = assess(EURUSD_PROFILE, uptrend(), account=account)

    assert scan.category is Category.SIGNAL_REJECTED
    assert "posición abierta en el mismo instrumento" in scan.notes[0]


def test_aggregate_margin_is_enforced() -> None:
    account = AccountState(
        equity=Decimal("10000"), day_start_equity=Decimal("10000"), open_positions=(OpenPosition("AUDUSD=X", Decimal("0"), Decimal("7000")),)
    )
    scans = [assess(EURUSD_PROFILE, uptrend(), account=account)]

    (result,) = rank_and_allocate(scans, LIMITS, account)

    assert result.category is Category.SIGNAL_REJECTED and "margen agregado" in result.notes[-1]


# --- historical validation ---


def test_unvalidated_candidates_are_exploratory_and_validated_ones_rank_first() -> None:
    exploratory = assess(EURUSD_PROFILE, uptrend())
    validated = assess(GBPUSD_PROFILE, downtrend(), validation=VALIDATED)

    ranked = rank_and_allocate([exploratory, validated], LIMITS, FRESH)

    assert exploratory.category is Category.EXPLORATORY and validated.category is Category.VALIDATED
    by_symbol = {s.symbol: s for s in ranked}
    assert by_symbol["GBPUSD=X"].rank == 1 and by_symbol["EURUSD=X"].rank == 2
    assert exploratory.score.components[-1].points == 0 and validated.score.components[-1].points == 3  # type: ignore[union-attr]


def _segmented():  # type: ignore[no-untyped-def]
    bars = bars_from_moves(["0.0005" if i % 2 == 0 else "-0.0004" for i in range(600)])
    return run_segmented_backtest(
        bars, instrument=EURUSD, parameters=StrategyParameters(), costs=TradingCosts(), account=AccountAssumptions(), generated_at=closing_time(bars)
    )


def test_a_thin_or_negative_backtest_is_not_validated() -> None:
    validation = assess_segmented_backtest(_segmented())

    assert validation.status is ValidationStatus.NO_VALIDADA


def test_validation_requires_every_out_of_sample_segment_to_be_positive() -> None:
    segmented = _segmented()
    good = [replace(s, metrics=replace(s.metrics, trades=40, expectancy=Decimal("5"), profit_factor=Decimal("1.3"))) for s in segmented.segments]
    one_bad = [good[0], good[1], replace(good[2], metrics=replace(good[2].metrics, expectancy=Decimal("-1")))]

    assert assess_segmented_backtest(replace(segmented, segments=good)).status is ValidationStatus.VALIDADA
    assert assess_segmented_backtest(replace(segmented, segments=one_bad)).status is ValidationStatus.NO_VALIDADA
    # the development segment alone never validates anything
    dev_only_good = [good[0], *(replace(s, metrics=replace(s.metrics, trades=5)) for s in good[1:])]
    assert assess_segmented_backtest(replace(segmented, segments=dev_only_good)).status is ValidationStatus.NO_VALIDADA


# --- ranking ---


def test_ranking_is_deterministic_and_independent_of_universe_order() -> None:
    scans = [assess(EURUSD_PROFILE, uptrend()), assess(GBPUSD_PROFILE, downtrend()), assess(USDJPY_PROFILE, jpy_bars())]

    first = [(s.symbol, s.rank) for s in rank_and_allocate(scans, LIMITS, FRESH)]
    second = [(s.symbol, s.rank) for s in rank_and_allocate(list(reversed(scans)), LIMITS, FRESH)]

    assert sorted(first) == sorted(second)
    assert first == [(s.symbol, s.rank) for s in rank_and_allocate(scans, LIMITS, FRESH)]


def test_ties_break_by_symbol_and_score_is_explained() -> None:
    a = assess(EURUSD_PROFILE, uptrend())
    b = replace(a, symbol="AAAUSD=X")

    ranked = rank_and_allocate([a, b], LIMITS, AccountState.fresh(Decimal("100000")))

    assert [s.symbol for s in sorted(ranked, key=lambda s: s.rank or 0)] == ["AAAUSD=X", "EURUSD=X"]
    labels = [c.label for c in a.score.components]  # type: ignore[union-attr]
    assert labels == ["frescura de datos", "compatibilidad estrategia-instrumento", "costos frente al riesgo", "condición de entrada",
                      "riesgo/beneficio tras costos", "uso de margen de la cuenta", "evidencia histórica"]


def test_reward_risk_alone_does_not_decide_the_order() -> None:
    eur, gbp = assess(EURUSD_PROFILE, uptrend()), assess(GBPUSD_PROFILE, uptrend())
    better_rr_worse_fit = replace(gbp, money=replace(gbp.money, reward_amount=gbp.money.reward_amount * 2))  # type: ignore[union-attr]

    ranked = {s.symbol: s.rank for s in rank_and_allocate([eur, better_rr_worse_fit], LIMITS, FRESH)}

    assert better_rr_worse_fit.money.net_reward_risk > eur.money.net_reward_risk  # type: ignore[union-attr]
    assert ranked["EURUSD=X"] == 1


def test_at_most_three_are_ranked() -> None:
    base = assess(EURUSD_PROFILE, uptrend())
    many = [replace(base, symbol=f"X{i}USD=X") for i in range(5)]

    ranked = rank_and_allocate(many, LIMITS, AccountState.fresh(Decimal("1000000")))

    assert sorted(s.rank for s in ranked if s.rank) == [1, 2, 3]
    assert sum(1 for s in ranked if s.is_candidate and s.rank is None) == 2


def test_zero_opportunities_when_nothing_signals() -> None:
    scans = [assess(EURUSD_PROFILE, sideways()), assess(GBPUSD_PROFILE, sideways())]

    ranked = rank_and_allocate(scans, LIMITS, FRESH)

    assert all(s.rank is None and s.category is Category.NO_SIGNAL for s in ranked)


# --- AI objection ---


def test_an_ai_objection_discards_and_no_objection_upgrades_nothing() -> None:
    candidate = assess(EURUSD_PROFILE, uptrend())

    objected = apply_ai_objection(candidate, AIReview("ai-1", True, "conclusión NO CONCLUYENTE"))
    supported = apply_ai_objection(candidate, AIReview("ai-2", False, "conclusión FAVORABLE"))

    assert objected.category is Category.SIGNAL_REJECTED and "objeción del comité de IA" in objected.notes[-1]
    assert supported.category is Category.EXPLORATORY  # never VALIDATED because the AI agreed
    assert supported.money == candidate.money and supported.signal == candidate.signal


# --- later evaluation without look-ahead ---


def test_hypothetical_outcome_uses_only_bars_after_the_signal() -> None:
    bars = uptrend()
    scan = assess(EURUSD_PROFILE, bars)
    signal = scan.signal
    assert signal is not None and signal.plan is not None and signal.data_as_of is not None

    nothing_yet = hypothetical_outcome(scan, bars, evaluated_at=signal.data_as_of)
    rally = bars_from_moves(["0.0040"] * 3, start=bars[-1].timestamp + HOUR, first_close=str(bars[-1].close))
    later = hypothetical_outcome(scan, [*bars, *rally], evaluated_at=closing_time(rally))
    rewritten_past = [replace(b, close=b.close, high=b.high + Decimal("0.05")) for b in bars]  # history before the signal
    same = hypothetical_outcome(scan, [*rewritten_past, *rally], evaluated_at=closing_time(rally))

    assert nothing_yet is not None and nothing_yet.status is TradeStatus.OPEN and nothing_yet.bars_observed == 0
    assert later is not None and later.status is TradeStatus.TAKE_PROFIT
    assert same == later  # bars before the signal cannot change its outcome


def test_no_signal_has_no_hypothetical_outcome() -> None:
    assert hypothetical_outcome(assess(EURUSD_PROFILE, sideways()), sideways(), closing_time(sideways())) is None



# --- shared USD exposure ---


def test_usd_direction_of_each_pair() -> None:
    from investment_committee.simulation import usd_direction

    assert usd_direction(EURUSD_PROFILE, TradeDirection.LONG) == -1  # buys EUR, sells USD
    assert usd_direction(GBPUSD_PROFILE, TradeDirection.SHORT) == 1
    assert usd_direction(USDJPY_PROFILE, TradeDirection.LONG) == 1  # buys USD
    assert usd_direction(USDJPY_PROFILE, TradeDirection.SHORT) == -1


def test_two_bets_against_the_same_dollar_are_capped() -> None:
    limits = RiskLimits(max_usd_direction_risk=Decimal("0.005"))  # room for one 0.5 % position per USD side
    eur_long, gbp_long = assess(EURUSD_PROFILE, uptrend(), limits=limits), assess(GBPUSD_PROFILE, uptrend(), limits=limits)
    jpy_short = assess(USDJPY_PROFILE, jpy_bars(-1), limits=limits)  # also short USD

    ranked = {s.symbol: s for s in rank_and_allocate([eur_long, gbp_long, jpy_short], limits, FRESH)}

    assert ranked["EURUSD=X"].rank == 1
    for symbol in ("GBPUSD=X", "USDJPY=X"):
        assert ranked[symbol].category is Category.SIGNAL_REJECTED and "exposición compartida al USD" in ranked[symbol].notes[-1]


def test_opposite_usd_sides_do_not_add_up() -> None:
    limits = RiskLimits(max_usd_direction_risk=Decimal("0.005"))
    eur_long = assess(EURUSD_PROFILE, uptrend(), limits=limits)  # short USD
    jpy_long = assess(USDJPY_PROFILE, jpy_bars(1), limits=limits)  # long USD

    ranked = rank_and_allocate([eur_long, jpy_long], limits, FRESH)

    assert sorted(s.rank for s in ranked if s.rank) == [1, 2]


def test_an_open_position_with_unknown_usd_side_counts_against_both() -> None:
    account = AccountState(equity=Decimal("10000"), day_start_equity=Decimal("10000"), open_positions=(OpenPosition("XAUUSD=X", Decimal("80"), Decimal("0")),))

    (result,) = rank_and_allocate([assess(EURUSD_PROFILE, uptrend(), account=account)], LIMITS, account)

    assert result.category is Category.SIGNAL_REJECTED and "exposición compartida al USD" in result.notes[-1]
