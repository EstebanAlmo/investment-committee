"""Tests for simulation.backtest (engine 2.0.0): capital account, margin,
liquidation, suspension, next-bar entries, reconciliation, segmentation."""

from __future__ import annotations

from dataclasses import replace

from decimal import Decimal

import pytest

from investment_committee.domain import PriceBar
from investment_committee.simulation import (
    EURUSD,
    AccountAssumptions,
    AccountStatus,
    BacktestDataError,
    BacktestResult,
    RejectionReason,
    StrategyParameters,
    TradeDirection,
    TradeStatus,
    TradingCosts,
    entry_fill,
    reconciliation_difference,
    run_backtest,
    run_segmented_backtest,
)

from ._support import HOUR, bars_from_moves, closing_time, regime_series, uptrend

PIP = Decimal("0.0001")


def _backtest(
    bars: list[PriceBar],
    account: AccountAssumptions | None = None,
    costs: TradingCosts | None = None,
    **window: int,
) -> BacktestResult:
    return run_backtest(
        bars,
        instrument=EURUSD,
        parameters=StrategyParameters(),
        costs=costs or TradingCosts(),
        account=account or AccountAssumptions(),
        generated_at=closing_time(bars),
        **window,  # type: ignore[arg-type]
    )


def _crash_after_entry() -> list[PriceBar]:
    """The uptrend's last bar triggers a LONG; the next bar fills it, then the
    market gaps 5,000 pips down - far through the stop."""
    bars = uptrend()
    last = bars[-1].close
    entry_bar = bars_from_moves(["0.0001"], start=closing_time(bars), first_close=str(last))
    crash_open = entry_bar[0].close - Decimal("0.5000")
    crash = PriceBar(
        timestamp=entry_bar[0].timestamp + HOUR, open=crash_open, high=crash_open, low=crash_open - Decimal("0.0005"),
        close=crash_open, volume=0,
    )
    return bars + entry_bar + [crash, replace(crash, timestamp=crash.timestamp + HOUR)]


# --- the books balance ---


def test_balance_reconciles_exactly() -> None:
    result = _backtest(regime_series(), costs=TradingCosts(commission_per_100k=Decimal("7")))

    assert result.metrics.trades > 0
    assert reconciliation_difference(result) == 0
    expected = result.initial_capital + sum((t.gross_pnl for t in result.trades), Decimal("0")) - result.metrics.commission_cost
    assert result.final_balance == expected
    assert result.final_equity == result.final_balance  # nothing left open
    assert result.metrics.net_pnl == (result.final_balance - result.initial_capital).quantize(Decimal("0.01"))


def test_costs_are_reported_once_and_not_subtracted_twice() -> None:
    result = _backtest(regime_series(), costs=TradingCosts(commission_per_100k=Decimal("7")))

    for trade in result.trades:
        assert trade.net_pnl == trade.gross_pnl - trade.commission  # spread/slippage live inside gross_pnl
        assert trade.spread_cost == Decimal(trade.units) * Decimal("1.0") * PIP
        fills_with_slippage = 1 if trade.outcome.status is TradeStatus.TAKE_PROFIT else 2
        assert trade.slippage_cost == Decimal(trade.units) * Decimal("0.2") * PIP * fills_with_slippage
    metrics = result.metrics
    assert metrics.total_costs == metrics.spread_cost + metrics.slippage_cost + metrics.commission_cost


def test_metrics_are_internally_consistent() -> None:
    result = _backtest(regime_series())
    m = result.metrics

    assert m.wins > 0 and m.losses > 0
    assert m.take_profits + m.stop_losses + m.expired + m.liquidations + m.closed_at_end == m.trades
    assert m.gross_profit + m.gross_loss == m.net_pnl
    assert m.expectancy == (m.net_pnl / m.trades).quantize(Decimal("0.01"))
    assert m.average_win == (m.gross_profit / m.wins).quantize(Decimal("0.01"))
    assert m.average_loss == (m.gross_loss / m.losses).quantize(Decimal("0.01"))
    assert m.net_return == ((result.final_equity - result.initial_capital) / result.initial_capital).quantize(Decimal("0.0001"))
    assert m.max_drawdown <= 0 and m.max_drawdown_fraction <= 0
    assert len(result.account_curve) == result.bars


def test_drawdown_is_measured_on_the_bar_by_bar_equity_curve() -> None:
    result = _backtest(regime_series())

    peak = result.initial_capital
    worst = Decimal("0")
    for point in result.account_curve:
        peak = max(peak, point.equity)
        worst = min(worst, point.equity - peak)
    assert result.metrics.max_drawdown == worst.quantize(Decimal("0.01"))


