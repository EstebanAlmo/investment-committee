"""Walk-forward backtest, engine 2.0.0: a capital account bar by bar.

Order of events for every closed bar (oldest first) inside the period:
  1. A pending signal (decided at an earlier close) enters at this bar's
     OPEN if the bar is eligible (simulation/execution.py); before opening,
     the account checks size and margin against its current equity.
  2. An open position is checked against this bar: gap, stop-loss, margin
     liquidation, take-profit (adverse first when both are reachable), then
     expiry at the bar's close.
  3. The account is marked to market at the bar's close (equity curve).
  4. Limits: balance below zero -> NEGATIVE_BALANCE; drawdown from peak
     equity at or beyond max_drawdown_halt -> SUSPENDED_DRAWDOWN; equity
     unable to fund the minimum position -> CAPITAL_EXHAUSTED. Any of them
     stops new entries for the rest of the period (an open trade still
     runs to its exit).
  5. If flat with nothing pending, the strategy is evaluated on the bars
     closed so far, sizing against the current equity. A LONG/SHORT signal
     becomes pending for the next bar; nothing is ever filled on the bar
     that produced the signal.

Money: commission (round trip) is charged to the balance at entry; the
price P&L - which already contains spread and slippage through the fill
prices - is added at exit. Hence, exactly:
    final balance = initial capital + sum(gross P&L) - sum(commissions)
Spread and slippage costs are reported, never subtracted a second time.
A position still open when the period ends is closed at the last bar's
close (END_OF_DATA), so final equity equals final balance.

A period [start, end) may use earlier bars to initialise indicators, but it
only ever decides and trades on its own bars and starts from a fresh
account - so segments never share trades.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Sequence

from investment_committee.domain import PriceBar

from .execution import (
    ExitEvent,
    Position,
    check_exit,
    close_at_bar_close,
    close_at_bar_open,
    entry_fill,
    exit_side,
    find_entry_bar,
    gross_pnl,
    levels_for,
    liquidation_price,
    slippage,
)
from .models import (
    AccountAssumptions,
    AccountPoint,
    AccountStatus,
    BacktestMetrics,
    BacktestResult,
    BacktestTrade,
    InstrumentSpec,
    Rejection,
    RejectionReason,
    SegmentedBacktest,
    SignalReason,
    StrategyParameters,
    TradeOutcome,
    TradeSignal,
    TradeStatus,
    TradingCosts,
)
from .strategy import evaluate_signal, lookback_bars
from .technical import closed_bars

DEFAULT_SPLITS: tuple[tuple[str, Decimal], ...] = (
    ("desarrollo", Decimal("0.6")),
    ("validación", Decimal("0.2")),
    ("prueba final", Decimal("0.2")),
)
_CENT = Decimal("0.01")
_HUNDRED_THOUSAND = Decimal("100000")
_CAPITAL_REJECTIONS = {
    SignalReason.INSUFFICIENT_MARGIN: RejectionReason.INSUFFICIENT_MARGIN,
    SignalReason.POSITION_TOO_SMALL: RejectionReason.POSITION_TOO_SMALL,
}


class BacktestDataError(ValueError):
    """Raised when the bars cannot support a backtest at all."""


def run_backtest(
    bars: Sequence[PriceBar],
    *,
    instrument: InstrumentSpec,
    parameters: StrategyParameters,
    costs: TradingCosts,
    account: AccountAssumptions,
    generated_at: datetime,
    start: int | None = None,
    end: int | None = None,
    label: str = "completo",
) -> BacktestResult:
    closed = _validated_closed_bars(bars, parameters, generated_at)
    return _run(closed, start or 0, len(closed) if end is None else end, label, instrument, parameters, costs, account, generated_at)


def run_segmented_backtest(
    bars: Sequence[PriceBar],
    *,
    instrument: InstrumentSpec,
    parameters: StrategyParameters,
    costs: TradingCosts,
    account: AccountAssumptions,
    generated_at: datetime,
    splits: Sequence[tuple[str, Decimal]] = DEFAULT_SPLITS,
) -> SegmentedBacktest:
    """The full period plus chronological segments (60/20/20 by default),
    each traded from a fresh account with only earlier bars as warm-up."""
    closed = _validated_closed_bars(bars, parameters, generated_at)
    total = len(closed)
    common = (instrument, parameters, costs, account, generated_at)
    full = _run(closed, 0, total, "completo", *common)
    segments = []
    boundary = 0
    cumulative = Decimal("0")
    for position, (name, fraction) in enumerate(splits):
        cumulative += fraction
        stop = total if position == len(splits) - 1 else int((Decimal(total) * cumulative).to_integral_value(rounding=ROUND_DOWN))
        segments.append(_run(closed, boundary, stop, name, *common))
        boundary = stop
    return SegmentedBacktest(full=full, segments=segments)


def reconciliation_difference(result: BacktestResult) -> Decimal:
    """initial + sum(gross P&L) - sum(commissions) - final balance; exactly 0
    when the books balance."""
    expected = result.initial_capital + sum((t.gross_pnl - t.commission for t in result.trades), Decimal("0"))
    return expected - result.final_balance


def _validated_closed_bars(bars: Sequence[PriceBar], parameters: StrategyParameters, generated_at: datetime) -> list[PriceBar]:
    closed = closed_bars(bars, parameters.timeframe, generated_at)
    if len(closed) <= parameters.min_bars:
        raise BacktestDataError(f"a backtest needs more than {parameters.min_bars} closed bars, got {len(closed)}")
    if any(later.timestamp <= earlier.timestamp for earlier, later in zip(closed, closed[1:])):
        raise BacktestDataError("bars must be in strictly increasing time order")
    return closed


def _run(
    closed: Sequence[PriceBar],
    start: int,
    end: int,
    label: str,
    instrument: InstrumentSpec,
    parameters: StrategyParameters,
    costs: TradingCosts,
    account: AccountAssumptions,
    generated_at: datetime,
) -> BacktestResult:
    if not 0 <= start < end <= len(closed):
        raise BacktestDataError(f"invalid backtest period [{start}, {end}) for {len(closed)} bars")
    timeframe = parameters.timeframe
    lookback = lookback_bars(parameters)
    first_decision = max(start, parameters.min_bars - 1)
    slip = slippage(costs, instrument)
    pip = instrument.pip_size

    balance = account.capital
    peak = account.capital
    lowest = account.capital
    status = AccountStatus.ACTIVE
    suspended_at: datetime | None = None
    position: Position | None = None
    position_signal: TradeSignal | None = None
    position_equity_at_decision = Decimal("0")
    position_risk = Decimal("0")
    position_bars = 0
    pending: TradeSignal | None = None
    pending_equity = Decimal("0")
    trades: list[BacktestTrade] = []
    rejections: list[Rejection] = []
    curve: list[AccountPoint] = []
    signals = 0

    def close_position(event: ExitEvent) -> None:
        nonlocal balance, position, position_signal
        assert position is not None and position_signal is not None
        pnl = gross_pnl(position, event.price)
        balance += pnl
        units = Decimal(position.units)
        exit_slippage = 0 if event.status is TradeStatus.TAKE_PROFIT else 1
        move = (event.price - position.entry_price) * position.sign
        outcome = TradeOutcome(
            signal_id=position.signal_id,
            status=event.status,
            evaluated_at=generated_at,
            bars_observed=position_bars,
            exit_time=event.time,
            exit_price=event.price,
            pnl_pips=(move / pip).quantize(Decimal("0.1")),
            pnl_amount=(pnl - position.commission).quantize(_CENT),
            same_bar_conflict=event.same_bar_conflict,
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit,
            units=position.units,
        )
        trades.append(
            BacktestTrade(
                signal=position_signal,
                outcome=outcome,
                units=position.units,
                margin_used=position.margin,
                equity_at_decision=position_equity_at_decision,
                risk_amount=position_risk,
                gross_pnl=pnl,
                commission=position.commission,
                net_pnl=pnl - position.commission,
                spread_cost=units * costs.spread_pips * pip,
                slippage_cost=units * slip * (1 + exit_slippage),
            )
        )
        position = None
        position_signal = None

    for index in range(start, end):
        bar = closed[index]

        # 1. Entry at this bar's open.
        if pending is not None:
            signal, pending = pending, None
            assert signal.data_as_of is not None and signal.reading.atr is not None
            _, available = find_entry_bar([bar], signal.data_as_of, parameters)
            if not available:
                rejections.append(Rejection(signal.signal_id, signal.data_as_of, RejectionReason.ENTRY_NOT_AVAILABLE, pending_equity))
            else:
                opened = _open_position(signal, bar, balance, instrument, parameters, costs, account)
                if isinstance(opened, Rejection):
                    rejections.append(opened)
                else:
                    position, position_risk = opened
                    position_signal = signal
                    position_equity_at_decision = pending_equity
                    position_bars = 0
                    balance -= position.commission

        # 2. Exits during this bar.
        if position is not None:
            position_bars += 1
            liquidation = liquidation_price(position, balance, account.stop_out_level)
            if bar.timestamp >= position.expires_at:  # bars missing around the expiry
                event: ExitEvent | None = close_at_bar_open(position, bar, costs, instrument, TradeStatus.EXPIRED)
            else:
                event = check_exit(position, bar, timeframe, costs, instrument, liquidation)
            if event is None and bar.timestamp + timeframe >= position.expires_at:
                event = close_at_bar_close(position, bar, timeframe, costs, instrument, TradeStatus.EXPIRED)
            if event is not None:
                close_position(event)

        # 3. Mark to market at the close.
        unrealized = Decimal("0")
        used_margin = Decimal("0")
        if position is not None:
            unrealized = gross_pnl(position, exit_side(position.direction, bar.close, costs, instrument))
            used_margin = position.margin
        equity = balance + unrealized
        curve.append(AccountPoint(bar.timestamp + timeframe, balance, equity, used_margin))
        peak = max(peak, equity)
        lowest = min(lowest, equity)

        # 4. Account limits (sticky).
        if status is AccountStatus.ACTIVE:
            minimum_margin = Decimal(account.min_units) * bar.close / account.leverage
            if balance < 0:
                status = AccountStatus.NEGATIVE_BALANCE
            elif equity <= peak * (1 - account.max_drawdown_halt):
                status = AccountStatus.SUSPENDED_DRAWDOWN
            elif equity * (1 - account.margin_reserve_fraction) < minimum_margin:
                status = AccountStatus.CAPITAL_EXHAUSTED
            if status is not AccountStatus.ACTIVE:
                suspended_at = bar.timestamp + timeframe
        elif balance < 0:
            status = AccountStatus.NEGATIVE_BALANCE

        # 5. Decide at the close, for the next bar.
        if index < first_decision or index >= end - 1 or position is not None or pending is not None:
            continue
        decided_at = bar.timestamp + timeframe
        signal = evaluate_signal(
            closed[max(0, index + 1 - lookback) : index + 1],
            decided_at,
            signal_id=f"{label}-{index}",
            instrument=instrument,
            parameters=parameters,
            costs=costs,
            account=replace(account, capital=equity),
        )
        capital_reason = next((_CAPITAL_REJECTIONS[r] for r in signal.reasons if r in _CAPITAL_REJECTIONS), None)
        if signal.is_trade or capital_reason is not None:
            signals += 1
        if capital_reason is not None:
            rejections.append(Rejection(signal.signal_id, decided_at, capital_reason, equity))
        elif signal.is_trade:
            if status is not AccountStatus.ACTIVE:
                rejections.append(Rejection(signal.signal_id, decided_at, RejectionReason.TRADING_SUSPENDED, equity))
            else:
                pending, pending_equity = signal, equity

    # A position still open at the end of the period is closed at the last close.
    if position is not None:
        last = closed[end - 1]
        close_position(close_at_bar_close(position, last, timeframe, costs, instrument, TradeStatus.END_OF_DATA))
        curve[-1] = AccountPoint(curve[-1].timestamp, balance, balance, Decimal("0"))
        lowest = min(lowest, balance)
        if balance < 0:
            status = AccountStatus.NEGATIVE_BALANCE

    final_equity = curve[-1].equity
    return BacktestResult(
        instrument=instrument,
        timeframe=timeframe,
        label=label,
        data_start=closed[start].timestamp,
        data_end=closed[end - 1].timestamp + timeframe,
        bars=end - start,
        warmup_bars=min(start, lookback),
        initial_capital=account.capital,
        final_balance=balance,
        final_equity=final_equity,
        lowest_equity=lowest,
        status=status,
        suspended_at=suspended_at,
        trades=trades,
        rejections=rejections,
        metrics=_metrics(trades, rejections, curve, account.capital, final_equity, signals),
        costs=costs,
        parameters=parameters,
        account=account,
        generated_at=generated_at,
        account_curve=curve,
    )


def _open_position(
    signal: TradeSignal,
    bar: PriceBar,
    equity: Decimal,
    instrument: InstrumentSpec,
    parameters: StrategyParameters,
    costs: TradingCosts,
    account: AccountAssumptions,
) -> tuple[Position, Decimal] | Rejection:
    """Fill at the bar's open, size on current equity, and check margin.
    Never downsizes to squeeze a trade in: a failed requirement rejects it."""
    assert signal.reading.atr is not None and signal.data_as_of is not None
    entry = entry_fill(signal.direction, bar.open, costs, instrument)
    stop, target = levels_for(signal.direction, entry, signal.reading.atr, parameters, instrument)
    slip = slippage(costs, instrument)
    risk_per_unit = abs(entry - stop) + slip
    step = Decimal(account.lot_step_units)
    budget = equity * account.risk_fraction
    units = int((budget / risk_per_unit / step).to_integral_value(rounding=ROUND_DOWN) * step) if budget > 0 else 0
    if units < max(account.min_units, account.lot_step_units):
        return Rejection(signal.signal_id, signal.data_as_of, RejectionReason.POSITION_TOO_SMALL, equity)
    margin = (Decimal(units) * entry / account.leverage).quantize(_CENT)
    available = ((1 - account.margin_reserve_fraction) * equity).quantize(_CENT)
    if margin > available:
        return Rejection(signal.signal_id, signal.data_as_of, RejectionReason.INSUFFICIENT_MARGIN, equity, margin, available)
    commission = (costs.commission_per_100k * Decimal(units) / _HUNDRED_THOUSAND).quantize(_CENT)
    position = Position(
        signal_id=signal.signal_id,
        direction=signal.direction,
        entry_time=bar.timestamp,
        entry_price=entry,
        stop_loss=stop,
        take_profit=target,
        units=units,
        commission=commission,
        margin=margin,
        expires_at=bar.timestamp + parameters.expiry_bars * parameters.timeframe,
    )
    return position, (Decimal(units) * risk_per_unit + commission).quantize(_CENT)


def _metrics(
    trades: Sequence[BacktestTrade],
    rejections: Sequence[Rejection],
    curve: Sequence[AccountPoint],
    initial: Decimal,
    final_equity: Decimal,
    signals: int,
) -> BacktestMetrics:
    nets = [t.net_pnl for t in trades]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = sum(losses, Decimal("0"))

    peak = initial
    max_drawdown = Decimal("0")
    max_drawdown_fraction = Decimal("0")
    for point in curve:
        peak = max(peak, point.equity)
        max_drawdown = min(max_drawdown, point.equity - peak)
        if peak > 0:
            max_drawdown_fraction = min(max_drawdown_fraction, (point.equity - peak) / peak)

    def count(status: TradeStatus) -> int:
        return sum(1 for t in trades if t.outcome.status is status)

    spread = sum((t.spread_cost for t in trades), Decimal("0"))
    slip = sum((t.slippage_cost for t in trades), Decimal("0"))
    commission = sum((t.commission for t in trades), Decimal("0"))
    reasons: dict[RejectionReason, int] = {}
    for rejection in rejections:
        reasons[rejection.reason] = reasons.get(rejection.reason, 0) + 1
    n = len(trades)
    return BacktestMetrics(
        trades=n,
        wins=len(wins),
        losses=len(losses),
        take_profits=count(TradeStatus.TAKE_PROFIT),
        stop_losses=count(TradeStatus.STOP_LOSS),
        expired=count(TradeStatus.EXPIRED),
        liquidations=count(TradeStatus.LIQUIDATED),
        closed_at_end=count(TradeStatus.END_OF_DATA),
        win_rate=(Decimal(len(wins)) / Decimal(n)).quantize(Decimal("0.0001")) if n else None,
        gross_profit=gross_profit.quantize(_CENT),
        gross_loss=gross_loss.quantize(_CENT),
        profit_factor=(gross_profit / -gross_loss).quantize(_CENT) if gross_loss < 0 else None,
        average_win=(gross_profit / len(wins)).quantize(_CENT) if wins else None,
        average_loss=(gross_loss / len(losses)).quantize(_CENT) if losses else None,
        expectancy=(sum(nets, Decimal("0")) / n).quantize(_CENT) if n else None,
        net_pnl=sum(nets, Decimal("0")).quantize(_CENT),
        net_pnl_pips=sum((t.outcome.pnl_pips or Decimal("0") for t in trades), Decimal("0")),
        net_return=((final_equity - initial) / initial).quantize(Decimal("0.0001")),
        max_drawdown=max_drawdown.quantize(_CENT),
        max_drawdown_fraction=max_drawdown_fraction.quantize(Decimal("0.0001")),
        spread_cost=spread.quantize(_CENT),
        slippage_cost=slip.quantize(_CENT),
        commission_cost=commission.quantize(_CENT),
        total_costs=(spread + slip + commission).quantize(_CENT),
        same_bar_conflicts=sum(1 for t in trades if t.outcome.same_bar_conflict),
        signals=signals,
        rejections=reasons,
    )
