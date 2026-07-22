"""Tests for investment_committee.application.factory."""

from __future__ import annotations

import pytest

from investment_committee.application.factory import build_default_application_service
from investment_committee.application.service import ApplicationService
from investment_committee.committee import DeterministicCommitteeEngine
from investment_committee.indicators import IndicatorEngine
from investment_committee.market_intelligence import MarketIntelligenceService
from investment_committee.market_intelligence.providers.yfinance_provider import YFinanceProvider


def test_returns_application_service() -> None:
    service = build_default_application_service()

    assert isinstance(service, ApplicationService)


def test_wires_expected_concrete_collaborators() -> None:
    service = build_default_application_service()

    assert isinstance(service._market_intelligence_service, MarketIntelligenceService)
    assert isinstance(service._market_intelligence_service.provider, YFinanceProvider)
    assert isinstance(service._indicator_engine, IndicatorEngine)
    assert isinstance(service._committee_engine, DeterministicCommitteeEngine)


def test_no_network_call_during_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(self: YFinanceProvider, ticker: str) -> None:
        raise AssertionError("build_default_application_service must not fetch market data")

    monkeypatch.setattr(YFinanceProvider, "get_daily_history", _blocked)

    build_default_application_service()
