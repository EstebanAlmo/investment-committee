"""Synthetic hourly EUR/USD bars for simulation tests. Not collected by pytest."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Sequence

from investment_committee.domain import PriceBar
from investment_committee.simulation import (
    EURUSD,
    AccountAssumptions,
    StrategyParameters,
    TradeSignal,
    TradingCosts,
    evaluate_signal,
)

HOUR = timedelta(hours=1)
# Monday 2026-10-12 00:00 UTC: 80 hourly bars stay inside the trading week.
WEEK_START = datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc)
WICK = Decimal("0.0002")


def bars_from_moves(moves: Sequence[str], start: datetime = WEEK_START, first_close: str = "1.10000") -> list[PriceBar]:
    """One bar per move: open = previous close, close = open + move, with a
    fixed wick above and below."""
    bars: list[PriceBar] = []
    close = Decimal(first_close)
    for index, move in enumerate(moves):
        open_ = close
        close = open_ + Decimal(move)
        bars.append(
            PriceBar(
                timestamp=start + index * HOUR,
                open=open_,
                high=max(open_, close) + WICK,
                low=min(open_, close) - WICK,
                close=close,
                volume=0,
            )
        )
    return bars


def uptrend(count: int = 70) -> list[PriceBar]:
    return bars_from_moves(["0.0005" if i % 2 == 0 else "-0.0004" for i in range(count)])


def downtrend(count: int = 70) -> list[PriceBar]:
    return bars_from_moves(["-0.0005" if i % 2 == 0 else "0.0004" for i in range(count)])


def sideways(count: int = 70) -> list[PriceBar]:
    # A rise followed by a drop: the fast average is still above the slow
    # one, but price has fallen below it - trend and price disagree.
    half = count - 15
    return bars_from_moves(["0.0004"] * half + ["-0.0012"] * (count - half))


def closing_time(bars: Sequence[PriceBar]) -> datetime:
    return bars[-1].timestamp + HOUR


def signal_for(
    bars: Sequence[PriceBar],
    as_of: datetime | None = None,
    costs: TradingCosts | None = None,
    parameters: StrategyParameters | None = None,
    account: AccountAssumptions | None = None,
) -> TradeSignal:
    return evaluate_signal(
        bars,
        as_of if as_of is not None else closing_time(bars),
        signal_id="sig-1",
        instrument=EURUSD,
        parameters=parameters or StrategyParameters(),
        costs=costs or TradingCosts(),
        account=account or AccountAssumptions(),
    )


def regime_series() -> list[PriceBar]:
    """~260 hourly bars alternating up and down regimes, so the strategy
    opens, wins, and loses trades in a backtest."""
    moves: list[str] = []
    for block in range(4):
        up = block % 2 == 0
        step, back = ("0.0006", "-0.0004") if up else ("-0.0006", "0.0004")
        moves += [step if i % 2 == 0 else back for i in range(50)]  # steady trend
        moves += [("0.0003" if up else "-0.0003")] * 6  # push towards the target
        moves += [("-0.0009" if up else "0.0009")] * 9  # sharp reversal
    return bars_from_moves(moves)
