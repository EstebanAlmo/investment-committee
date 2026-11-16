"""Tests for AnalyzeAssetTool and CompareAssetsTool. See spec-006 S15.1, S15.4, S26.

The core MVP guarantee lives here: an unsupported asset class returns
PolicyDeclined and RecordDecisionUseCase.analyze_and_record is never called -
nothing reaches the Application layer or the Decision Ledger.
"""

from __future__ import annotations

import pytest

from investment_committee.conversation.asset_policy import TickerPatternAssetClassPolicy
from investment_committee.conversation.models import IntentType
from investment_committee.conversation.tools import (
    AnalyzeAssetTool,
    CompareAssetsTool,
    HelpContent,
    PolicyDeclined,
)
from investment_committee.memory import DecisionRecord

from ._support import FakeRecordDecisionUseCase, make_intent, make_session


def _analyze(symbol: str | None) -> tuple[object, FakeRecordDecisionUseCase]:
    use_case = FakeRecordDecisionUseCase()
    tool = AnalyzeAssetTool(use_case, TickerPatternAssetClassPolicy())
    parameters = {"symbol": symbol} if symbol is not None else {}
    return tool.execute(make_intent(IntentType.ANALYZE_ASSET, **parameters), make_session()), use_case


def test_supported_symbol_is_analyzed_and_recorded() -> None:
    outcome, use_case = _analyze("AAPL")

    assert isinstance(outcome, DecisionRecord)
    assert outcome.asset.ticker == "AAPL"
    assert use_case.calls == ["AAPL"]


@pytest.mark.parametrize("symbol", ["BTC-USD", "XAUUSD=X", "GC=F", "AAPL240119C00150000"])
def test_unsupported_symbol_is_declined_without_reaching_application_layer(symbol: str) -> None:
    outcome, use_case = _analyze(symbol)

    assert isinstance(outcome, PolicyDeclined)
    assert symbol in outcome.message
    assert use_case.calls == []


def test_missing_symbol_asks_which_asset_without_calling_application() -> None:
    outcome, use_case = _analyze(None)

    assert isinstance(outcome, HelpContent)
    assert "/analyze AAPL" in outcome.description
    assert use_case.calls == []


def _compare(symbols: str | None) -> tuple[object, FakeRecordDecisionUseCase]:
    use_case = FakeRecordDecisionUseCase()
    tool = CompareAssetsTool(use_case, TickerPatternAssetClassPolicy())
    parameters = {"symbols": symbols} if symbols is not None else {}
    return tool.execute(make_intent(IntentType.COMPARE_ASSETS, **parameters), make_session()), use_case


def test_compare_two_supported_symbols() -> None:
    outcome, use_case = _compare("MSFT,AAPL")

    assert isinstance(outcome, tuple)
    assert [record.asset.ticker for record in outcome] == ["MSFT", "AAPL"]
    assert use_case.calls == ["MSFT", "AAPL"]


@pytest.mark.parametrize("symbols", ["MSFT,BTC-USD", "XAUUSD=X,AAPL"])
def test_compare_with_any_unsupported_symbol_analyzes_neither(symbols: str) -> None:
    outcome, use_case = _compare(symbols)

    assert isinstance(outcome, PolicyDeclined)
    assert use_case.calls == []


@pytest.mark.parametrize("symbols", [None, "MSFT", "A,B,C"])
def test_compare_without_exactly_two_symbols_asks_for_clarification(symbols: str | None) -> None:
    outcome, use_case = _compare(symbols)

    assert isinstance(outcome, HelpContent)
    assert use_case.calls == []


def test_unrecognized_ticker_shape_gets_a_ticker_hint_not_an_asset_class_claim() -> None:
    outcome, use_case = _analyze("NOTATICKERX")

    assert isinstance(outcome, PolicyDeclined)
    assert "no parece un símbolo que yo pueda analizar" in outcome.message
    assert "No se analizó ni se registró nada" in outcome.message
    assert use_case.calls == []


@pytest.mark.parametrize("symbol", ["EUR/USD", "EURUSD=X", "eur/usd", "eurusd=x", "EURUSD"])
def test_forex_pair_in_any_spelling_is_analyzed_under_the_canonical_symbol(symbol: str) -> None:
    outcome, use_case = _analyze(symbol)

    assert isinstance(outcome, DecisionRecord)
    assert outcome.asset.ticker == "EURUSD=X"
    assert use_case.calls == ["EURUSD=X"]


def test_gold_in_slash_form_is_still_declined_as_a_commodity() -> None:
    outcome, use_case = _analyze("XAU/USD")

    assert isinstance(outcome, PolicyDeclined)
    assert outcome.reason == "unsupported_commodity"
    assert use_case.calls == []


def test_compare_normalizes_forex_pairs() -> None:
    outcome, use_case = _compare("EUR/USD,aapl")

    assert isinstance(outcome, tuple)
    assert use_case.calls == ["EURUSD=X", "AAPL"]
