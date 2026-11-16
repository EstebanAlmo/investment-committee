"""Multi-month evaluation: snapshot reproducibility, capital continuity,
temporal isolation, monthly reconciliation. Synthetic bars, no network."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from investment_committee.application.period import PeriodEvaluationError, PeriodEvaluationService, month_table, summary
from investment_committee.conversation.period_render import render_period
from investment_committee.domain import PriceBar
from investment_committee.market_intelligence import MarketDataError, MarketIntelligenceService
from investment_committee.market_intelligence.snapshots import SnapshotError, bars_from_csv, bars_to_csv, read_snapshot, write_snapshot
from investment_committee.simulation import DEFAULT_UNIVERSE
from investment_committee.simulation.period import data_gaps, month_windows, scale_costs

from tests.application.test_opportunities import FakeMarket
from tests.simulation._support import bars_from_moves

UTC = timezone.utc
START = datetime(2026, 1, 19, 0, tzinfo=UTC)  # Monday; warm-up before February
UP = ["0.0005" if i % 2 == 0 else "-0.0004" for i in range(24 * 72)]
CHOP = ["0.0006", "-0.0004", "0.0005", "-0.0008", "-0.0005", "0.0004"] * (24 * 12)


def synthetic() -> dict[str, list[PriceBar]]:
    eur = bars_from_moves(UP[: 24 * 72], start=START)
    gbp = bars_from_moves([str(-Decimal(m)) for m in CHOP[: 24 * 72]], start=START, first_close="1.30000")
    jpy = bars_from_moves([str(Decimal(m) * 200) for m in CHOP[: 24 * 72]], start=START, first_close="150.000")
    return {"EURUSD=X": eur, "GBPUSD=X": gbp, "USDJPY=X": jpy}




def service(tmp_path: Path, data=None) -> tuple[PeriodEvaluationService, FakeMarket]:  # type: ignore[no-untyped-def]
    market = FakeMarket(data or synthetic())
    return PeriodEvaluationService(MarketIntelligenceService(market), snapshot_root=tmp_path, clock=lambda: datetime(2026, 4, 10, tzinfo=UTC)), market


@pytest.fixture(scope="module")
def evaluated(tmp_path_factory):  # type: ignore[no-untyped-def]
    root = tmp_path_factory.mktemp("snap")
    svc, market = service(root)
    return svc.evaluate(date(2026, 2, 1), date(2026, 4, 1)), root, market


# --- months and data ---


def test_month_windows_are_whole_sessions_with_dst() -> None:
    feb, mar = month_windows(date(2026, 2, 1), date(2026, 3, 31))

    assert (feb.start, feb.end) == (datetime(2026, 1, 31, 22, tzinfo=UTC), datetime(2026, 2, 28, 22, tzinfo=UTC))  # EST
    assert (mar.start, mar.end) == (datetime(2026, 2, 28, 22, tzinfo=UTC), datetime(2026, 3, 31, 21, tzinfo=UTC))  # EDT from 8 March
    assert feb.end == mar.start


def test_gaps_are_reported_not_filled() -> None:
    bars = bars_from_moves(["0.0001"] * 48, start=datetime(2026, 2, 3, tzinfo=UTC))
    holed = [b for b in bars if not (datetime(2026, 2, 3, 10, tzinfo=UTC) <= b.timestamp < datetime(2026, 2, 3, 13, tzinfo=UTC))]

    gaps = data_gaps(holed, datetime(2026, 2, 3, tzinfo=UTC), datetime(2026, 2, 5, tzinfo=UTC))

    assert [(g.start.hour, g.hours) for g in gaps] == [(10, 3)]
    assert data_gaps(bars, datetime(2026, 2, 7, tzinfo=UTC), datetime(2026, 2, 8, tzinfo=UTC)) == []  # Saturday is not a gap


def test_cost_scenarios_scale_spread_and_slippage_only() -> None:
    scaled = scale_costs(DEFAULT_UNIVERSE, Decimal("1.5"))

    for before, after in zip(DEFAULT_UNIVERSE, scaled):
        assert after.costs.spread_pips == before.costs.spread_pips * Decimal("1.5")
        assert after.costs.slippage_pips == before.costs.slippage_pips * Decimal("1.5")
        assert after.spec == before.spec and after.costs.commission_per_100k == before.costs.commission_per_100k


# --- snapshot reproducibility ---


def test_snapshot_round_trip_and_tamper_detection(tmp_path: Path) -> None:
    data = synthetic()
    manifest = write_snapshot(tmp_path, data, source="test", interval="1h", requested_from=START, requested_to=START + timedelta(days=5), downloaded_at=START)

    again, bars = read_snapshot(tmp_path)

    assert bars == {s: sorted(b, key=lambda x: x.timestamp) for s, b in data.items()}
    assert again.combined_sha256 == manifest.combined_sha256
    assert bars_from_csv(bars_to_csv(data["EURUSD=X"])) == data["EURUSD=X"]
    victim = tmp_path / manifest.series[0].file
    victim.write_text(victim.read_text(encoding="utf-8").replace("1.1", "1.2", 1), encoding="utf-8")
    with pytest.raises(SnapshotError, match="hash"):
        read_snapshot(tmp_path)


def test_a_second_run_reuses_the_snapshot_and_reproduces_everything(evaluated, tmp_path_factory) -> None:  # type: ignore[no-untyped-def]
    report, root, market = evaluated
    calls = len(market.requests)
    offline = PeriodEvaluationService(MarketIntelligenceService(FakeMarket({s: MarketDataError("offline") for s in report.symbols})),
                                      snapshot_root=root, clock=lambda: datetime(2026, 4, 10, tzinfo=UTC))

    again = offline.evaluate(date(2026, 2, 1), date(2026, 4, 1))

    assert calls == 3  # one download per instrument
    assert json.dumps(summary(again), sort_keys=True) == json.dumps(summary(report), sort_keys=True)
    assert month_table(again.continuous_rows, again.symbols) == month_table(report.continuous_rows, report.symbols)


def test_insufficient_warmup_is_refused(tmp_path: Path) -> None:
    late = {s: [b for b in bars if b.timestamp >= datetime(2026, 1, 31, 12, tzinfo=UTC)] for s, bars in synthetic().items()}
    svc, _ = service(tmp_path, late)

    with pytest.raises(PeriodEvaluationError, match="velas previas insuficientes"):
        svc.evaluate(date(2026, 2, 1), date(2026, 4, 1))


# --- continuity, isolation, reconciliation ---


def test_capital_carries_from_month_to_month_in_continuous_mode(evaluated) -> None:  # type: ignore[no-untyped-def]
    report, _, _ = evaluated
    for scenario, rep in report.continuous.items():
        for result in rep.results:
            rows = [r for r in report.continuous_rows if r.scenario == scenario and r.policy == result.policy.value]
            assert rows[0].start_balance == result.initial_capital
            for previous, current in zip(rows, rows[1:]):
                assert current.start_balance == previous.end_balance
            assert rows[-1].end_balance == result.final_balance
            assert sum(r.pnl for r in rows) == result.metrics.net_pnl
            assert sum(r.trades for r in rows) == result.metrics.trades


def test_monthly_rows_reconcile_with_their_trades(evaluated) -> None:  # type: ignore[no-untyped-def]
    report, _, _ = evaluated
    for row in [*report.continuous_rows, *report.normalized_rows]:
        assert sum(row.by_instrument.values()) == row.pnl
        assert sum(row.trades_by_instrument.values()) == row.trades
        assert row.end_balance == row.start_balance + row.pnl
        assert row.wins + row.losses <= row.trades
    for month, rep in report.normalized.items():
        for result in rep.results:
            row = next(r for r in report.normalized_rows if r.month == month and r.policy == result.policy.value)
            assert row.start_balance == Decimal("10000") and row.pnl == result.metrics.net_pnl and row.trades == result.metrics.trades


def test_first_month_is_identical_in_both_modes(evaluated) -> None:  # type: ignore[no-untyped-def]
    report, _, _ = evaluated
    first = report.windows[0].label
    for policy in ("A", "B", "C"):
        c = next(r for r in report.continuous_rows if r.scenario == "base" and r.policy == policy and r.month == first)
        n = next(r for r in report.normalized_rows if r.policy == policy and r.month == first)
        assert (c.pnl, c.trades, c.costs) == (n.pnl, n.trades, n.costs)


def test_a_month_is_isolated_from_later_data(tmp_path: Path) -> None:
    data = synthetic()
    cut = datetime(2026, 2, 28, 22, tzinfo=UTC)  # end of the February session month
    altered = {s: [replace(b, open=b.open * 3, high=b.high * 3, low=b.low * 3, close=b.close * 3) if b.timestamp >= cut else b for b in bars]
               for s, bars in data.items()}
    base_svc, _ = service(tmp_path / "a", data)
    alt_svc, _ = service(tmp_path / "b", altered)

    base = base_svc.evaluate(date(2026, 2, 1), date(2026, 4, 1))
    alt = alt_svc.evaluate(date(2026, 2, 1), date(2026, 4, 1))

    def feb(rows):  # type: ignore[no-untyped-def]
        return [(r.mode, r.scenario, r.policy, r.pnl, r.trades) for r in rows if r.month == "2026-02"]

    assert feb(base.continuous_rows) == feb(alt.continuous_rows)
    assert feb(base.normalized_rows) == feb(alt.normalized_rows)


def test_report_keeps_modes_and_scenarios_apart(evaluated) -> None:  # type: ignore[no-untyped-def]
    report, _, _ = evaluated
    text = render_period(report)

    assert "1) MODO CONTINUO" in text and "2) MODO NORMALIZADO" in text and "no se puede sumar ni componer" in text
    assert "costos ×1,0" in text and "costos ×1,5" in text and "costos ×2,0" in text
    assert "Ninguna estrategia se marca VALIDADA" in text
    data = summary(report)
    assert data["kind"] == "BACKTEST_RETROSPECTIVO" and data["validated"] is False
    assert set(data["continuous"]) == {"base", "x1.5", "x2"} and set(data["normalized"]) == {"2026-02", "2026-03"}
