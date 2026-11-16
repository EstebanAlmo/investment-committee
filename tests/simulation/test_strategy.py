"""Tests for simulation.strategy: signals, gates, levels, costs, sizing."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from investment_committee.domain import PriceBar
from investment_committee.simulation import (
    SignalReason,
    StrategyParameters,
    TradeDirection,
    TradingCosts,
    Trend,
)

from ._support import HOUR, closing_time, downtrend, sideways, signal_for, uptrend


def test_uptrend_with_confirming_momentum_is_a_long_signal() -> None:
    signal = signal_for(uptrend())

    assert signal.direction is TradeDirection.LONG
    assert signal.reasons == (SignalReason.TREND_UP, SignalReason.MOMENTUM_CONFIRMS, SignalReason.VOLATILITY_OK)
    assert signal.reading.trend is Trend.UP
    assert Decimal("50") < signal.reading.rsi < Decimal("70")  # type: ignore[operator]


def test_downtrend_with_confirming_momentum_is_a_short_signal() -> None:
    signal = signal_for(downtrend())

    assert signal.direction is TradeDirection.SHORT
    assert signal.reading.trend is Trend.DOWN


def test_sideways_market_is_no_trade_without_a_plan() -> None:
    signal = signal_for(sideways())

    assert signal.direction is TradeDirection.NO_TRADE
    assert SignalReason.TREND_SIDEWAYS in signal.reasons
    assert signal.plan is None


def test_long_levels_follow_the_documented_formulas() -> None:
    signal = signal_for(uptrend())
    plan = signal.plan
    assert plan is not None and signal.reading.atr is not None

    quote = signal.reading.quote_price
    assert quote == Decimal("1.10350")  # close of the last closed bar (mid)
    # entry = quote + half spread (0.5 pip) + slippage (0.2 pip): a simulated ask fill
    assert plan.entry_price == Decimal("1.10357")
    assert plan.stop_loss == (plan.entry_price - Decimal("1.5") * signal.reading.atr).quantize(Decimal("0.00001"))
    assert plan.take_profit == (plan.entry_price + Decimal("3.0") * signal.reading.atr).quantize(Decimal("0.00001"))
    assert plan.stop_loss < plan.entry_price < plan.take_profit
    assert Decimal("1.98") <= plan.gross_reward_risk <= Decimal("2.02")  # 3.0 / 1.5 ATR, after rounding to 0.1 pip


def test_short_levels_mirror_the_long_ones() -> None:
    plan = signal_for(downtrend()).plan
    assert plan is not None

    assert plan.entry_price == Decimal("1.09643")  # quote - half spread - slippage: a bid fill
    assert plan.take_profit < plan.entry_price < plan.stop_loss


def test_sizing_risks_the_configured_fraction_of_capital() -> None:
    plan = signal_for(uptrend()).plan
    assert plan is not None

    assert plan.units % 1000 == 0
    assert plan.risk_amount <= Decimal("100.00")  # 1 % of 10,000
    assert plan.risk_amount > Decimal("90")
    assert plan.margin_required == (plan.notional / Decimal("30")).quantize(Decimal("0.01"))


def test_costs_are_reported_separately_from_prices() -> None:
    plan = signal_for(uptrend(), costs=TradingCosts(spread_pips=Decimal("1.0"), commission_per_100k=Decimal("7"))).plan
    assert plan is not None

    assert plan.spread_cost == (Decimal(plan.units) * Decimal("0.0001")).quantize(Decimal("0.01"))
    assert plan.commission == (Decimal("7") * Decimal(plan.units) / Decimal("100000")).quantize(Decimal("0.01"))
    assert plan.net_reward_risk < plan.gross_reward_risk  # costs make the real ratio worse


def test_a_wider_spread_moves_the_entry_and_can_cancel_the_trade() -> None:
    narrow = signal_for(uptrend(), costs=TradingCosts(spread_pips=Decimal("1")))
    wide = signal_for(uptrend(), costs=TradingCosts(spread_pips=Decimal("4")))

    assert narrow.is_trade
    assert wide.direction is TradeDirection.NO_TRADE
    assert wide.reasons[-1] is SignalReason.COSTS_TOO_HIGH


def test_minimum_reward_risk_gate() -> None:
    signal = signal_for(uptrend(), parameters=StrategyParameters(min_net_reward_risk=Decimal("2.5")))

    assert signal.direction is TradeDirection.NO_TRADE
    assert signal.reasons[-1] is SignalReason.REWARD_RISK_TOO_LOW


def test_tiny_account_cannot_size_a_position() -> None:
    from investment_committee.simulation import AccountAssumptions

    signal = signal_for(uptrend(), account=AccountAssumptions(capital=Decimal("50")))

    assert signal.direction is TradeDirection.NO_TRADE
    assert signal.reasons[-1] is SignalReason.POSITION_TOO_SMALL


def test_insufficient_data_is_rejected() -> None:
    signal = signal_for(uptrend(30))

    assert signal.direction is TradeDirection.NO_TRADE
    assert signal.reasons == (SignalReason.INSUFFICIENT_DATA,)
    assert signal.bars_used == 30


def test_stale_data_is_rejected() -> None:
    bars = uptrend()

    signal = signal_for(bars, as_of=closing_time(bars) + timedelta(hours=4))

    assert signal.reasons == (SignalReason.STALE_DATA,)
    assert signal.plan is None


def test_closed_market_is_rejected() -> None:
    bars = uptrend()
    saturday_noon = bars[0].timestamp + timedelta(days=5, hours=12)  # WEEK_START is a Monday

    signal = signal_for(bars, as_of=saturday_noon)

    assert signal.reasons == (SignalReason.MARKET_CLOSED,)


def test_out_of_order_bars_are_rejected_as_inconsistent() -> None:
    bars = uptrend()
    bars[40], bars[41] = bars[41], bars[40]

    signal = signal_for(bars)

    assert signal.reasons == (SignalReason.INCONSISTENT_DATA,)


def test_the_bar_still_forming_is_ignored() -> None:
    bars = uptrend()
    forming = PriceBar(
        timestamp=closing_time(bars), open=Decimal("1.2"), high=Decimal("1.3"), low=Decimal("1.0"), close=Decimal("1.0"), volume=0
    )

    with_forming = signal_for([*bars, forming], as_of=closing_time(bars) + timedelta(minutes=30))
    without = signal_for(bars)

    assert with_forming.reading == without.reading
    assert with_forming.data_as_of == closing_time(bars)


def test_volatility_too_low_and_too_high() -> None:
    low = signal_for(uptrend(), parameters=StrategyParameters(atr_min_pips=Decimal("20")))
    high = signal_for(uptrend(), parameters=StrategyParameters(atr_max_pips=Decimal("5")))

    assert SignalReason.VOLATILITY_TOO_LOW in low.reasons and low.plan is None
    assert SignalReason.VOLATILITY_TOO_HIGH in high.reasons and high.plan is None


def test_signal_is_deterministic() -> None:
    assert signal_for(uptrend()) == signal_for(uptrend())


@pytest.mark.parametrize("cut", [60, 64, 69])
def test_no_look_ahead_future_bars_never_change_a_signal(cut: int) -> None:
    bars = uptrend()
    as_of = bars[cut - 1].timestamp + HOUR
    altered_future = [replace(bar, close=bar.open) for bar in bars[cut:]]

    assert signal_for(bars[:cut], as_of=as_of) == signal_for(bars[:cut] + altered_future, as_of=as_of)
