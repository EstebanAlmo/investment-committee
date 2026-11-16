"""Tests for investment_committee.conversation's package-level exports.

Mirrors tests/application/test_init.py's pattern. See
docs/specs/spec-006-conversational-interface.md S24, S26.
"""

from __future__ import annotations

import investment_committee.conversation as conversation


def test_public_exports_are_exactly_the_expected_set() -> None:
    expected = {
        "ChannelType",
        "UserIdentity",
        "ConversationIdentity",
        "AudioPayload",
        "ConversationMessage",
        "ConversationResponse",
        "IntentType",
        "Intent",
        "ConversationSession",
        "AccessPolicy",
        "AllowlistAccessPolicy",
        "CommandParser",
        "IntentRouter",
        "RuleBasedIntentRouter",
        "SymbolResolver",
        "StaticSymbolResolver",
        "AssetClassDecision",
        "AssetClassPolicy",
        "TickerPatternAssetClassPolicy",
        "SessionStore",
        "InMemorySessionStore",
        "SpeechToTranscriber",
        "TextToSpeechSynthesizer",
        "ResponseSummarizer",
        "TemplateResponseSummarizer",
        "ConversationAdapter",
        "ConversationService",
        "build_default_conversation_service",
        "DEFAULT_NAME_TO_TICKER",
        "ConversationError",
        "TranscriptionError",
        "SynthesisError",
        "SessionStoreError",
        "LLMInterpretationError",
    }
    assert set(conversation.__all__) == expected
    for name in expected:
        assert hasattr(conversation, name)
