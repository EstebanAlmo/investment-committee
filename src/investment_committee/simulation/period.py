"""Multi-month evaluation on top of the audited replay (simulation/replay.py).

Months are made of whole FX SESSIONS: month M is every session whose session
day (17:00 New York to 17:00 New York, named by its end date) falls in M. So
a month starts at 17:00 New York on the last day of the previous month and
ends at 17:00 New York on its own last day; daylight saving is applied. Every
position is flat by 16:00 New York, so no position crosses a month boundary,
and both evaluation modes use the very same windows:
  - CONTINUOUS: one replay over the whole period; the capital carries from
    month to month; months are slices of that single run;
  - NORMALIZED: one independent replay per month, each from the same fresh
    capital (10 000 USD) - comparable month to month, not compoundable.
The two modes answer different questions and are never mixed in one table.

Nothing here tunes anything: strategy parameters, risk limits and selection
policies are those of the replay. Cost scenarios scale the assumed spread and
slippage only (the strategy's own cost gate then reacts to them, as it
would to real costs).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Mapping, Sequence

from investment_committee.domain import PriceBar

from .models import TradingCosts
from .opportunities import InstrumentProfile, is_aligned
from .replay import PolicyResult, ReplayTrade
from .sessions import is_week_open, new_york_time

CONTINUOUS = "CONTINUO"
NORMALIZED = "NORMALIZADO"
# Rejections by the account's risk limits (not by the strategy, the data, or
# the policy's own selection).
RISK_REJECTIONS = frozenset({
    "riesgo por operación", "riesgo agregado", "exposición compartida al USD", "límite de pérdida diaria", "margen agregado",
    "riesgo agregado al ejecutar", "exposición compartida al USD al ejecutar", "tamaño insuficiente al ejecutar",
})
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class MonthWindow:
    label: str  # "2026-04"
    start: datetime  # 17:00 New York on the previous month's last day, in UTC
    end: datetime  # 17:00 New York on the month's last day, in UTC


def month_windows(first: date, last: date) -> list[MonthWindow]:
    """Session months from `first`'s month up to and including `last`'s."""
    windows = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        first_day = date(year, month, 1)
        next_first = date(year + (month == 12), month % 12 + 1, 1)
        windows.append(MonthWindow(
            f"{year:04d}-{month:02d}",
            new_york_time(first_day - timedelta(days=1), time(17)),
            new_york_time(next_first - timedelta(days=1), time(17)),
        ))
        year, month = next_first.year, next_first.month
    return windows


def scale_costs(universe: Sequence[InstrumentProfile], factor: Decimal) -> tuple[InstrumentProfile, ...]:
    """Spread and slippage multiplied by `factor` (commission unchanged)."""
    return tuple(
        replace(p, costs=TradingCosts(spread_pips=p.costs.spread_pips * factor, slippage_pips=p.costs.slippage_pips * factor,
                                      commission_per_100k=p.costs.commission_per_100k))
        for p in universe
    )


@dataclass(frozen=True)
class DataGap:
    start: datetime  # first missing bar's open time
    end: datetime  # end of the gap (open time of the next bar that exists)
    hours: int


def data_gaps(bars: Sequence[PriceBar], start: datetime, end: datetime, timeframe: timedelta = timedelta(hours=1)) -> list[DataGap]:
    """Hours of the open FX week in [start, end) with no bar, grouped into
    intervals. Nothing is filled in: a gap stays a gap."""
    present = {int(b.timestamp.timestamp()) for b in bars if is_aligned(b.timestamp, timeframe)}
    step = int(timeframe.total_seconds())
    first = int(start.timestamp()) + (-int(start.timestamp())) % step
    gaps: list[DataGap] = []
    run_start: int | None = None
    for s in range(first, int(end.timestamp()), step):
        moment = datetime.fromtimestamp(s, tz=start.tzinfo)
        missing = is_week_open(moment) and is_week_open(moment + timeframe - timedelta(microseconds=1)) and s not in present
        if missing and run_start is None:
            run_start = s
        elif not missing and run_start is not None:
            gaps.append(DataGap(datetime.fromtimestamp(run_start, tz=start.tzinfo), moment, (s - run_start) // step))
            run_start = None
    if run_start is not None:
        gaps.append(DataGap(datetime.fromtimestamp(run_start, tz=start.tzinfo), end, (int(end.timestamp()) - run_start) // step))
    return gaps


def closed_bars_before(bars: Sequence[PriceBar], moment: datetime, timeframe: timedelta = timedelta(hours=1)) -> int:
    return sum(1 for b in bars if is_aligned(b.timestamp, timeframe) and b.timestamp + timeframe <= moment)


@dataclass(frozen=True)
class MonthRow:
    mode: str
    scenario: str
    policy: str
    month: str
    start_balance: Decimal
    end_balance: Decimal
    pnl: Decimal
    net_return: Decimal  # pnl / start_balance
    trades: int
    wins: int
    losses: int
    win_rate: Decimal | None
    expectancy: Decimal | None
    profit_factor: Decimal | None
    max_drawdown: Decimal
    max_drawdown_fraction: Decimal
    costs: Decimal
    by_instrument: Mapping[str, Decimal]
    trades_by_instrument: Mapping[str, int]
    risk_rejections: int
    other_rejections: int


def _row(mode: str, scenario: str, policy: str, label: str, start_balance: Decimal, trades: Sequence[ReplayTrade],
         curve_equities: Sequence[Decimal], rejections: Sequence[str], symbols: Sequence[str]) -> MonthRow:
    pnl = sum((t.net_pnl_usd for t in trades), Decimal("0"))
    wins = [t.net_pnl_usd for t in trades if t.net_pnl_usd > 0]
    losses = [t.net_pnl_usd for t in trades if t.net_pnl_usd < 0]
    gross_profit, gross_loss = sum(wins, Decimal("0")), sum(losses, Decimal("0"))
    peak, worst, worst_fraction = start_balance, Decimal("0"), Decimal("0")
    for value in curve_equities:
        peak = max(peak, value)
        worst = min(worst, value - peak)
        worst_fraction = min(worst_fraction, (value - peak) / peak)
    return MonthRow(
        mode=mode, scenario=scenario, policy=policy, month=label, start_balance=start_balance, end_balance=start_balance + pnl,
        pnl=pnl, net_return=(pnl / start_balance).quantize(Decimal("0.0001")), trades=len(trades), wins=len(wins), losses=len(losses),
        win_rate=(Decimal(len(wins)) / len(trades)).quantize(Decimal("0.0001")) if trades else None,
        expectancy=(pnl / len(trades)).quantize(_CENT) if trades else None,
        profit_factor=(gross_profit / -gross_loss).quantize(_CENT) if gross_loss < 0 else None,
        max_drawdown=worst.quantize(_CENT), max_drawdown_fraction=worst_fraction.quantize(Decimal("0.0001")),
        costs=sum((t.costs_usd for t in trades), Decimal("0")),
        by_instrument={s: sum((t.net_pnl_usd for t in trades if t.symbol == s), Decimal("0")) for s in symbols},
        trades_by_instrument={s: sum(1 for t in trades if t.symbol == s) for s in symbols},
        risk_rejections=sum(1 for r in rejections if r in RISK_REJECTIONS),
        other_rejections=sum(1 for r in rejections if r not in RISK_REJECTIONS),
    )


def continuous_months(result: PolicyResult, windows: Sequence[MonthWindow], symbols: Sequence[str], scenario: str) -> list[MonthRow]:
    """Monthly slices of ONE continuous run: each month starts with the
    previous month's ending balance."""
    rows = []
    balance = result.initial_capital
    for w in windows:
        trades = [t for t in result.trades if w.start <= t.decided_at < w.end]
        curve = [p.equity for p in result.equity_curve if w.start <= p.at < w.end]
        rejections = [r.reason for r in result.rejections if w.start <= r.decided_at < w.end]
        row = _row(CONTINUOUS, scenario, result.policy.value, w.label, balance, trades, curve, rejections, symbols)
        rows.append(row)
        balance = row.end_balance
    return rows


def normalized_month(result: PolicyResult, window: MonthWindow, symbols: Sequence[str], scenario: str) -> MonthRow:
    """A month replayed on its own from fresh capital."""
    return _row(NORMALIZED, scenario, result.policy.value, window.label, result.initial_capital, list(result.trades),
                [p.equity for p in result.equity_curve], [r.reason for r in result.rejections], symbols)


