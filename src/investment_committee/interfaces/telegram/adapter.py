"""TelegramConversationAdapter: Telegram Update <-> ConversationService.

See docs/specs/spec-006-conversational-interface.md S21. Owns no business
logic: every inbound update becomes exactly one
ConversationService.handle_message() call, and the reply is delivered
unmodified (split only to respect Telegram's message-size limit).

The adapter owns the async boundary (S21): handle_message is synchronous
and may do network I/O (market data), so it runs in a worker thread via
asyncio.to_thread rather than blocking the polling event loop. `send` is
therefore a coroutine here, overriding the port's synchronous signature.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

from investment_committee.conversation import (
    AudioPayload,
    ChannelType,
    ConversationAdapter,
    ConversationIdentity,
    ConversationMessage,
    ConversationResponse,
    UserIdentity,
)

_logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096


class BotClient(Protocol):
    """The two Bot API calls this adapter makes - telegram.Bot satisfies it."""

    async def send_message(self, chat_id: int, text: str) -> Any: ...

    async def send_voice(self, chat_id: int, voice: bytes) -> Any: ...


class ConversationHandler(Protocol):
    def handle_message(self, message: ConversationMessage) -> ConversationResponse: ...


class TelegramConversationAdapter(ConversationAdapter):
    channel = ChannelType.TELEGRAM

    def __init__(self, conversation_service: ConversationHandler, bot: BotClient) -> None:
        super().__init__(conversation_service)  # type: ignore[arg-type]
        self._bot = bot

    async def on_update(self, update: Any, context: Any) -> None:
        """python-telegram-bot handler callback for text and voice messages."""
        message = await self._to_conversation_message(update)
        if message is None:
            return
        response = await asyncio.to_thread(self._conversation_service.handle_message, message)
        await self.send(response, message.conversation_identity)

    async def send(  # type: ignore[override]
        self, response: ConversationResponse, conversation_identity: ConversationIdentity
    ) -> None:
        chat_id = int(conversation_identity.channel_conversation_id)
        for chunk in _split_for_telegram(response.text):
            await self._bot.send_message(chat_id=chat_id, text=chunk)
        if response.audio is not None:
            await self._bot.send_voice(chat_id=chat_id, voice=response.audio.data)

    async def _to_conversation_message(self, update: Any) -> ConversationMessage | None:
        telegram_message = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        if telegram_message is None or user is None or chat is None:
            return None

        conversation_identity = ConversationIdentity(channel=ChannelType.TELEGRAM, channel_conversation_id=str(chat.id))
        user_identity = UserIdentity(user_id=str(user.id), display_name=getattr(user, "full_name", None))
        received_at = telegram_message.date or datetime.now(timezone.utc)

        if telegram_message.voice is not None:
            telegram_file = await telegram_message.voice.get_file()
            data = bytes(await telegram_file.download_as_bytearray())
            if not data:
                return None
            audio = AudioPayload(
                data=data,
                mime_type="audio/ogg",
                duration_seconds=Decimal(telegram_message.voice.duration),
            )
            return ConversationMessage(conversation_identity, user_identity, None, audio, received_at)

        text = telegram_message.text
        if text is None or not text.strip():
            return None
        return ConversationMessage(conversation_identity, user_identity, _strip_bot_mention(text), None, received_at)


def _strip_bot_mention(text: str) -> str:
    """Telegram appends "@BotName" to commands in group chats
    ("/history@MyBot AAPL"); CommandParser expects the bare command."""
    if not text.startswith("/"):
        return text
    command, _, rest = text.partition(" ")
    command = command.split("@", 1)[0]
    return f"{command} {rest}" if rest else command


def _split_for_telegram(text: str) -> list[str]:
    """Split on line boundaries into chunks under TELEGRAM_MESSAGE_LIMIT,
    hard-splitting any single line that is itself too long."""
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > TELEGRAM_MESSAGE_LIMIT:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:TELEGRAM_MESSAGE_LIMIT])
            line = line[TELEGRAM_MESSAGE_LIMIT:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > TELEGRAM_MESSAGE_LIMIT:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
