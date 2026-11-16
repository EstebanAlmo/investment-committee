"""Tests for investment_committee.conversation.adapters.base.

See docs/specs/spec-006-conversational-interface.md S21, S26.
ConversationService (conversation/service.py) is scoped to a later phase
and does not exist yet - `conversation_service` here is a duck-typed fake,
which the postponed-evaluation-of-annotations convention (PEP 563) this
codebase already uses makes safe (see adapters/base.py's module docstring).
"""

from __future__ import annotations

import pytest

from investment_committee.conversation.adapters.base import ConversationAdapter
from investment_committee.conversation.models import ChannelType, ConversationIdentity, ConversationResponse


class _FakeConversationService:
    pass


class _FakeAdapter(ConversationAdapter):
    channel = ChannelType.TELEGRAM

    def __init__(self, conversation_service: object) -> None:
        super().__init__(conversation_service)  # type: ignore[arg-type]
        self.sent: list[tuple[ConversationResponse, ConversationIdentity]] = []

    def send(self, response: ConversationResponse, conversation_identity: ConversationIdentity) -> None:
        self.sent.append((response, conversation_identity))


def test_conversation_adapter_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        ConversationAdapter(_FakeConversationService())  # type: ignore[abstract]


def test_concrete_adapter_stores_the_conversation_service() -> None:
    service = _FakeConversationService()

    adapter = _FakeAdapter(service)

    assert adapter._conversation_service is service


def test_concrete_adapter_send_is_called_with_response_and_identity() -> None:
    adapter = _FakeAdapter(_FakeConversationService())
    response = ConversationResponse(text="pong", audio=None)
    identity = ConversationIdentity(channel=ChannelType.TELEGRAM, channel_conversation_id="chat-1")

    adapter.send(response, identity)

    assert adapter.sent == [(response, identity)]
