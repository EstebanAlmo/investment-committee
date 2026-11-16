"""Tests for investment_committee.conversation.command_parser.

See docs/specs/spec-006-conversational-interface.md S12, S26.
"""

from __future__ import annotations

import pytest

from investment_committee.conversation.command_parser import CommandParser
from investment_committee.conversation.models import Intent, IntentType


@pytest.fixture
def parser() -> CommandParser:
    return CommandParser()


class TestNonCommandText:
    def test_text_without_leading_slash_returns_none(self, parser: CommandParser) -> None:
        assert parser.parse("Analyze Apple") is None

    def test_empty_text_returns_none(self, parser: CommandParser) -> None:
        assert parser.parse("") is None

    def test_whitespace_only_text_returns_none(self, parser: CommandParser) -> None:
        assert parser.parse("   ") is None


class TestRecognizedCommandsWithArguments:
    def test_help(self, parser: CommandParser) -> None:
        intent = parser.parse("/help")
        assert intent == Intent(type=IntentType.HELP, parameters={}, raw_text="/help")

    def test_analyze_with_symbol(self, parser: CommandParser) -> None:
        intent = parser.parse("/analyze AAPL")
        assert intent == Intent(type=IntentType.ANALYZE_ASSET, parameters={"symbol": "AAPL"}, raw_text="/analyze AAPL")

    def test_history_with_symbol(self, parser: CommandParser) -> None:
        intent = parser.parse("/history AAPL")
        assert intent == Intent(
            type=IntentType.DECISION_HISTORY, parameters={"symbol": "AAPL"}, raw_text="/history AAPL"
        )

    def test_explain_with_symbol(self, parser: CommandParser) -> None:
        intent = parser.parse("/explain AAPL")
        assert intent == Intent(
            type=IntentType.EXPLAIN_DECISION, parameters={"symbol": "AAPL"}, raw_text="/explain AAPL"
        )

    def test_portfolio(self, parser: CommandParser) -> None:
        intent = parser.parse("/portfolio")
        assert intent == Intent(type=IntentType.PORTFOLIO_SUMMARY, parameters={}, raw_text="/portfolio")

    def test_performance(self, parser: CommandParser) -> None:
        intent = parser.parse("/performance")
        assert intent == Intent(type=IntentType.PERFORMANCE_SUMMARY, parameters={}, raw_text="/performance")

    def test_command_name_is_case_insensitive(self, parser: CommandParser) -> None:
        intent = parser.parse("/ANALYZE AAPL")
        assert intent is not None
        assert intent.type is IntentType.ANALYZE_ASSET

    def test_symbol_argument_is_uppercased(self, parser: CommandParser) -> None:
        intent = parser.parse("/analyze aapl")
        assert intent is not None
        assert intent.parameters == {"symbol": "AAPL"}

    def test_extra_internal_whitespace_in_argument_is_collapsed(self, parser: CommandParser) -> None:
        intent = parser.parse("/analyze   AAPL")
        assert intent is not None
        assert intent.parameters == {"symbol": "AAPL"}


class TestCompareCommand:
    def test_space_separated_symbols_normalize_to_comma_joined(self, parser: CommandParser) -> None:
        intent = parser.parse("/compare MSFT AAPL")
        assert intent == Intent(
            type=IntentType.COMPARE_ASSETS, parameters={"symbols": "MSFT,AAPL"}, raw_text="/compare MSFT AAPL"
        )

    def test_comma_separated_symbols_normalize_unchanged(self, parser: CommandParser) -> None:
        intent = parser.parse("/compare MSFT,AAPL")
        assert intent is not None
        assert intent.parameters == {"symbols": "MSFT,AAPL"}

    def test_mixed_comma_and_whitespace_normalizes_cleanly(self, parser: CommandParser) -> None:
        intent = parser.parse("/compare  msft,   aapl")
        assert intent is not None
        assert intent.parameters == {"symbols": "MSFT,AAPL"}


