"""Tests for interfaces.telegram.handlers.

Uses fabricated, duck-typed fake Update/Context objects - never a real
Telegram server or SDK network call, matching this codebase's existing
no-live-network-calls-in-tests convention.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from investment_committee.interfaces.telegram.config import BotConfig
from investment_committee.interfaces.telegram.handlers import (
    handle_id,
    handle_ping,
    id_response,
    ping_response,
    unauthorized_response,
)

AUTHORIZED_ID = 111
UNAUTHORIZED_ID = 999


def _fake_update(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )


def _fake_context(config: BotConfig) -> SimpleNamespace:
    return SimpleNamespace(bot_data={"config": config})


@pytest.fixture
def config() -> BotConfig:
    return BotConfig(bot_token="fake-token", allowed_user_ids=frozenset({AUTHORIZED_ID}))


def test_ping_response_text() -> None:
    assert ping_response() == "pong"


def test_id_response_text() -> None:
    assert id_response(AUTHORIZED_ID) == f"Tu ID de usuario de Telegram es {AUTHORIZED_ID}."


async def test_handle_ping_authorized_replies_pong(config: BotConfig) -> None:
    update = _fake_update(AUTHORIZED_ID)
    context = _fake_context(config)

    await handle_ping(update, context)

    update.message.reply_text.assert_awaited_once_with(ping_response())


async def test_handle_ping_unauthorized_is_rejected(config: BotConfig) -> None:
    update = _fake_update(UNAUTHORIZED_ID)
    context = _fake_context(config)

    await handle_ping(update, context)

    update.message.reply_text.assert_awaited_once_with(unauthorized_response())


async def test_handle_id_authorized_replies_with_user_id(config: BotConfig) -> None:
    update = _fake_update(AUTHORIZED_ID)
    context = _fake_context(config)

    await handle_id(update, context)

    update.message.reply_text.assert_awaited_once_with(id_response(AUTHORIZED_ID))


async def test_handle_id_unauthorized_is_rejected(config: BotConfig) -> None:
    update = _fake_update(UNAUTHORIZED_ID)
    context = _fake_context(config)

    await handle_id(update, context)

    update.message.reply_text.assert_awaited_once_with(unauthorized_response())


async def test_handle_id_unauthorized_never_reveals_the_requesting_id(config: BotConfig) -> None:
    update = _fake_update(UNAUTHORIZED_ID)
    context = _fake_context(config)

    await handle_id(update, context)

    (reply_text,), _ = update.message.reply_text.call_args
    assert str(UNAUTHORIZED_ID) not in reply_text


def test_unauthorized_response_text_is_spanish() -> None:
    assert unauthorized_response() == "No tienes autorización para usar este bot."
