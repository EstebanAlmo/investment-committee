"""Command handlers for the Telegram connectivity bootstrap (Phase 0).

Response text is built by plain functions (`ping_response`, `id_response`,
`unauthorized_response`) with no dependency on python-telegram-bot, so the
response content itself is testable without any fake Update/Context
objects. The `async def` handlers below are thin adapters that read the
requesting user id off `update`, apply the allowlist, and reply - no other
logic lives in them.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from .allowlist import is_authorized
from .config import BotConfig

_logger = logging.getLogger(__name__)


def ping_response() -> str:
    return "pong"


def id_response(user_id: int) -> str:
    return f"Tu ID de usuario de Telegram es {user_id}."


def unauthorized_response() -> str:
    return "No tienes autorización para usar este bot."


async def handle_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: BotConfig = context.bot_data["config"]
    user_id = update.effective_user.id
    if not is_authorized(user_id, config.allowed_user_ids):
        _logger.info("Rejected /ping from unauthorized user")
        await update.message.reply_text(unauthorized_response())
        return
    await update.message.reply_text(ping_response())


async def handle_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: BotConfig = context.bot_data["config"]
    user_id = update.effective_user.id
    if not is_authorized(user_id, config.allowed_user_ids):
        _logger.info("Rejected /id from unauthorized user")
        await update.message.reply_text(unauthorized_response())
        return
    await update.message.reply_text(id_response(user_id))
