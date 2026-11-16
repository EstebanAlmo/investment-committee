"""Strategy research on already-inspected data (diagnosis only).

Runs every registered strategy (v1.0.0 baseline and the experimental V2
ones) through the same multi-period evaluation, on the same data snapshot,
with the same risk limits, policies and cost scenarios. Nothing is tuned:
each strategy runs with its frozen parameters. The results are
RETROSPECTIVE - April-September 2026 had been inspected before the V2
strategies existed - so they can rule a strategy out, never validate it.
Summaries can be recorded in SQLite's backtest_runs (record_kind
RETROSPECTIVO), apart from the prospective registry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

from investment_committee.memory.database import DEFAULT_DB_PATH, audit, connect, record_backtest_run, utc
from investment_committee.simulation import ScanStrategy
from investment_committee.simulation.sqlite_store import ensure_strategy
from investment_committee.simulation.strategies import all_strategies

from .period import PeriodEvaluationService, PeriodReport


@dataclass(frozen=True)
class ResearchReport:
    first_day: date
    end_day: date
    reports: dict[str, PeriodReport]  # strategy id -> evaluation

    @property
    def data_sha256(self) -> str:
        hashes = {r.manifest.combined_sha256 for r in self.reports.values()}
        assert len(hashes) == 1, "every strategy must run on the same data"
        return hashes.pop()


def run_research(service: PeriodEvaluationService, first_day: date, end_day: date, strategies: Sequence[ScanStrategy] | None = None) -> ResearchReport:
    reports = {}
    for strategy in strategies or all_strategies():
        reports[f"{strategy.name}@{strategy.version}"] = service.evaluate(first_day, end_day, strategy=strategy, normalized=False)
    return ResearchReport(first_day, end_day, reports)


def totals_table(research: ResearchReport) -> list[dict[str, Any]]:
    rows = []
    for strategy_id, report in research.reports.items():
        for scenario, rep in report.continuous.items():
            for result in rep.results:
                months = [r for r in report.continuous_rows if r.scenario == scenario and r.policy == result.policy.value]
                m = result.metrics
                rows.append({
                    "strategy": strategy_id, "policy": result.policy.value, "scenario": scenario, "trades": m.trades, "wins": m.wins,
                    "losses": m.losses, "net_pnl": str(m.net_pnl), "net_return": str(result.net_return),
                    "profit_factor": None if m.profit_factor is None else str(m.profit_factor),
                    "expectancy": None if m.expectancy is None else str(m.expectancy), "max_drawdown_fraction": str(result.max_drawdown_fraction),
                    "costs": str(m.total_costs), "negative_months": sum(1 for r in months if r.pnl < 0), "months": len(months),
                    "vs_no_trade": str(result.final_balance - result.initial_capital),
                    **{f"pnl_{s}": str(x.net_pnl) for s, x in result.by_instrument.items()},
                })
    return rows


def monthly_table(research: ResearchReport) -> list[dict[str, Any]]:
    rows = []
    for strategy_id, report in research.reports.items():
        for r in report.continuous_rows:
            rows.append({"strategy": strategy_id, "scenario": r.scenario, "policy": r.policy, "month": r.month, "start_balance": str(r.start_balance),
                         "pnl": str(r.pnl), "net_return": str(r.net_return), "trades": r.trades, "wins": r.wins, "losses": r.losses,
                         **{f"pnl_{s}": str(v) for s, v in r.by_instrument.items()}})
    return rows


def record_retrospective(research: ResearchReport, db_path: Path | str = DEFAULT_DB_PATH, at: datetime | None = None) -> int:
    """Store every continuous total and month as RETROSPECTIVO rows; re-running
    the same research on the same data adds nothing (stable run ids)."""
    sha = research.data_sha256
    written = 0
    with connect(db_path) as conn:
        for strategy_id, report in research.reports.items():
            name, version = strategy_id.split("@")
            when = at or report.generated_at
            ensure_strategy(conn, name, version, when)
            start, end = utc(report.windows[0].start), utc(report.windows[-1].end)
            for scenario, rep in report.continuous.items():
                for result in rep.results:
                    base = {"strategy_id": strategy_id, "period_start": start, "period_end": end, "mode": "CONTINUO", "policy": result.policy.value,
                            "cost_scenario": scenario, "data_sha256": sha}
                    record_backtest_run(conn, {**base, "run_id": f"{strategy_id}|{scenario}|{result.policy.value}|{start}|{end}|{sha[:16]}|total",
                                               "month": None, "net_pnl": str(result.metrics.net_pnl), "net_return": str(result.net_return),
                                               "trades": result.metrics.trades, "summary_json": json.dumps({"profit_factor": str(result.metrics.profit_factor)})}, when)
                    written += 1
                    for r in (x for x in report.continuous_rows if x.scenario == scenario and x.policy == result.policy.value):
                        record_backtest_run(conn, {**base, "run_id": f"{strategy_id}|{scenario}|{r.policy}|{start}|{end}|{sha[:16]}|{r.month}",
                                                   "month": r.month, "net_pnl": str(r.pnl), "net_return": str(r.net_return), "trades": r.trades,
                                                   "summary_json": json.dumps({"start_balance": str(r.start_balance)})}, when)
                        written += 1
            audit(conn, when, "BACKTEST_RETROSPECTIVO", "strategy", strategy_id, {"data_sha256": sha, "period": [start, end]})
    return written