def test_backtest_is_reproducible() -> None:
    assert _backtest(regime_series()) == _backtest(regime_series())


# --- execution timing ---


def test_entries_fill_at_the_open_of_the_bar_after_the_signal() -> None:
    bars = regime_series()
    by_time = {bar.timestamp: bar for bar in bars}

    for trade in _backtest(bars).trades:
        signal, outcome = trade.signal, trade.outcome
        assert signal.data_as_of is not None and outcome.entry_time is not None
        assert outcome.entry_time == signal.data_as_of  # the bar that opens right after the signal closed
        assert outcome.entry_time > signal.last_bar_open_time  # type: ignore[operator]
        assert outcome.entry_price == entry_fill(signal.direction, by_time[outcome.entry_time].open, TradingCosts(), EURUSD)
        assert outcome.exit_time is not None and outcome.exit_time > outcome.entry_time


def test_one_position_at_a_time() -> None:
    trades = _backtest(regime_series()).trades

    for earlier, later in zip(trades, trades[1:]):
        assert later.outcome.entry_time >= earlier.outcome.exit_time  # type: ignore[operator]


def test_no_look_ahead_truncating_the_future_keeps_past_trades_identical() -> None:
    bars = regime_series()
    full = _backtest(bars)
    cut = 200
    cutoff = closing_time(bars[:cut])
    truncated = _backtest(bars[:cut])

    done = [t for t in full.trades if t.outcome.exit_time is not None and t.outcome.exit_time <= cutoff]
    kept = [t for t in truncated.trades if t.outcome.status is not TradeStatus.END_OF_DATA]
    assert done
    assert [(t.signal, t.units, t.gross_pnl) for t in kept] == [(t.signal, t.units, t.gross_pnl) for t in done]


def test_missing_bar_after_a_signal_means_no_entry() -> None:
    bars = uptrend(60)  # first possible decision is the last bar: a LONG
    later = bars_from_moves(["0.0001"] * 3, start=closing_time(bars) + 4 * HOUR, first_close=str(bars[-1].close))

    result = _backtest(bars + later)

    first = result.rejections[0]
    assert first.reason is RejectionReason.ENTRY_NOT_AVAILABLE
    assert first.decided_at == closing_time(bars)
    assert all(t.outcome.entry_time != closing_time(bars) for t in result.trades)


def test_position_still_open_at_the_end_is_closed_at_the_last_close() -> None:
    bars = uptrend()
    after = bars_from_moves(["0.0001"] * 3, start=closing_time(bars), first_close=str(bars[-1].close))

    result = _backtest(bars + after)

    assert [t.outcome.status for t in result.trades] == [TradeStatus.END_OF_DATA]
    assert result.metrics.closed_at_end == 1
    assert reconciliation_difference(result) == 0


# --- the capital account ---


def test_risk_is_one_percent_of_equity_at_each_decision() -> None:
    trades = _backtest(regime_series()).trades
    assert len({t.equity_at_decision for t in trades}) > 1  # equity really changed between trades

    for trade in trades:
        budget = trade.equity_at_decision * Decimal("0.01")
        assert trade.risk_amount <= budget
        assert trade.risk_amount > budget * Decimal("0.95")


def test_margin_follows_the_leverage() -> None:
    for trade in _backtest(regime_series()).trades:
        assert trade.outcome.entry_price is not None
        expected = (Decimal(trade.units) * trade.outcome.entry_price / Decimal("30")).quantize(Decimal("0.01"))
        assert trade.margin_used == expected
        assert trade.margin_used <= trade.equity_at_decision * Decimal("0.75")  # 25 % margin reserve


def test_tiny_account_rejects_trades_it_cannot_size() -> None:
    result = _backtest(regime_series(), account=AccountAssumptions(capital=Decimal("100")))

    assert result.metrics.trades == 0
    assert result.metrics.rejections[RejectionReason.POSITION_TOO_SMALL] > 0
    assert result.final_balance == Decimal("100")


def test_trade_needing_more_margin_than_allowed_is_rejected_not_downsized() -> None:
    greedy = AccountAssumptions(risk_fraction=Decimal("0.2"))  # ~1.5M units would need ~55k margin

    result = _backtest(regime_series(), account=greedy)

    assert result.metrics.trades == 0
    assert result.metrics.rejections[RejectionReason.INSUFFICIENT_MARGIN] > 0


