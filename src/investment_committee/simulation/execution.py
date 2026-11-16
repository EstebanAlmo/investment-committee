"""Simulated execution: entry fills, exit checks, liquidation - shared by the
live trade journal (outcome.py) and the backtest (backtest.py).

Rules (engine 2.0.0):
- ENTRY: a signal is decided after a bar closes. It fills at the OPEN of the
  first later bar, and only if that bar opens within
  StrategyParameters.max_entry_delay of the decision while the FX market is
  open - otherwise it is not filled (weekend, gap, missing data). The fill
  is the open plus half the spread plus slippage for a LONG (an ask fill),
  minus both for a SHORT (a bid fill). Stop and target are re-derived from
  the actual fill with the same ATR multiples as the signal.
- EXIT PRICES are on the exit side: a LONG sells at the bid (mid - spread/2),
  a SHORT buys at the ask (mid + spread/2).
- ADVERSE EXITS (stop-loss, margin liquidation) fill at their trigger with
  adverse slippage - or at the bar's open plus slippage if the market
  opened beyond the trigger (a gap). A stop therefore does NOT cap the loss.
- TARGETS fill exactly at the target (no favourable gap fills).
- SAME BAR: if one bar reaches both an adverse trigger and the target, the
  adverse exit is assumed (conservative).
- LIQUIDATION: when the account's equity, with the position marked at the
  exit side, would fall to stop_out_level x used margin, the position is
  closed at that price. If both the stop and the liquidation price are
  reached, whichever is crossed first as price moves against the position
  decides the status.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Sequence

from investment_committee.domain import PriceBar

from .models import InstrumentSpec, StrategyParameters, TradeDirection, TradingCosts, TradeStatus
from .technical import is_forex_market_open


@dataclass(frozen=True)
class Position:
    signal_id: str
    direction: TradeDirection
    entry_time: datetime
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    units: int
    commission: Decimal
    margin: Decimal
    expires_at: datetime

    @property
    def sign(self) -> Decimal:
        return Decimal("1") if self.direction is TradeDirection.LONG else Decimal("-1")


@dataclass(frozen=True)
class ExitEvent:
    status: TradeStatus
    price: Decimal
    time: datetime
    same_bar_conflict: bool = False


def _sign(direction: TradeDirection) -> Decimal:
    if direction is TradeDirection.NO_TRADE:
        raise ValueError("NO_TRADE has no execution side")
    return Decimal("1") if direction is TradeDirection.LONG else Decimal("-1")


def half_spread(costs: TradingCosts, instrument: InstrumentSpec) -> Decimal:
    return costs.spread_pips * instrument.pip_size / 2


def slippage(costs: TradingCosts, instrument: InstrumentSpec) -> Decimal:
    return costs.slippage_pips * instrument.pip_size


def quantize_price(value: Decimal, instrument: InstrumentSpec) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-instrument.price_places), rounding=ROUND_HALF_EVEN)


def entry_fill(direction: TradeDirection, mid_open: Decimal, costs: TradingCosts, instrument: InstrumentSpec) -> Decimal:
    adjustment = half_spread(costs, instrument) + slippage(costs, instrument)
    return quantize_price(mid_open + _sign(direction) * adjustment, instrument)


def levels_for(
    direction: TradeDirection, entry: Decimal, atr: Decimal, parameters: StrategyParameters, instrument: InstrumentSpec
) -> tuple[Decimal, Decimal]:
    """(stop_loss, take_profit) at the strategy's ATR multiples from `entry`."""
    sign = _sign(direction)
    stop = quantize_price(entry - sign * parameters.stop_atr_multiple * atr, instrument)
    target = quantize_price(entry + sign * parameters.target_atr_multiple * atr, instrument)
    return stop, target


def exit_side(direction: TradeDirection, mid: Decimal, costs: TradingCosts, instrument: InstrumentSpec) -> Decimal:
    return mid - _sign(direction) * half_spread(costs, instrument)


def find_entry_bar(
    bars: Sequence[PriceBar], decided_at: datetime, parameters: StrategyParameters
) -> tuple[int | None, bool]:
    """(index of the entry bar, available). (None, True): no bar after the
    decision yet - still pending. (i, False): the next bar exists but is not
    eligible (too late after the decision, or market closed)."""
    for index, bar in enumerate(bars):
        if bar.timestamp >= decided_at:
            eligible = bar.timestamp - decided_at <= parameters.max_entry_delay and is_forex_market_open(bar.timestamp)
            return index, eligible
    return None, True


def liquidation_price(position: Position, balance: Decimal, stop_out_level: Decimal) -> Decimal | None:
    """Exit-side price at which equity = stop_out_level x used margin, given
    the account balance (commission already charged). None without margin."""
    if position.margin <= 0 or position.units <= 0:
        return None
    cushion = balance - stop_out_level * position.margin
    return position.entry_price - position.sign * cushion / Decimal(position.units)


def check_exit(
    position: Position,
    bar: PriceBar,
    timeframe: timedelta,
    costs: TradingCosts,
    instrument: InstrumentSpec,
    liquidation: Decimal | None = None,
) -> ExitEvent | None:
    """The exit this bar causes, if any (expiry is the caller's check)."""
    sign = position.sign
    slip = slippage(costs, instrument)
    bar_close_time = bar.timestamp + timeframe
    open_ = exit_side(position.direction, bar.open, costs, instrument)
    low = exit_side(position.direction, bar.low, costs, instrument)
    high = exit_side(position.direction, bar.high, costs, instrument)
    worst, best = (low, high) if sign > 0 else (high, low)

    # The adverse trigger crossed first as price moves against the position.
    adverse_status, adverse_level = TradeStatus.STOP_LOSS, position.stop_loss
    if liquidation is not None and (liquidation - position.stop_loss) * sign > 0:
        adverse_status, adverse_level = TradeStatus.LIQUIDATED, liquidation

    if (open_ - adverse_level) * sign <= 0:  # gapped through at the open
        return ExitEvent(adverse_status, open_ - sign * slip, bar.timestamp)
    adverse_hit = (worst - adverse_level) * sign <= 0
    target_hit = (best - position.take_profit) * sign >= 0
    if adverse_hit:
        return ExitEvent(adverse_status, adverse_level - sign * slip, bar_close_time, same_bar_conflict=target_hit)
    if target_hit:
        return ExitEvent(TradeStatus.TAKE_PROFIT, position.take_profit, bar_close_time)
    return None


def close_at_bar_close(
    position: Position, bar: PriceBar, timeframe: timedelta, costs: TradingCosts, instrument: InstrumentSpec, status: TradeStatus
) -> ExitEvent:
    """A market exit at the bar's close (expiry, end of data), with slippage."""
    price = exit_side(position.direction, bar.close, costs, instrument) - position.sign * slippage(costs, instrument)
    return ExitEvent(status, price, bar.timestamp + timeframe)


def close_at_bar_open(
    position: Position, bar: PriceBar, costs: TradingCosts, instrument: InstrumentSpec, status: TradeStatus
) -> ExitEvent:
    """A market exit at the bar's open - used when data is missing at the
    expiry time and the next available price is this bar's open."""
    price = exit_side(position.direction, bar.open, costs, instrument) - position.sign * slippage(costs, instrument)
    return ExitEvent(status, price, bar.timestamp)


def gross_pnl(position: Position, exit_price: Decimal) -> Decimal:
    """Price P&L in the quote currency; spread and slippage are inside the
    fill prices, so they must not be subtracted again."""
    return Decimal(position.units) * (exit_price - position.entry_price) * position.sign
