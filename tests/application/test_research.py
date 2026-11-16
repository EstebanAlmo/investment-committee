"""Strategy research: every strategy on the same data, v1 unchanged,
retrospective recording apart and idempotent. Synthetic bars, no network."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from investment_committee.application.period import PeriodEvaluationService
from investment_committee.application.research import monthly_table, record_retrospective, run_research, totals_table
from investment_committee.conversation.research_render import render_research
from investment_committee.market_intelligence import MarketIntelligenceService
from investment_committee.memory.database import connect
from investment_committee.simulation import TrendMomentumVolatilityStrategy
from investment_committee.simulation.strategies import BreakoutStrategy

from tests.application.test_opportunities import FakeMarket
from tests.application.test_period import synthetic

UTC = timezone.utc


@pytest.fixture(scope="module")
def research(tmp_path_factory):  # type: ignore[no-untyped-def]
    root = tmp_path_factory.mktemp("research")
    service = PeriodEvaluationService(MarketIntelligenceService(FakeMarket(synthetic())), snapshot_root=root, clock=lambda: datetime(2026, 4, 10, tzinfo=UTC))
    return run_research(service, date(2026, 3, 1), date(2026, 4, 1), [TrendMomentumVolatilityStrategy(), BreakoutStrategy()]), root


def test_all_strategies_run_on_the_same_data_and_v1_matches_its_own_evaluation(research) -> None:  # type: ignore[no-untyped-def]
    report, root = research
    assert set(report.reports) == {"tendencia-momentum-volatilidad@1.0.0", "ruptura-rango-londres@0.1.0"}
    assert len(report.data_sha256) == 64
    service = PeriodEvaluationService(MarketIntelligenceService(FakeMarket({})), snapshot_root=root, clock=lambda: datetime(2026, 4, 10, tzinfo=UTC))
    alone = service.evaluate(date(2026, 3, 1), date(2026, 4, 1), normalized=False)  # v1 by default
    v1 = report.reports["tendencia-momentum-volatilidad@1.0.0"]
    assert [r.final_balance for r in alone.continuous["base"].results] == [r.final_balance for r in v1.continuous["base"].results]


def test_results_are_split_by_strategy_policy_cost_month_and_instrument(research) -> None:  # type: ignore[no-untyped-def]
    report, _ = research
    totals = totals_table(report)
    assert {(r["strategy"], r["policy"], r["scenario"]) for r in totals} == {
        (s, p, c) for s in report.reports for p in ("A", "B", "C") for c in ("base", "x1.5", "x2")
    }
    assert all({"pnl_EURUSD=X", "pnl_GBPUSD=X", "pnl_USDJPY=X", "vs_no_trade", "negative_months"} <= set(r) for r in totals)
    assert {r["month"] for r in monthly_table(report)} == {"2026-03"}
    text = render_research(report)
    assert "Ninguna estrategia está VALIDADA" in text and "no operar" in text


def test_retrospective_summaries_are_recorded_once_and_apart(research, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    report, _ = research
    db = tmp_path / "ic.db"

    first = record_retrospective(report, db)
    record_retrospective(report, db)  # same research, same data: nothing new

    with connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM backtest_runs").fetchone()[0] == first
        assert {r[0] for r in conn.execute("SELECT DISTINCT record_kind FROM backtest_runs")} == {"RETROSPECTIVO"}
        assert conn.execute("SELECT count(*) FROM scans").fetchone()[0] == 0  # nothing prospective
        statuses = dict(conn.execute("SELECT strategy_id, status FROM strategies").fetchall())
    assert statuses == {"tendencia-momentum-volatilidad@1.0.0": "BASE_EXPERIMENTAL", "ruptura-rango-londres@0.1.0": "EXPERIMENTAL"}