def test_lower_leverage_rejects_what_30_to_1_accepts() -> None:
    normal = _backtest(regime_series())
    low = _backtest(regime_series(), account=AccountAssumptions(leverage=Decimal("5")))

    assert normal.metrics.trades > 0
    assert low.metrics.trades < normal.metrics.trades
    assert low.metrics.rejections.get(RejectionReason.INSUFFICIENT_MARGIN, 0) > 0


def test_margin_stop_out_liquidates_before_the_stop() -> None:
    # A stop-out level just under the opening margin level: a small adverse move liquidates.
    strict = AccountAssumptions(stop_out_level=Decimal("3.5"), margin_reserve_fraction=Decimal("0"))

    result = _backtest(regime_series(), account=strict)

    liquidated = [t for t in result.trades if t.outcome.status is TradeStatus.LIQUIDATED]
    assert liquidated and result.metrics.liquidations == len(liquidated)
    for trade in liquidated:
        exit_price, stop = trade.outcome.exit_price, trade.outcome.stop_loss
        assert exit_price is not None and stop is not None
        if trade.signal.direction is TradeDirection.LONG:
            assert exit_price > stop - Decimal("0.00002")
        else:
            assert exit_price < stop + Decimal("0.00002")
    assert reconciliation_difference(result) == 0


def test_gap_losses_are_not_capped_and_debt_is_reported() -> None:
    result = _backtest(_crash_after_entry())

    (trade,) = result.trades
    assert trade.outcome.status is TradeStatus.STOP_LOSS
    assert -trade.net_pnl > trade.risk_amount * 10  # the stop did not cap the loss
    assert result.final_balance < 0
    assert result.status is AccountStatus.NEGATIVE_BALANCE
    assert result.debt == -result.final_balance
    assert reconciliation_difference(result) == 0


def test_loss_limit_suspends_new_entries() -> None:
    cautious = AccountAssumptions(max_drawdown_halt=Decimal("0.005"))

    result = _backtest(regime_series(), account=cautious)

    assert result.status is AccountStatus.SUSPENDED_DRAWDOWN
    assert result.suspended_at is not None
    assert all(t.outcome.entry_time < result.suspended_at for t in result.trades)  # type: ignore[operator]
    assert result.metrics.rejections[RejectionReason.TRADING_SUSPENDED] > 0


def test_spread_and_slippage_lower_the_result() -> None:
    cheap = _backtest(regime_series(), costs=TradingCosts(spread_pips=Decimal("0.5"), slippage_pips=Decimal("0")))
    dear = _backtest(regime_series(), costs=TradingCosts(spread_pips=Decimal("1.5"), slippage_pips=Decimal("0.5")))

    assert dear.metrics.total_costs > cheap.metrics.total_costs
    assert dear.final_balance < cheap.final_balance


# --- segmentation ---


def test_segments_are_chronological_contiguous_and_independent() -> None:
    bars = regime_series()
    segmented = run_segmented_backtest(
        bars, instrument=EURUSD, parameters=StrategyParameters(), costs=TradingCosts(),
        account=AccountAssumptions(), generated_at=closing_time(bars),
    )
    names = [segment.label for segment in segmented.segments]

    assert names == ["desarrollo", "validación", "prueba final"]
    assert sum(segment.bars for segment in segmented.segments) == segmented.full.bars == len(bars)
    assert [s.bars for s in segmented.segments] == [156, 52, 52]
    for earlier, later in zip(segmented.segments, segmented.segments[1:]):
        assert later.data_start == earlier.data_end
        assert later.warmup_bars > 0  # earlier bars initialise its indicators
    for segment in segmented.segments:
        assert segment.initial_capital == Decimal("10000")
        assert reconciliation_difference(segment) == 0
        for trade in segment.trades:
            assert segment.data_start <= trade.outcome.entry_time < segment.data_end  # type: ignore[operator]
            assert trade.outcome.exit_time <= segment.data_end  # type: ignore[operator]


def test_too_little_data_is_rejected() -> None:
    with pytest.raises(BacktestDataError):
        _backtest(uptrend(40))


def test_invalid_period_is_rejected() -> None:
    with pytest.raises(BacktestDataError):
        _backtest(regime_series(), start=100, end=100)



def test_account_that_cannot_fund_the_minimum_position_is_marked_exhausted() -> None:
    # 1,000 units of EUR/USD at ~1.10 need ~36.7 USD of margin at 30:1, plus the 25 % reserve.
    result = _backtest(regime_series(), account=AccountAssumptions(capital=Decimal("40")))

    assert result.status is AccountStatus.CAPITAL_EXHAUSTED
    assert result.suspended_at == result.account_curve[0].timestamp
    assert result.metrics.trades == 0
    assert result.final_balance == Decimal("40")
