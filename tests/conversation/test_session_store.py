"""Tests for investment_committee.conversation.session_store.

See docs/specs/spec-006-conversational-interface.md S20, S26.
"""

from __future__ import annotations

import pytest

from investment_committee.conversation.models import ChannelType, ConversationIdentity, ConversationSession
from investment_committee.conversation.session_store import InMemorySessionStore, SessionStore
from investment_committee.domain import Asset, AssetType


def _identity(chat_id: str = "chat-1") -> ConversationIdentity:
    return ConversationIdentity(channel=ChannelType.TELEGRAM, channel_conversation_id=chat_id)


class TestInMemorySessionStore:
    def test_get_on_unseen_identity_returns_a_fresh_empty_session(self) -> None:
        store = InMemorySessionStore()

        session = store.get(_identity())

        assert session == ConversationSession(conversation_identity=_identity())
        assert session.last_asset is None
        assert session.last_record_id is None

    def test_get_never_raises_and_does_not_persist_the_fresh_session(self) -> None:
        store = InMemorySessionStore()

        first = store.get(_identity())
        first.last_asset = Asset(ticker="AAPL", name=None, asset_type=AssetType.STOCK, exchange=None, currency=None)

        second = store.get(_identity())

        assert second.last_asset is None  # mutating the returned session alone must not persist it

    def test_save_then_get_round_trips(self) -> None:
        store = InMemorySessionStore()
        session = ConversationSession(conversation_identity=_identity())
        session.last_asset = Asset(ticker="MSFT", name=None, asset_type=AssetType.STOCK, exchange=None, currency=None)

        store.save(session)
        retrieved = store.get(_identity())

        assert retrieved is session
        assert retrieved.last_asset is not None
        assert retrieved.last_asset.ticker == "MSFT"

    def test_sessions_for_different_identities_are_independent(self) -> None:
        store = InMemorySessionStore()
        store.save(ConversationSession(conversation_identity=_identity("chat-1")))

        other = store.get(_identity("chat-2"))

        assert other.conversation_identity == _identity("chat-2")


def test_session_store_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        SessionStore()  # type: ignore[abstract]
