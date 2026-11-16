"""Tests for investment_committee.conversation.models.

See docs/specs/spec-006-conversational-interface.md S10, S26.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from investment_committee.conversation.models import (
    AudioPayload,
    ChannelType,
    ConversationIdentity,
    ConversationMessage,
    ConversationResponse,
    ConversationSession,
    Intent,
    IntentType,
    UserIdentity,
)
from investment_committee.domain import Asset, AssetType
from investment_committee.memory import DecisionRecordId

AWARE_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
NAIVE_NOW = datetime(2026, 1, 1)


def _identity() -> ConversationIdentity:
    return ConversationIdentity(channel=ChannelType.TELEGRAM, channel_conversation_id="chat-1")


def _user() -> UserIdentity:
    return UserIdentity(user_id="user-1", display_name="Alice")


def _audio() -> AudioPayload:
    return AudioPayload(data=b"raw-bytes", mime_type="audio/ogg", duration_seconds=None)


class TestUserIdentity:
    def test_blank_user_id_raises(self) -> None:
        with pytest.raises(ValueError, match="user_id"):
            UserIdentity(user_id="   ", display_name=None)

    def test_frozen(self) -> None:
        user = _user()
        with pytest.raises(FrozenInstanceError):
            user.user_id = "other"  # type: ignore[misc]


class TestConversationIdentity:
    def test_blank_channel_conversation_id_raises(self) -> None:
        with pytest.raises(ValueError, match="channel_conversation_id"):
            ConversationIdentity(channel=ChannelType.TELEGRAM, channel_conversation_id="  ")

    def test_frozen_and_hashable(self) -> None:
        identity = _identity()
        with pytest.raises(FrozenInstanceError):
            identity.channel_conversation_id = "other"  # type: ignore[misc]
        assert hash(identity) == hash(_identity())


class TestAudioPayload:
    def test_empty_data_raises(self) -> None:
        with pytest.raises(ValueError, match="data"):
            AudioPayload(data=b"", mime_type="audio/ogg", duration_seconds=None)

    def test_blank_mime_type_raises(self) -> None:
        with pytest.raises(ValueError, match="mime_type"):
            AudioPayload(data=b"x", mime_type="  ", duration_seconds=None)


class TestConversationMessage:
    def test_text_only_is_valid(self) -> None:
        message = ConversationMessage(
            conversation_identity=_identity(),
            user_identity=_user(),
            text="Analyze Apple",
            audio=None,
            received_at=AWARE_NOW,
        )
        assert message.text == "Analyze Apple"
        assert message.audio is None

    def test_audio_only_is_valid(self) -> None:
        message = ConversationMessage(
            conversation_identity=_identity(),
            user_identity=_user(),
            text=None,
            audio=_audio(),
            received_at=AWARE_NOW,
        )
        assert message.audio is not None
        assert message.text is None

    def test_both_text_and_audio_raises(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            ConversationMessage(
                conversation_identity=_identity(),
                user_identity=_user(),
                text="Analyze Apple",
                audio=_audio(),
                received_at=AWARE_NOW,
            )

    def test_neither_text_nor_audio_raises(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            ConversationMessage(
                conversation_identity=_identity(),
                user_identity=_user(),
                text=None,
                audio=None,
                received_at=AWARE_NOW,
            )

    def test_blank_text_raises(self) -> None:
        with pytest.raises(ValueError, match="text"):
            ConversationMessage(
                conversation_identity=_identity(),
                user_identity=_user(),
                text="   ",
                audio=None,
                received_at=AWARE_NOW,
            )

    def test_timezone_naive_received_at_raises(self) -> None:
        with pytest.raises(ValueError, match="received_at"):
            ConversationMessage(
                conversation_identity=_identity(),
                user_identity=_user(),
                text="Analyze Apple",
                audio=None,
                received_at=NAIVE_NOW,
            )


class TestConversationResponse:
    def test_blank_text_raises(self) -> None:
        with pytest.raises(ValueError, match="text"):
            ConversationResponse(text="   ", audio=None)

    def test_valid_text_only(self) -> None:
        response = ConversationResponse(text="pong", audio=None)
        assert response.text == "pong"
        assert response.audio is None


class TestIntent:
    def test_blank_raw_text_raises(self) -> None:
        with pytest.raises(ValueError, match="raw_text"):
            Intent(type=IntentType.HELP, parameters={}, raw_text="   ")

    def test_parameters_immutable_after_construction(self) -> None:
        source = {"symbol": "AAPL"}
        intent = Intent(type=IntentType.ANALYZE_ASSET, parameters=source, raw_text="Analyze Apple")

        source["symbol"] = "MSFT"  # mutating the caller's original dict must not affect intent

        assert intent.parameters == {"symbol": "AAPL"}
        with pytest.raises(TypeError):
            intent.parameters["symbol"] = "MSFT"  # type: ignore[index]

    def test_frozen(self) -> None:
        intent = Intent(type=IntentType.HELP, parameters={}, raw_text="/help")
        with pytest.raises(FrozenInstanceError):
            intent.raw_text = "other"  # type: ignore[misc]


class TestConversationSession:
    def test_defaults(self) -> None:
        session = ConversationSession(conversation_identity=_identity())
        assert session.last_asset is None
        assert session.last_record_id is None

    def test_mutable(self) -> None:
        session = ConversationSession(conversation_identity=_identity())
        asset = Asset(ticker="AAPL", name=None, asset_type=AssetType.STOCK, exchange=None, currency=None)

        session.last_asset = asset
        session.last_record_id = DecisionRecordId("record-1")

        assert session.last_asset is asset
        assert session.last_record_id == "record-1"
