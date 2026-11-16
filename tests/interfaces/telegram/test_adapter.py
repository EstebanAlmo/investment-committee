"""Tests for interfaces.telegram.adapter.TelegramConversationAdapter.

Against a fake Bot API client and duck-typed Update objects - never a real
Telegram server (spec-006 S21, S26).
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from investment_committee.conversation import (
    ChannelType,
    ConversationIdentity,
    ConversationMessage,
    ConversationResponse,
)
from investment_committee.interfaces.telegram.adapter import TELEGRAM_MESSAGE_LIMIT, TelegramConversationAdapter

_SENT_AT = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []
        self.voices: list[tuple[int, bytes]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))

    async def send_voice(self, chat_id: int, voice: bytes) -> None:
        self.voices.append((chat_id, voice))


class RecordingService:
    def __init__(self, reply: str = "ok") -> None:
        self.messages: list[ConversationMessage] = []
        self._reply = reply

    def handle_message(self, message: ConversationMessage) -> ConversationResponse:
        self.messages.append(message)
        return ConversationResponse(text=self._reply, audio=None)


class FakeFile:
    async def download_as_bytearray(self) -> bytearray:
        return bytearray(b"OggS-voice")


class FakeVoice:
    duration = 3

    async def get_file(self) -> FakeFile:
        return FakeFile()


def make_update(text: str | None = None, voice: Any = None, user_id: int = 42, chat_id: int = 777) -> SimpleNamespace:
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, full_name="Owner"),
        effective_chat=SimpleNamespace(id=chat_id),
        effective_message=SimpleNamespace(text=text, voice=voice, date=_SENT_AT),
    )


async def test_text_update_builds_text_message_and_replies_to_chat() -> None:
    service, bot = RecordingService("hello back"), FakeBot()

    await TelegramConversationAdapter(service, bot).on_update(make_update("/analyze AAPL"), None)

    (message,) = service.messages
    assert message.text == "/analyze AAPL"
    assert message.audio is None
    assert message.user_identity.user_id == "42"
    assert message.conversation_identity == ConversationIdentity(ChannelType.TELEGRAM, "777")
    assert message.received_at == _SENT_AT
    assert bot.messages == [(777, "hello back")]


async def test_voice_update_downloads_audio() -> None:
    service, bot = RecordingService(), FakeBot()

    await TelegramConversationAdapter(service, bot).on_update(make_update(voice=FakeVoice()), None)

    (message,) = service.messages
    assert message.text is None
    assert message.audio is not None
    assert message.audio.data == b"OggS-voice"
    assert message.audio.mime_type == "audio/ogg"


async def test_bot_username_suffix_is_stripped_from_commands() -> None:
    service = RecordingService()

    await TelegramConversationAdapter(service, FakeBot()).on_update(make_update("/history@MyCommitteeBot AAPL"), None)

    assert service.messages[0].text == "/history AAPL"


async def test_updates_without_text_or_voice_are_ignored() -> None:
    service, bot = RecordingService(), FakeBot()

    await TelegramConversationAdapter(service, bot).on_update(make_update(text="   "), None)
    await TelegramConversationAdapter(service, bot).on_update(
        SimpleNamespace(effective_user=None, effective_chat=None, effective_message=None), None
    )

    assert service.messages == []
    assert bot.messages == []


async def test_long_replies_are_split_under_telegram_limit() -> None:
    long_text = "\n".join(f"line {i} " + "x" * 80 for i in range(120))
    bot = FakeBot()

    await TelegramConversationAdapter(RecordingService(long_text), bot).on_update(make_update("/history"), None)

    assert len(bot.messages) > 1
    assert all(len(text) <= TELEGRAM_MESSAGE_LIMIT for _, text in bot.messages)
    assert "\n".join(text for _, text in bot.messages) == long_text


async def test_send_delivers_voice_when_response_has_audio() -> None:
    from decimal import Decimal

    from investment_committee.conversation import AudioPayload

    bot = FakeBot()
    response = ConversationResponse(
        text="spoken", audio=AudioPayload(data=b"ogg", mime_type="audio/ogg", duration_seconds=Decimal("1"))
    )

    await TelegramConversationAdapter(RecordingService(), bot).send(response, ConversationIdentity(ChannelType.TELEGRAM, "5"))

    assert bot.messages == [(5, "spoken")]
    assert bot.voices == [(5, b"ogg")]
