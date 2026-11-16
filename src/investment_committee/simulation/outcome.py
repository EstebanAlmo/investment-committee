"""Resolve a recorded live signal against the bars that came after it.

Uses the same execution rules as the backtest (simulation/execution.py):
the trade enters at the open of the first eligible bar after the signal,
with the signal's reference size; stop and target are re-derived from that
fill. A live journal entry has no account behind it, so there is no margin
liquidation here - only the stop, the target, and expiry (expiry_bars after
the entry). Only bars closed by `evaluated_at` are used.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Sequence

from investment_committee.domain import PriceBar

from .execution import (
    ExitEvent,
    Position,
    check_exit,
    close_at_bar_close,
    close_at_bar_open,
    entry_fill,
    find_entry_bar,
    gross_pnl,
    levels_for,
)
from .models import TradeOutcome, TradeSignal, TradeStatus
from .technical import closed_bars


def resolve_trade(signal: TradeSignal, bars: Sequence[PriceBar], evaluated_at: datetime) -> TradeOutcome:
    plan = signal.plan
    if plan is None or signal.data_as_of is None or signal.reading.atr is None:
        raise ValueError("only a LONG/SHORT signal with a trade plan can be resolved")

    later = [bar for bar in closed_bars(bars, signal.timeframe, evaluated_at) if bar.timestamp >= signal.data_as_of]
    entry_index, available = find_entry_bar(later, signal.data_as_of, signal.parameters)
    if entry_index is None:
        return TradeOutcome(signal_id=signal.signal_id, status=TradeStatus.OPEN, evaluated_at=evaluated_at, bars_observed=0)
    if not available:
        return TradeOutcome(
            signal_id=signal.signal_id, status=TradeStatus.NOT_FILLED, evaluated_at=evaluated_at, bars_observed=0
        )

    entry_bar = later[entry_index]
    entry = entry_fill(signal.direction, entry_bar.open, signal.costs, signal.instrument)
    stop, target = levels_for(signal.direction, entry, signal.reading.atr, signal.parameters, signal.instrument)
    position = Position(
        signal_id=signal.signal_id,
        direction=signal.direction,
        entry_time=entry_bar.timestamp,
        entry_price=entry,
        stop_loss=stop,
        take_profit=target,
        units=plan.units,
        commission=plan.commission,
        margin=Decimal("0"),
        expires_at=entry_bar.timestamp + signal.parameters.expiry_bars * signal.timeframe,
    )

    def finish(event: ExitEvent, observed: int) -> TradeOutcome:
        move = (event.price - position.entry_price) * position.sign
        return TradeOutcome(
            signal_id=signal.signal_id,
            status=event.status,
            evaluated_at=evaluated_at,
            bars_observed=observed,
            exit_time=event.time,
            exit_price=event.price,
            pnl_pips=(move / signal.instrument.pip_size).quantize(Decimal("0.1")),
            pnl_amount=(gross_pnl(position, event.price) - position.commission).quantize(Decimal("0.01")),
            same_bar_conflict=event.same_bar_conflict,
            **_entry_fields(position),
        )

    observed = 0
    for bar in later[entry_index:]:
        if bar.timestamp >= position.expires_at:
            # Bars missing around the expiry: exit at the first price after it.
            expiry = close_at_bar_open(position, bar, signal.costs, signal.instrument, TradeStatus.EXPIRED)
            return finish(expiry, observed)
        observed += 1
        event = check_exit(position, bar, signal.timeframe, signal.costs, signal.instrument)
        if event is not None:
            return finish(event, observed)
        if bar.timestamp + signal.timeframe >= position.expires_at:
            return finish(close_at_bar_close(position, bar, signal.timeframe, signal.costs, signal.instrument, TradeStatus.EXPIRED), observed)

    return TradeOutcome(
        signal_id=signal.signal_id,
        status=TradeStatus.OPEN,
        evaluated_at=evaluated_at,
        bars_observed=observed,
        **_entry_fields(position),
    )


def _entry_fields(position: Position) -> dict[str, object]:
    return {
        "entry_time": position.entry_time,
        "entry_price": position.entry_price,
        "stop_loss": position.stop_loss,
        "take_profit": position.take_profit,
        "units": position.units,
    }
