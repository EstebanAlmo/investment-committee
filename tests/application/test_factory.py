"""Tests for investment_committee.application.factory."""

from __future__ import annotations

from pathlib import Path

import pytest

from investment_committee.application.factory import (
    build_default_application_service,
    build_default_record_decision_use_case,
)
from investment_committee.application.record_decision import RecordDecisionUseCase
from investment_committee.application.service import ApplicationService
from investment_committee.committee import DeterministicCommitteeEngine
from investment_committee.indicators import IndicatorEngine
from investment_committee.market_intelligence import MarketIntelligenceService
from investment_committee.market_intelligence.providers.yfinance_provider import YFinanceProvider
from investment_committee.memory import DEFAULT_LEDGER_PATH, DETERMINISTIC_STRATEGY_METADATA
from investment_committee.memory.ledgers.in_memory import InMemoryDecisionLedger
from investment_committee.memory.ledgers.jsonl import JsonLinesDecisionLedger


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


def test_build_default_record_decision_use_case_returns_use_case() -> None:
    use_case = build_default_record_decision_use_case(ledger=InMemoryDecisionLedger())

    assert isinstance(use_case, RecordDecisionUseCase)


def test_build_default_record_decision_use_case_wires_deterministic_application_service() -> None:
    use_case = build_default_record_decision_use_case(ledger=InMemoryDecisionLedger())

    assert isinstance(use_case._application_service, ApplicationService)
    assert isinstance(use_case._application_service._committee_engine, DeterministicCommitteeEngine)


def test_build_default_record_decision_use_case_wires_deterministic_strategy_metadata() -> None:
    use_case = build_default_record_decision_use_case(ledger=InMemoryDecisionLedger())

    assert use_case._strategy_metadata is DETERMINISTIC_STRATEGY_METADATA


def test_build_default_record_decision_use_case_defaults_to_jsonl_ledger_at_default_path() -> None:
    use_case = build_default_record_decision_use_case()

    assert isinstance(use_case._ledger, JsonLinesDecisionLedger)
    assert use_case._ledger._path == DEFAULT_LEDGER_PATH


def test_build_default_record_decision_use_case_respects_ledger_override(tmp_path: Path) -> None:
    override = JsonLinesDecisionLedger(tmp_path / "custom.jsonl")

    use_case = build_default_record_decision_use_case(ledger=override)

    assert use_case._ledger is override


def test_build_default_record_decision_use_case_no_network_during_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _blocked(self: YFinanceProvider, ticker: str) -> None:
        raise AssertionError("build_default_record_decision_use_case must not fetch market data")

    monkeypatch.setattr(YFinanceProvider, "get_daily_history", _blocked)

    build_default_record_decision_use_case(ledger=InMemoryDecisionLedger())


def test_build_default_record_decision_use_case_returns_fresh_instances() -> None:
    first = build_default_record_decision_use_case(ledger=InMemoryDecisionLedger())
    second = build_default_record_decision_use_case(ledger=InMemoryDecisionLedger())

    assert first is not second
    assert first._application_service is not second._application_service


def test_build_default_record_decision_use_case_no_cached_default_ledger() -> None:
    """No hidden singleton: two calls with no override build two distinct ledger instances."""
    first = build_default_record_decision_use_case()
    second = build_default_record_decision_use_case()

    assert first._ledger is not second._ledger
