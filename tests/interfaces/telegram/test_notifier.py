"""Tests for TelegramNotifier with a fake bot: no network, no token, and
the real bot is never started."""

from __future__ import annotations

from typing import Any

import pytest

from investment_committee.interfaces.telegram.adapter import TELEGRAM_MESSAGE_LIMIT
from investment_committee.interfaces.telegram.notifier import TelegramNotifier


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


async def test_sends_plain_text_to_each_allow_listed_chat_once() -> None:
    bot = FakeBot()

    await TelegramNotifier(bot, [22, 11, 22]).send("hola")

    assert bot.calls == [{"chat_id": 11, "text": "hola"}, {"chat_id": 22, "text": "hola"}]  # no keyboard, no buttons


async def test_long_messages_are_split_to_the_telegram_limit() -> None:
    bot = FakeBot()
    text = "\n".join(["x" * 1000] * 9)

    await TelegramNotifier(bot, [11]).send(text)

    assert len(bot.calls) > 1 and all(len(c["text"]) <= TELEGRAM_MESSAGE_LIMIT for c in bot.calls)
    assert "\n".join(c["text"] for c in bot.calls) == text


def test_without_an_allow_listed_chat_there_is_no_notifier() -> None:
    with pytest.raises(ValueError):
        TelegramNotifier(FakeBot(), [])
