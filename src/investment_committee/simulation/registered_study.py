"""One strategy, one period, the fixed scenarios: the shape of a registered
retrospective test (docs/forex-research-protocol.md), run with the existing
engine and written with the existing study writer.

run_from(bars, strategy, start=...) hands the engine every bar it is given
- the ones before `start` only warm the indicators up - and evaluates,
opens and settles nothing dated before `start`. Nothing here changes a
rule, a cost or a limit: evaluate_signals and run_account do the work.
"""

from __future__ import annotations

from datetime import datetime
from typing import Mapping, Sequence

from investment_committee.domain import PriceBar
from investment_committee.market_intelligence.quality import Calendar
from investment_committee.market_intelligence.quotes import QuoteKind, to_utc
from .forex_backtest import BacktestRun, Scenario, Strategy, evaluate_signals, run_account
from .forex_backtest_metrics import first_evaluable_at
from .forex_backtest_study import FULL, HOUR, SCENARIOS, Study


def run_from(
    bars: Mapping[str, Sequence[PriceBar]], strategy: Strategy, *, start: datetime, data_kind: QuoteKind, data_source: str, calendar: Calendar,
    scenarios: Sequence[Scenario] = SCENARIOS,
) -> Study:
    """The study of `strategy` from `start` (the first candle close that is
    evaluated) to the close of the last bar, one account per scenario."""
    labels = [to_utc(b.timestamp) for series in bars.values() for b in series]
    if not labels:
        raise ValueError("no bars")
    end = max(labels) + HOUR
    if not min(labels) < start <= end:
        raise ValueError("the start must fall inside the bars")
    signals = evaluate_signals(strategy, bars, start=start, end=end, data_kind=data_kind, data_source=data_source, calendar=calendar)
    first = first_evaluable_at(signals)
    if first is None:
        raise ValueError("the strategy can never evaluate these bars from that start")
    runs: dict[tuple[str, str, str], BacktestRun] = {
        (strategy.strategy_id, FULL, scenario.name): run_account(
            strategy.strategy_id, strategy.parameters_sha256, signals, bars, start=first, end=end, scenario=scenario, calendar=calendar)
        for scenario in scenarios
    }
    return Study({strategy.strategy_id: signals}, {strategy.strategy_id: strategy}, {strategy.strategy_id: first}, first, end, data_kind, data_source, runs)
