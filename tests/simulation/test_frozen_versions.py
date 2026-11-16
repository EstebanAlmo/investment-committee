"""Golden tests: strategy v1.0.0 and backtest engine v2.0.0 must not change
silently (the AI committee phase is not allowed to touch them).

If a future change is intended, bump STRATEGY_VERSION or
BACKTEST_ENGINE_VERSION and update these expectations in the same commit.
"""

from __future__ import annotations

from decimal import Decimal

from investment_committee.simulation import (
    BACKTEST_ENGINE_VERSION,
    EURUSD,
    STRATEGY_VERSION,
    AccountAssumptions,
    AccountStatus,
    StrategyParameters,
    TradeDirection,
    TradingCosts,
    run_backtest,
)

from ._support import closing_time, regime_series, signal_for, uptrend


def test_versions_are_unchanged() -> None:
    assert STRATEGY_VERSION == "1.0.0"
    assert BACKTEST_ENGINE_VERSION == "2.0.0"


def test_default_parameters_are_unchanged() -> None:
    p, a, c = StrategyParameters(), AccountAssumptions(), TradingCosts()
    assert (p.sma_fast, p.sma_slow, p.rsi_period, p.atr_period, p.range_lookback) == (20, 50, 14, 14, 24)
    assert (p.rsi_long_min, p.rsi_long_max, p.rsi_short_min, p.rsi_short_max) == (50, 70, 30, 50)
    assert (p.atr_min_pips, p.atr_max_pips, p.stop_atr_multiple, p.target_atr_multiple) == (3, 40, Decimal("1.5"), Decimal("3.0"))
    assert (p.min_net_reward_risk, p.max_cost_fraction_of_risk, p.expiry_bars) == (Decimal("1.5"), Decimal("0.25"), 24)
    assert (a.capital, a.risk_fraction, a.leverage, a.margin_reserve_fraction, a.stop_out_level, a.max_drawdown_halt) == (
        10000, Decimal("0.01"), 30, Decimal("0.25"), Decimal("0.5"), Decimal("0.30"),
    )
    assert (c.spread_pips, c.slippage_pips, c.commission_per_100k) == (Decimal("1.0"), Decimal("0.2"), 0)


def test_strategy_signal_is_unchanged() -> None:
    signal = signal_for(uptrend())
    plan = signal.plan
    assert signal.direction is TradeDirection.LONG and plan is not None
    assert (plan.entry_price, plan.stop_loss, plan.take_profit, plan.units, plan.net_reward_risk) == (
        Decimal("1.10357"), Decimal("1.10230"), Decimal("1.10612"), 77000, Decimal("1.98"),
    )


def test_backtest_results_are_unchanged() -> None:
    bars = regime_series()
    result = run_backtest(
        bars, instrument=EURUSD, parameters=StrategyParameters(), costs=TradingCosts(),
        account=AccountAssumptions(), generated_at=closing_time(bars),
    )
    m = result.metrics
    assert (m.trades, m.wins, m.losses) == (8, 5, 3)
    assert (m.net_pnl, m.max_drawdown, m.total_costs) == (Decimal("692.44"), Decimal("-223.86"), Decimal("78.38"))
    assert result.final_balance == Decimal("10692.44")
    assert result.status is AccountStatus.ACTIVE
