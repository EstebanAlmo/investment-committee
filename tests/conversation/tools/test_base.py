"""Tests for investment_committee.conversation.tools.base.

See docs/specs/spec-006-conversational-interface.md S14, S26. This phase
delivers the base models, ToolOutcome, and the Tool port only - ToolRouter
and every concrete Tool are scoped to a later phase.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from investment_committee.conversation.models import ChannelType, ConversationIdentity, ConversationSession, Intent, IntentType
from investment_committee.conversation.tools.base import HelpContent, NotYetAvailable, PolicyDeclined, Tool


def _session() -> ConversationSession:
    return ConversationSession(
        conversation_identity=ConversationIdentity(channel=ChannelType.TELEGRAM, channel_conversation_id="chat-1")
    )


class _FakeTool(Tool):
    def execute(self, intent: Intent, session: ConversationSession) -> HelpContent:
        return HelpContent(available_intents=("Analyze an asset",), description="help text")


class TestHelpContent:
    def test_frozen(self) -> None:
        content = HelpContent(available_intents=("a",), description="d")
        with pytest.raises(FrozenInstanceError):
            content.description = "other"  # type: ignore[misc]


class TestNotYetAvailable:
    def test_fields(self) -> None:
        outcome = NotYetAvailable(feature_name="Portfolio", message="not built yet")
        assert outcome.feature_name == "Portfolio"
        assert outcome.message == "not built yet"


class TestPolicyDeclined:
    def test_fields(self) -> None:
        outcome = PolicyDeclined(reason="no_broker_integration", message="I don't execute trades.")
        assert outcome.reason == "no_broker_integration"


def test_tool_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        Tool()  # type: ignore[abstract]


def test_concrete_tool_executes() -> None:
    intent = Intent(type=IntentType.HELP, parameters={}, raw_text="/help")

    outcome = _FakeTool().execute(intent, _session())

    assert isinstance(outcome, HelpContent)
