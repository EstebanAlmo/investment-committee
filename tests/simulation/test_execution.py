"""Hand-checked numeric examples for simulation.execution (fills, P&L,
pips to USD, liquidation, entry eligibility)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from investment_committee.domain import PriceBar
from investment_committee.simulation import (
    EURUSD,
    Position,
    StrategyParameters,
    TradeDirection,
    TradeStatus,
    TradingCosts,
    check_exit,
    entry_fill,
    find_entry_bar,
    levels_for,
    liquidation_price,
)
from investment_committee.simulation.execution import gross_pnl

HOUR = timedelta(hours=1)
TUESDAY = datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc)
COSTS = TradingCosts(spread_pips=Decimal("1.0"), slippage_pips=Decimal("0.2"))


def _position(direction: TradeDirection, entry: str, stop: str, target: str, units: int = 100_000, margin: str = "3666.67") -> Position:
    return Position(
        signal_id="p", direction=direction, entry_time=TUESDAY, entry_price=Decimal(entry), stop_loss=Decimal(stop),
        take_profit=Decimal(target), units=units, commission=Decimal("0"), margin=Decimal(margin), expires_at=TUESDAY + 24 * HOUR,
    )


def _bar(open_: str, high: str, low: str, close: str, at: datetime = TUESDAY) -> PriceBar:
    return PriceBar(timestamp=at, open=Decimal(open_), high=Decimal(high), low=Decimal(low), close=Decimal(close), volume=0)


def test_fills_include_half_spread_and_slippage_on_the_right_side() -> None:
    # 1.10000 mid: ask fill = +0.5 pip spread +0.2 pip slippage; bid fill = -0.7 pip
    assert entry_fill(TradeDirection.LONG, Decimal("1.10000"), COSTS, EURUSD) == Decimal("1.10007")
    assert entry_fill(TradeDirection.SHORT, Decimal("1.10000"), COSTS, EURUSD) == Decimal("1.09993")


def test_levels_from_the_fill() -> None:
    stop, target = levels_for(TradeDirection.LONG, Decimal("1.10007"), Decimal("0.00100"), StrategyParameters(), EURUSD)

    assert (stop, target) == (Decimal("1.09857"), Decimal("1.10307"))  # 15 and 30 pips away


def test_one_pip_on_100k_eur_usd_is_ten_dollars_long_and_short() -> None:
    long = _position(TradeDirection.LONG, "1.10000", "1.09000", "1.12000")
    short = _position(TradeDirection.SHORT, "1.10000", "1.11000", "1.08000")

    assert gross_pnl(long, Decimal("1.10010")) == Decimal("10.00000")
    assert gross_pnl(long, Decimal("1.09990")) == Decimal("-10.00000")
    assert gross_pnl(short, Decimal("1.09990")) == Decimal("10.00000")
    assert gross_pnl(short, Decimal("1.10010")) == Decimal("-10.00000")


def test_long_stop_fill_and_loss() -> None:
    position = _position(TradeDirection.LONG, "1.10007", "1.09857", "1.10307")
    # mid low 1.09860 -> bid low 1.09855 <= stop 1.09857: stop fills at 1.09855 (stop - 0.2 pip)
    event = check_exit(position, _bar("1.10000", "1.10010", "1.09860", "1.09900"), HOUR, COSTS, EURUSD)

    assert event is not None and event.status is TradeStatus.STOP_LOSS
    assert event.price == Decimal("1.09855")
    assert gross_pnl(position, event.price) == Decimal("-152.00000")  # 15.2 pips x 10 USD


def test_short_target_needs_the_ask() -> None:
    position = _position(TradeDirection.SHORT, "1.09993", "1.10143", "1.09693")
    # mid low 1.09690 -> ask low 1.09695: target 1.09693 not reached yet
    assert check_exit(position, _bar("1.09900", "1.09950", "1.09690", "1.09800"), HOUR, COSTS, EURUSD) is None
    event = check_exit(position, _bar("1.09900", "1.09950", "1.09688", "1.09800"), HOUR, COSTS, EURUSD)
    assert event is not None and event.status is TradeStatus.TAKE_PROFIT
    assert gross_pnl(position, event.price) == Decimal("300.00000")


def test_gap_through_the_stop_fills_at_the_open() -> None:
    position = _position(TradeDirection.LONG, "1.10007", "1.09857", "1.10307")
    event = check_exit(position, _bar("1.09500", "1.09600", "1.09400", "1.09550"), HOUR, COSTS, EURUSD)

    assert event is not None and event.status is TradeStatus.STOP_LOSS
    assert event.price == Decimal("1.09493")  # open 1.09500 - 0.5 pip spread - 0.2 pip slippage
    assert event.time == TUESDAY


def test_same_bar_stop_and_target_is_a_stop() -> None:
    position = _position(TradeDirection.LONG, "1.10007", "1.09857", "1.10307")
    event = check_exit(position, _bar("1.10000", "1.10500", "1.09500", "1.10400"), HOUR, COSTS, EURUSD)

    assert event is not None and event.status is TradeStatus.STOP_LOSS and event.same_bar_conflict


def test_liquidation_price_and_priority_over_a_farther_stop() -> None:
    # balance 2,000, margin 3,666.67, stop-out 50 %: liquidation when equity = 1,833.33,
    # i.e. after losing 166.67 USD = 16.67 pips on 100k units.
    position = _position(TradeDirection.LONG, "1.10000", "1.09700", "1.11000")
    liquidation = liquidation_price(position, Decimal("2000"), Decimal("0.5"))

    assert liquidation is not None
    assert liquidation.quantize(Decimal("0.000001")) == Decimal("1.098333")
    event = check_exit(position, _bar("1.09950", "1.09990", "1.09800", "1.09850"), HOUR, COSTS, EURUSD, liquidation)
    assert event is not None and event.status is TradeStatus.LIQUIDATED
    assert event.price == liquidation - Decimal("0.00002")


def test_entry_bar_must_open_soon_after_the_signal_and_while_the_market_is_open() -> None:
    parameters = StrategyParameters()
    friday_close = datetime(2026, 10, 16, 21, 0, tzinfo=timezone.utc)
    sunday_open = datetime(2026, 10, 18, 22, 0, tzinfo=timezone.utc)
    next_bar = _bar("1.1", "1.1", "1.1", "1.1", at=TUESDAY)
    weekend_bar = _bar("1.1", "1.1", "1.1", "1.1", at=sunday_open)
    late_bar = _bar("1.1", "1.1", "1.1", "1.1", at=TUESDAY + 3 * HOUR)
    saturday_bar = _bar("1.1", "1.1", "1.1", "1.1", at=datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc))

    assert find_entry_bar([next_bar], TUESDAY, parameters) == (0, True)
    assert find_entry_bar([weekend_bar], friday_close, parameters) == (0, False)  # 49 hours later
    assert find_entry_bar([late_bar], TUESDAY, parameters) == (0, False)
    assert find_entry_bar([saturday_bar], saturday_bar.timestamp, parameters) == (0, False)  # market closed
    assert find_entry_bar([], TUESDAY, parameters) == (None, True)
