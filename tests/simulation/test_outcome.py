"""Tests for simulation.outcome: live journal resolution with next-bar-open
entries, stop-loss, take-profit, expiry, gaps, and the conservative same-bar rule."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from investment_committee.domain import PriceBar
from investment_committee.simulation import TradeOutcome, TradeSignal, TradeStatus, resolve_trade

from ._support import HOUR, closing_time, downtrend, sideways, signal_for, uptrend

HALF_SPREAD = Decimal("0.00005")
SLIPPAGE = Decimal("0.00002")
FIVE_PLACES = Decimal("0.00001")


def _bar(start: datetime, offset: int, open_: Decimal, high: Decimal, low: Decimal, close: Decimal) -> PriceBar:
    return PriceBar(timestamp=start + offset * HOUR, open=open_, high=high, low=low, close=close, volume=0)


@pytest.fixture
def long_signal() -> TradeSignal:
    signal = signal_for(uptrend())
    assert signal.plan is not None and signal.data_as_of is not None and signal.reading.atr is not None
    return signal


def _long_levels(signal: TradeSignal, mid_open: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """Independent re-statement of the fill rule: ask fill at the open,
    stop/target at 1.5/3.0 ATR from it."""
    assert signal.reading.atr is not None
    entry = (mid_open + HALF_SPREAD + SLIPPAGE).quantize(FIVE_PLACES)
    stop = (entry - Decimal("1.5") * signal.reading.atr).quantize(FIVE_PLACES)
    target = (entry + Decimal("3.0") * signal.reading.atr).quantize(FIVE_PLACES)
    return entry, stop, target


def _resolve(signal: TradeSignal, bars: list[PriceBar]) -> TradeOutcome:
    return resolve_trade(signal, bars, evaluated_at=bars[-1].timestamp + HOUR)


def _flat(signal: TradeSignal, offset: int, mid: Decimal) -> PriceBar:
    assert signal.data_as_of is not None
    return _bar(signal.data_as_of, offset, mid, mid + Decimal("0.0003"), mid - Decimal("0.0003"), mid)


def test_entry_happens_at_the_next_bar_open_not_at_the_signal_close(long_signal: TradeSignal) -> None:
    assert long_signal.plan is not None
    next_open = long_signal.plan.quote_price + Decimal("0.0004")  # price moved after the signal

    outcome = _resolve(long_signal, [_flat(long_signal, 0, next_open)])

    entry, stop, target = _long_levels(long_signal, next_open)
    assert outcome.status is TradeStatus.OPEN
    assert outcome.entry_time == long_signal.data_as_of
    assert outcome.entry_price == entry != long_signal.plan.entry_price
    assert (outcome.stop_loss, outcome.take_profit) == (stop, target)
    assert outcome.units == long_signal.plan.units


def test_take_profit_once_the_bid_reaches_the_target(long_signal: TradeSignal) -> None:
    assert long_signal.plan is not None and long_signal.data_as_of is not None
    mid = long_signal.plan.quote_price
    entry, _, target = _long_levels(long_signal, mid)
    rally = _bar(long_signal.data_as_of, 1, mid, target + HALF_SPREAD, mid, target + HALF_SPREAD)

    outcome = _resolve(long_signal, [_flat(long_signal, 0, mid), rally])

    assert outcome.status is TradeStatus.TAKE_PROFIT
    assert outcome.exit_price == target
    assert outcome.pnl_amount == (Decimal(long_signal.plan.units) * (target - entry)).quantize(Decimal("0.01"))


def test_mid_touching_the_target_is_not_enough_for_a_long(long_signal: TradeSignal) -> None:
    assert long_signal.plan is not None and long_signal.data_as_of is not None
    mid = long_signal.plan.quote_price
    _, _, target = _long_levels(long_signal, mid)

    outcome = _resolve(long_signal, [_bar(long_signal.data_as_of, 0, mid, target, mid, mid)])

    assert outcome.status is TradeStatus.OPEN


def test_stop_loss_fills_with_adverse_slippage(long_signal: TradeSignal) -> None:
    assert long_signal.plan is not None and long_signal.data_as_of is not None
    mid = long_signal.plan.quote_price
    entry, stop, _ = _long_levels(long_signal, mid)
    drop = stop + HALF_SPREAD - Decimal("0.00001")

    outcome = _resolve(long_signal, [_bar(long_signal.data_as_of, 0, mid, mid, drop, drop)])

    assert outcome.status is TradeStatus.STOP_LOSS
    assert outcome.exit_price == stop - SLIPPAGE
    assert outcome.pnl_amount == (Decimal(long_signal.plan.units) * (stop - SLIPPAGE - entry)).quantize(Decimal("0.01"))


def test_gap_beyond_the_stop_loses_more_than_planned(long_signal: TradeSignal) -> None:
    assert long_signal.plan is not None and long_signal.data_as_of is not None
    mid = long_signal.plan.quote_price
    _, stop, _ = _long_levels(long_signal, mid)
    gap_open = stop - Decimal("0.0020")
    bars = [_flat(long_signal, 0, mid), _bar(long_signal.data_as_of, 1, gap_open, gap_open, gap_open - Decimal("0.0002"), gap_open)]

    outcome = _resolve(long_signal, bars)

    assert outcome.status is TradeStatus.STOP_LOSS
    assert outcome.exit_price == gap_open - HALF_SPREAD - SLIPPAGE
    assert -outcome.pnl_amount > long_signal.plan.risk_amount  # type: ignore[operator]


def test_stop_and_target_in_the_same_bar_counts_as_stop_loss(long_signal: TradeSignal) -> None:
    assert long_signal.plan is not None and long_signal.data_as_of is not None
    mid = long_signal.plan.quote_price
    _, stop, target = _long_levels(long_signal, mid)
    wide = _bar(long_signal.data_as_of, 0, mid, target + Decimal("0.001"), stop - Decimal("0.001"), target)

    outcome = _resolve(long_signal, [wide])

    assert outcome.status is TradeStatus.STOP_LOSS
    assert outcome.same_bar_conflict is True
    assert outcome.pnl_amount is not None and outcome.pnl_amount < 0


def test_expiry_after_24_bars_closes_at_the_last_bid(long_signal: TradeSignal) -> None:
    assert long_signal.plan is not None
    mid = long_signal.plan.quote_price
    bars = [_flat(long_signal, i, mid) for i in range(24)]

    outcome = _resolve(long_signal, bars)

    assert outcome.status is TradeStatus.EXPIRED
    assert outcome.bars_observed == 24
    assert outcome.exit_price == mid - HALF_SPREAD - SLIPPAGE
    assert outcome.exit_time == bars[-1].timestamp + HOUR


def test_missing_bars_at_expiry_exit_at_the_next_available_open(long_signal: TradeSignal) -> None:
    assert long_signal.plan is not None
    mid = long_signal.plan.quote_price
    bars = [_flat(long_signal, i, mid) for i in range(10)] + [_flat(long_signal, 30, mid + Decimal("0.0001"))]

    outcome = _resolve(long_signal, bars)

    assert outcome.status is TradeStatus.EXPIRED
    assert outcome.exit_time == bars[-1].timestamp
    assert outcome.exit_price == bars[-1].open - HALF_SPREAD - SLIPPAGE


def test_signal_without_an_entry_bar_yet_is_pending(long_signal: TradeSignal) -> None:
    outcome = resolve_trade(long_signal, uptrend(), evaluated_at=closing_time(uptrend()) + timedelta(minutes=30))

    assert outcome.status is TradeStatus.OPEN and outcome.entry_price is None


def test_signal_is_not_filled_when_the_next_bar_comes_too_late(long_signal: TradeSignal) -> None:
    late = _flat(long_signal, 5, Decimal("1.1"))  # data missing for 5 hours

    outcome = _resolve(long_signal, [late])

    assert outcome.status is TradeStatus.NOT_FILLED
    assert outcome.pnl_amount is None


def test_bars_before_the_signal_and_still_forming_are_ignored(long_signal: TradeSignal) -> None:
    assert long_signal.data_as_of is not None
    crash = Decimal("1.0")
    before = _bar(long_signal.data_as_of - 2 * HOUR, 0, crash, crash, crash, crash)
    forming = _bar(long_signal.data_as_of, 0, crash, crash, crash, crash)

    outcome = resolve_trade(long_signal, [before, forming], evaluated_at=long_signal.data_as_of + timedelta(minutes=30))

    assert outcome.status is TradeStatus.OPEN and outcome.bars_observed == 0


def test_short_take_profit_uses_the_ask_and_profits() -> None:
    signal = signal_for(downtrend())
    assert signal.plan is not None and signal.data_as_of is not None and signal.reading.atr is not None
    mid = signal.plan.quote_price
    entry = (mid - HALF_SPREAD - SLIPPAGE).quantize(FIVE_PLACES)
    target = (entry - Decimal("3.0") * signal.reading.atr).quantize(FIVE_PLACES)
    low_mid = target - HALF_SPREAD

    outcome = resolve_trade(signal, [_bar(signal.data_as_of, 0, mid, mid, low_mid, low_mid)], evaluated_at=signal.data_as_of + HOUR)

    assert outcome.status is TradeStatus.TAKE_PROFIT
    assert outcome.entry_price == entry
    assert outcome.pnl_amount == (Decimal(signal.plan.units) * (entry - target)).quantize(Decimal("0.01"))


def test_no_trade_signal_cannot_be_resolved() -> None:
    with pytest.raises(ValueError):
        resolve_trade(signal_for(sideways()), [], evaluated_at=closing_time(sideways()))
