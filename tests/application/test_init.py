"""Tests for investment_committee.application's package-level exports and convenience function."""

from __future__ import annotations

import investment_committee.application as application
import pytest

from investment_committee.application.models import AnalysisResult

from ._support import make_asset, make_decision, make_indicator_snapshot, make_market_snapshot


class _FakeApplicationService:
    """Deterministic fake ApplicationService; records every symbol it was called with."""

    def __init__(self, result: AnalysisResult) -> None:
        self._result = result
        self.calls: list[str] = []

    def analyze_asset(self, symbol: str) -> AnalysisResult:
        self.calls.append(symbol)
        return self._result


def test_module_level_analyze_asset_delegates_to_a_freshly_built_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = make_asset("MSFT")
    market_snapshot = make_market_snapshot(asset)
    indicator_snapshot = make_indicator_snapshot(market_snapshot)
    decision = make_decision(asset)
    result = AnalysisResult(
        market_snapshot=market_snapshot,
        indicator_snapshot=indicator_snapshot,
        decision=decision,
    )

    built_services: list[_FakeApplicationService] = []

    def _fake_builder() -> _FakeApplicationService:
        service = _FakeApplicationService(result)
        built_services.append(service)
        return service

    monkeypatch.setattr(application, "build_default_application_service", _fake_builder)

    first_result = application.analyze_asset("MSFT")
    second_result = application.analyze_asset("AAPL")

    # The exact AnalysisResult from the fake service is returned unchanged.
    assert first_result is result
    assert second_result is result

    # build_default_application_service() was called exactly once per analyze_asset() call.
    assert len(built_services) == 2

    # A fresh service is built per call - no module-level caching or singleton reuse.
    assert built_services[0] is not built_services[1]

    # The symbol is passed through to the built service's analyze_asset() unchanged.
    assert built_services[0].calls == ["MSFT"]
    assert built_services[1].calls == ["AAPL"]


def test_public_exports_are_exactly_the_expected_set() -> None:
    expected = {
        "ApplicationService",
        "AnalysisResult",
        "ApplicationServiceError",
        "InvalidTickerError",
        "MarketDataUnavailableError",
        "IndicatorCalculationFailedError",
        "CommitteeDecisionFailedError",
        "build_default_application_service",
        "analyze_asset",
        "RecordDecisionUseCase",
        "RecordDecisionResult",
        "DecisionRecordingError",
        "build_default_record_decision_use_case",
    }
    assert set(application.__all__) == expected
    for name in expected:
        assert hasattr(application, name)
