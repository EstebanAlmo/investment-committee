"""Tests for investment_committee.application.service.ApplicationService."""

from __future__ import annotations

import pytest

from investment_committee.application.errors import (
    CommitteeDecisionFailedError,
    IndicatorCalculationFailedError,
    InvalidTickerError,
    MarketDataUnavailableError,
)
from investment_committee.application.service import ApplicationService
from investment_committee.committee import CommitteeEngineError, InvestmentDecision
from investment_committee.domain import Asset, MarketSnapshot
from investment_committee.indicators import IndicatorCalculationError, IndicatorSnapshot
from investment_committee.market_intelligence import MarketDataError

from ._support import (
    FakeCommitteeEngine,
    FakeIndicatorEngine,
    FakeMarketIntelligenceService,
    make_asset,
    make_decision,
    make_indicator_snapshot,
    make_market_snapshot,
)


def _wired_service(
    asset: Asset | None = None,
) -> tuple[
    ApplicationService,
    FakeMarketIntelligenceService,
    FakeIndicatorEngine,
    FakeCommitteeEngine,
    MarketSnapshot,
    IndicatorSnapshot,
    InvestmentDecision,
]:
    asset = asset if asset is not None else make_asset()
    market_snapshot = make_market_snapshot(asset)
    indicator_snapshot = make_indicator_snapshot(market_snapshot)
    decision = make_decision(asset)

    market = FakeMarketIntelligenceService(market_snapshot)
    indicator = FakeIndicatorEngine(indicator_snapshot)
    committee = FakeCommitteeEngine(decision)
    service = ApplicationService(
        market_intelligence_service=market,
        indicator_engine=indicator,
        committee_engine=committee,
    )
    return service, market, indicator, committee, market_snapshot, indicator_snapshot, decision


def test_valid_ticker_orchestration_returns_wrapped_result() -> None:
    service, *_rest, market_snapshot, indicator_snapshot, decision = _wired_service()

    result = service.analyze_asset("MSFT")

    assert result.market_snapshot is market_snapshot
    assert result.indicator_snapshot is indicator_snapshot
    assert result.decision is decision


@pytest.mark.parametrize("raw_symbol", ["  msft  ", "msft", " MSFT", "MSFT", "\tmsft\n"])
def test_input_trimming_and_uppercase_normalization(raw_symbol: str) -> None:
    service, market, *_rest = _wired_service()

    service.analyze_asset(raw_symbol)

    assert market.calls == ["MSFT"]


@pytest.mark.parametrize("blank_symbol", ["", "   ", "\t\n"])
def test_empty_and_whitespace_only_symbol_rejected(blank_symbol: str) -> None:
    service, market, indicator, committee, *_rest = _wired_service()

    with pytest.raises(InvalidTickerError):
        service.analyze_asset(blank_symbol)

    assert market.calls == []
    assert indicator.calls == []
    assert committee.calls == []


@pytest.mark.parametrize("malformed_symbol", ["AA PL", "  MS FT  ", "A B C"])
def test_internal_whitespace_rejected(malformed_symbol: str) -> None:
    service, market, indicator, committee, *_rest = _wired_service()

    with pytest.raises(InvalidTickerError):
        service.analyze_asset(malformed_symbol)

    assert market.calls == []
    assert indicator.calls == []
    assert committee.calls == []


def test_exact_dependency_call_order() -> None:
    call_log: list[str] = []
    asset = make_asset()
    market_snapshot = make_market_snapshot(asset)
    indicator_snapshot = make_indicator_snapshot(market_snapshot)
    decision = make_decision(asset)

    service = ApplicationService(
        market_intelligence_service=FakeMarketIntelligenceService(market_snapshot, call_log=call_log),
        indicator_engine=FakeIndicatorEngine(indicator_snapshot, call_log=call_log),
        committee_engine=FakeCommitteeEngine(decision, call_log=call_log),
    )

    service.analyze_asset("MSFT")

    assert call_log == ["market", "indicator", "committee"]


def test_market_data_fetched_exactly_once() -> None:
    service, market, *_rest = _wired_service()

    service.analyze_asset("MSFT")

    assert len(market.calls) == 1