class TestMissingOrMalformedArguments:
    def test_analyze_with_no_argument_has_no_symbol_parameter(self, parser: CommandParser) -> None:
        intent = parser.parse("/analyze")
        assert intent == Intent(type=IntentType.ANALYZE_ASSET, parameters={}, raw_text="/analyze")

    def test_analyze_with_only_whitespace_argument_has_no_symbol_parameter(self, parser: CommandParser) -> None:
        intent = parser.parse("/analyze    ")
        assert intent is not None
        assert intent.parameters == {}

    def test_compare_with_no_argument_has_no_symbols_parameter(self, parser: CommandParser) -> None:
        intent = parser.parse("/compare")
        assert intent == Intent(type=IntentType.COMPARE_ASSETS, parameters={}, raw_text="/compare")

    def test_compare_with_a_single_symbol_does_not_raise(self, parser: CommandParser) -> None:
        intent = parser.parse("/compare MSFT")
        assert intent is not None
        assert intent.parameters == {"symbols": "MSFT"}

    def test_slash_alone_resolves_to_unknown(self, parser: CommandParser) -> None:
        intent = parser.parse("/")
        assert intent == Intent(type=IntentType.UNKNOWN, parameters={}, raw_text="/")

    def test_slash_followed_by_only_whitespace_resolves_to_unknown(self, parser: CommandParser) -> None:
        intent = parser.parse("/   ")
        assert intent is not None
        assert intent.type is IntentType.UNKNOWN


class TestUnrecognizedCommand:
    def test_unrecognized_command_resolves_to_unknown_not_none(self, parser: CommandParser) -> None:
        intent = parser.parse("/frobnicate")
        assert intent == Intent(type=IntentType.UNKNOWN, parameters={}, raw_text="/frobnicate")

    def test_unrecognized_command_with_argument_still_resolves_to_unknown_with_no_parameters(
        self, parser: CommandParser
    ) -> None:
        intent = parser.parse("/frobnicate xyz")
        assert intent == Intent(type=IntentType.UNKNOWN, parameters={}, raw_text="/frobnicate xyz")


class TestNeverRaises:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            "/",
            "/   ",
            "/analyze",
            "/analyze   ",
            "/compare",
            "/compare MSFT",
            "/frobnicate",
            "/frobnicate some very long argument with, commas, and   whitespace",
            "not a command at all",
            "/help /analyze /compare",
        ],
    )
    def test_parse_never_raises(self, parser: CommandParser, text: str) -> None:
        parser.parse(text)  # asserting only that no exception propagates


class TestForexSymbolNormalization:
    @pytest.mark.parametrize("argument", ["EUR/USD", "EURUSD=X", "eur/usd", "eurusd=x"])
    def test_analyze_forex_pair_in_either_spelling_yields_canonical_symbol(
        self, parser: CommandParser, argument: str
    ) -> None:
        intent = parser.parse(f"/analyze {argument}")

        assert intent is not None
        assert intent.type is IntentType.ANALYZE_ASSET
        assert intent.parameters == {"symbol": "EURUSD=X"}

    def test_history_forex_pair_yields_canonical_symbol(self, parser: CommandParser) -> None:
        intent = parser.parse("/history EUR/USD")

        assert intent is not None
        assert intent.parameters == {"symbol": "EURUSD=X"}

    def test_compare_normalizes_each_symbol(self, parser: CommandParser) -> None:
        intent = parser.parse("/compare EUR/USD aapl")

        assert intent is not None
        assert intent.parameters == {"symbols": "EURUSD=X,AAPL"}

    def test_start_command_is_help(self, parser: CommandParser) -> None:
        intent = parser.parse("/start")

        assert intent is not None
        assert intent.type is IntentType.HELP


class TestSimulatedTradingCommands:
    @pytest.mark.parametrize(
        ("text", "intent_type", "parameters"),
        [
            ("/trade EUR/USD", IntentType.TRADE_SIGNAL, {"symbol": "EURUSD=X"}),
            ("/trade", IntentType.TRADE_SIGNAL, {}),
            ("/explicar_trade", IntentType.EXPLAIN_TRADE, {}),
            ("/backtest eurusd=x", IntentType.BACKTEST, {"symbol": "EURUSD=X"}),
            ("/operaciones", IntentType.SIMULATED_TRADES, {}),
            ("/aprender stop-loss", IntentType.LEARN_CONCEPT, {"concept": "stop-loss"}),
            ("/aprender riesgo/beneficio", IntentType.LEARN_CONCEPT, {"concept": "riesgo/beneficio"}),
            ("/aprender", IntentType.LEARN_CONCEPT, {}),
        ],
    )
    def test_new_commands(self, parser: CommandParser, text: str, intent_type: IntentType, parameters: dict[str, str]) -> None:
        intent = parser.parse(text)

        assert intent is not None
        assert intent.type is intent_type
        assert dict(intent.parameters) == parameters

    @pytest.mark.parametrize("command", ["/analyze AAPL", "/history AAPL", "/explain AAPL", "/compare MSFT AAPL", "/help"])
    def test_existing_commands_are_unchanged(self, parser: CommandParser, command: str) -> None:
        intent = parser.parse(command)

        assert intent is not None and intent.type is not IntentType.UNKNOWN
