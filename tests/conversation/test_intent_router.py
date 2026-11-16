"""Tests for investment_committee.conversation.intent_router.

See docs/specs/spec-006-conversational-interface.md S12. This phase
delivers only the IntentRouter port - RuleBasedIntentRouter, LLMIntentRouter,
and FallbackIntentRouter are scoped to a later phase, so these tests confirm
the port shape only.
"""

from __future__ import annotations

import pytest

from investment_committee.conversation.intent_router import IntentRouter
from investment_committee.conversation.models import ChannelType, ConversationIdentity, ConversationSession, Intent, IntentType


class _FakeIntentRouter(IntentRouter):
    def route(self, text: str, session: ConversationSession) -> Intent:
        return Intent(type=IntentType.UNKNOWN, parameters={}, raw_text=text)


def test_intent_router_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        IntentRouter()  # type: ignore[abstract]


def test_concrete_router_returns_an_intent() -> None:
    session = ConversationSession(
        conversation_identity=ConversationIdentity(channel=ChannelType.TELEGRAM, channel_conversation_id="chat-1")
    )

    intent = _FakeIntentRouter().route("hello", session)

    assert intent.type is IntentType.UNKNOWN
    assert intent.raw_text == "hello"


# --- RuleBasedIntentRouter (spec-006 S12, S26) ---

from investment_committee.conversation.intent_router import RuleBasedIntentRouter  # noqa: E402
from investment_committee.conversation.symbol_resolver import StaticSymbolResolver  # noqa: E402

_NAMES = {"apple": "AAPL", "nvidia": "NVDA", "tesla": "TSLA", "microsoft": "MSFT", "google": "GOOGL"}


def _session() -> ConversationSession:
    return ConversationSession(
        conversation_identity=ConversationIdentity(channel=ChannelType.TELEGRAM, channel_conversation_id="chat-1")
    )


def _route(text: str) -> Intent:
    return RuleBasedIntentRouter(symbol_resolver=StaticSymbolResolver(_NAMES)).route(text, _session())


@pytest.mark.parametrize(
    ("text", "expected_type", "expected_parameters"),
    [
        ("Analyze Apple", IntentType.ANALYZE_ASSET, {"symbol": "AAPL"}),
        ("analyze apple", IntentType.ANALYZE_ASSET, {"symbol": "AAPL"}),
        ("Analyse Apple please", IntentType.ANALYZE_ASSET, {"symbol": "AAPL"}),
        ("Should I buy Nvidia?", IntentType.ANALYZE_ASSET, {"symbol": "NVDA"}),
        ("What does the committee think about TSLA?", IntentType.ANALYZE_ASSET, {"symbol": "TSLA"}),
        ("Apple", IntentType.ANALYZE_ASSET, {"symbol": "AAPL"}),
        ("Analyze BRK.B", IntentType.ANALYZE_ASSET, {"symbol": "BRK.B"}),
        ("Analyze BTC-USD", IntentType.ANALYZE_ASSET, {"symbol": "BTC-USD"}),
        ("Buy Bitcoin.", IntentType.UNSUPPORTED_TRADE_REQUEST, {}),
        ("Buy Apple", IntentType.UNSUPPORTED_TRADE_REQUEST, {"symbol": "AAPL"}),
        ("sell TSLA now", IntentType.UNSUPPORTED_TRADE_REQUEST, {"symbol": "TSLA"}),
        ("Place an order for MSFT", IntentType.UNSUPPORTED_TRADE_REQUEST, {"symbol": "MSFT"}),
        ("Why was Apple a BUY?", IntentType.EXPLAIN_DECISION, {"symbol": "AAPL"}),
        ("Explain that", IntentType.EXPLAIN_DECISION, {}),
        ("What have we recommended for Tesla?", IntentType.DECISION_HISTORY, {"symbol": "TSLA"}),
        ("Show my last 5 analyses", IntentType.DECISION_HISTORY, {"limit": "5"}),
        ("Show my last five analyses", IntentType.DECISION_HISTORY, {"limit": "5"}),
        ("history AAPL", IntentType.DECISION_HISTORY, {"symbol": "AAPL"}),
        ("Compare Microsoft and Apple", IntentType.COMPARE_ASSETS, {"symbols": "MSFT,AAPL"}),
        ("help", IntentType.HELP, {}),
        ("What can you do?", IntentType.HELP, {}),
        ("How is my portfolio?", IntentType.PORTFOLIO_SUMMARY, {}),
        ("How did we do?", IntentType.PERFORMANCE_SUMMARY, {}),
        ("hello there", IntentType.UNKNOWN, {}),
        ("Analyze EUR/USD", IntentType.ANALYZE_ASSET, {"symbol": "EURUSD=X"}),
        ("Analyze EURUSD=X", IntentType.ANALYZE_ASSET, {"symbol": "EURUSD=X"}),
        # Spanish free text (es-CO is the default interface language)
        ("Analiza Apple", IntentType.ANALYZE_ASSET, {"symbol": "AAPL"}),
        ("analiza EUR/USD", IntentType.ANALYZE_ASSET, {"symbol": "EURUSD=X"}),
        ("¿Debería comprar Nvidia?", IntentType.ANALYZE_ASSET, {"symbol": "NVDA"}),
        ("Compra Apple", IntentType.UNSUPPORTED_TRADE_REQUEST, {"symbol": "AAPL"}),
        ("vende TSLA ya", IntentType.UNSUPPORTED_TRADE_REQUEST, {"symbol": "TSLA"}),
        ("¿Por qué Apple quedó en COMPRAR?", IntentType.EXPLAIN_DECISION, {"symbol": "AAPL"}),
        ("explica eso", IntentType.EXPLAIN_DECISION, {}),
        ("historial AAPL", IntentType.DECISION_HISTORY, {"symbol": "AAPL"}),
        ("Muéstrame las últimas 5 decisiones", IntentType.DECISION_HISTORY, {"limit": "5"}),
        ("Compara Microsoft y Apple", IntentType.COMPARE_ASSETS, {"symbols": "MSFT,AAPL"}),
        ("ayuda", IntentType.HELP, {}),
        ("¿Qué puedes hacer?", IntentType.HELP, {}),
        ("¿Cómo va mi portafolio?", IntentType.PORTFOLIO_SUMMARY, {}),
        ("¿Cuál ha sido el rendimiento?", IntentType.PERFORMANCE_SUMMARY, {}),
        ("hola", IntentType.UNKNOWN, {}),
    ],
)
def test_rule_based_router_table(text: str, expected_type: IntentType, expected_parameters: dict[str, str]) -> None:
    intent = _route(text)

    assert intent.type is expected_type
    assert dict(intent.parameters) == expected_parameters
    assert intent.raw_text == text


def test_analyze_request_with_unresolvable_name_asks_for_clarification() -> None:
    intent = _route("Analyze foo corp")

    assert intent.type is IntentType.UNKNOWN
    assert "No reconozco" in intent.parameters["clarification"]
    assert "AAPL" in intent.parameters["clarification"]


@pytest.mark.parametrize("text", ["", "   ", "?!", "🙂", "a" * 5000])
def test_rule_based_router_never_raises(text: str) -> None:
    intent = _route(text)

    assert isinstance(intent.type, IntentType)