def test_snapshots_passed_unchanged_between_layers() -> None:
    service, market, indicator, committee, market_snapshot, indicator_snapshot, decision = _wired_service()

    service.analyze_asset("MSFT")

    assert indicator.calls[0] is market_snapshot
    assert committee.calls[0] is indicator_snapshot


def test_dependency_injection_with_duck_typed_fakes() -> None:
    """Fakes need not subclass MarketIntelligenceService/IndicatorEngine/DeterministicCommitteeEngine."""
    service, *_rest = _wired_service()

    assert isinstance(service, ApplicationService)


def test_provider_failure_translated_and_stops_pipeline() -> None:
    original = MarketDataError("no price history returned")
    market = FakeMarketIntelligenceService(original)
    indicator = FakeIndicatorEngine(make_indicator_snapshot())
    committee = FakeCommitteeEngine(make_decision())
    service = ApplicationService(market, indicator, committee)

    with pytest.raises(MarketDataUnavailableError) as exc_info:
        service.analyze_asset("MSFT")

    assert exc_info.value.__cause__ is original
    assert isinstance(exc_info.value.__cause__, MarketDataError)
    assert indicator.calls == []
    assert committee.calls == []


def test_indicator_failure_translated_and_stops_pipeline() -> None:
    original = IndicatorCalculationError("MarketSnapshot contains no PriceBars")
    market_snapshot = make_market_snapshot()
    market = FakeMarketIntelligenceService(market_snapshot)
    indicator = FakeIndicatorEngine(original)
    committee = FakeCommitteeEngine(make_decision())
    service = ApplicationService(market, indicator, committee)

    with pytest.raises(IndicatorCalculationFailedError) as exc_info:
        service.analyze_asset("MSFT")

    assert exc_info.value.__cause__ is original
    assert isinstance(exc_info.value.__cause__, IndicatorCalculationError)
    assert committee.calls == []


def test_committee_failure_translated() -> None:
    original = CommitteeEngineError("wrong role in a slot")
    market_snapshot = make_market_snapshot()
    indicator_snapshot = make_indicator_snapshot(market_snapshot)
    market = FakeMarketIntelligenceService(market_snapshot)
    indicator = FakeIndicatorEngine(indicator_snapshot)
    committee = FakeCommitteeEngine(original)
    service = ApplicationService(market, indicator, committee)

    with pytest.raises(CommitteeDecisionFailedError) as exc_info:
        service.analyze_asset("MSFT")

    assert exc_info.value.__cause__ is original
    assert isinstance(exc_info.value.__cause__, CommitteeEngineError)


def test_unexpected_exception_from_provider_propagates_unmodified() -> None:
    market = FakeMarketIntelligenceService(RuntimeError("unexpected provider bug"))
    indicator = FakeIndicatorEngine(make_indicator_snapshot())
    committee = FakeCommitteeEngine(make_decision())
    service = ApplicationService(market, indicator, committee)

    with pytest.raises(RuntimeError):
        service.analyze_asset("MSFT")


def test_unexpected_exception_from_indicator_engine_propagates_unmodified() -> None:
    market_snapshot = make_market_snapshot()
    market = FakeMarketIntelligenceService(market_snapshot)
    indicator = FakeIndicatorEngine(ValueError("unexpected bug, not IndicatorCalculationError"))
    committee = FakeCommitteeEngine(make_decision())
    service = ApplicationService(market, indicator, committee)

    with pytest.raises(ValueError):
        service.analyze_asset("MSFT")

    assert committee.calls == []


def test_unexpected_exception_from_committee_engine_propagates_unmodified() -> None:
    market_snapshot = make_market_snapshot()
    indicator_snapshot = make_indicator_snapshot(market_snapshot)
    market = FakeMarketIntelligenceService(market_snapshot)
    indicator = FakeIndicatorEngine(indicator_snapshot)
    committee = FakeCommitteeEngine(RuntimeError("unexpected committee bug"))
    service = ApplicationService(market, indicator, committee)

    with pytest.raises(RuntimeError):
        service.analyze_asset("MSFT")
