"""application.HistoricalReplayService - fake market data, no network."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from investment_committee.application import HistoricalReplayService, ReplayUnavailableError, previous_month
from investment_committee.application.replay import objections_from_ai_ledger
from investment_committee.market_intelligence import MarketDataError, MarketIntelligenceService

from tests.application.test_opportunities import FakeMarket
from tests.simulation.test_replay import MONDAY, market


def test_previous_month() -> None:
    assert previous_month(date(2026, 10, 13)) == (datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert previous_month(date(2026, 1, 5)) == (datetime(2025, 12, 1, tzinfo=timezone.utc), datetime(2026, 1, 1, tzinfo=timezone.utc))


def _service(data, now: datetime, **kwargs) -> tuple[HistoricalReplayService, FakeMarket]:  # type: ignore[no-untyped-def]
    fake = FakeMarket(data)
    return HistoricalReplayService(MarketIntelligenceService(fake), clock=lambda: now, **kwargs), fake


def test_replays_a_closed_period_and_never_passes_bars_after_its_end() -> None:
    data = market()
    end = MONDAY + timedelta(days=7)
    service, fake = _service(data, now=MONDAY + timedelta(days=11))

    report = service.replay(MONDAY + timedelta(days=3), end)

    assert {r[1] for r in fake.requests} == {"1h"}
    assert report.kind == "BACKTEST_RETROSPECTIVO"
    for result in report.results:
        assert all(t.exit_time <= end for t in result.trades)
    assert any(r.metrics.trades for r in report.results)


def test_periods_in_the_future_or_too_old_are_refused() -> None:
    service, _ = _service(market(), now=MONDAY + timedelta(days=2))
    with pytest.raises(ReplayUnavailableError, match="futuro"):
        service.replay(MONDAY, MONDAY + timedelta(days=5))

    service, _ = _service(market(), now=MONDAY + timedelta(days=800))
    with pytest.raises(ReplayUnavailableError, match="730"):
        service.replay(MONDAY, MONDAY + timedelta(days=5))


def test_missing_data_for_every_instrument_is_refused() -> None:
    service, _ = _service({s: MarketDataError("none") for s in ("EURUSD=X", "GBPUSD=X", "USDJPY=X")}, now=MONDAY + timedelta(days=20))

    with pytest.raises(ReplayUnavailableError):
        service.replay(MONDAY + timedelta(days=3), MONDAY + timedelta(days=7))


def test_ai_objections_come_only_from_contemporaneous_ledger_records() -> None:
    records = [
        {"analysis_id": "a1", "created_at": "2026-10-08T03:10:00+00:00", "evidence": {"instrument": "EURUSD=X"}, "decision": {"trading_stance": "NO_OPERAR"}},
        {"analysis_id": "a2", "created_at": "2026-10-08T05:00:00+00:00", "evidence": {"instrument": "EURUSD=X"}, "decision": {"trading_stance": "COMPRA"}},
        {"analysis_id": "broken"},
    ]

    objections = objections_from_ai_ledger(records)

    assert [(o.symbol, o.recorded_at) for o in objections] == [("EURUSD=X", datetime(2026, 10, 8, 3, 10, tzinfo=timezone.utc))]
    service, _ = _service(market(), now=MONDAY + timedelta(days=11), ai_records=lambda: records)
    report = service.replay(MONDAY + timedelta(days=3), MONDAY + timedelta(days=7))
    assert report.ai_decisions_used == 0  # no validated evidence, so C never reached the objection check
    assert report.no_trade_baseline == Decimal("10000")
