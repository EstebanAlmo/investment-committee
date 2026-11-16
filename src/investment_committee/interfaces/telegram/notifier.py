"""TelegramNotifier: delivers already-composed notification texts.

Owns no business logic and reads no database (the outbox lives in
application.notifications). Messages are plain text, split to Telegram's
size limit, sent only to the configured allow-listed chat ids. No button,
keyboard or command that could execute anything is ever attached. Nothing
here runs unless scripts/send_notifications.py is invoked with --enviar.
"""

from __future__ import annotations

from typing import Any, Iterable, Protocol

from .adapter import _split_for_telegram


class MessageBot(Protocol):
    async def send_message(self, chat_id: int, text: str) -> Any: ...


class TelegramNotifier:
    def __init__(self, bot: MessageBot, chat_ids: Iterable[int]) -> None:
        self._bot = bot
        self._chat_ids = tuple(sorted(set(chat_ids)))
        if not self._chat_ids:
            raise ValueError("no allow-listed chat to notify")

    async def send(self, text: str) -> None:
        for chat_id in self._chat_ids:
            for chunk in _split_for_telegram(text):
                await self._bot.send_message(chat_id=chat_id, text=chunk)  # plain text: no reply_markup, no buttons
