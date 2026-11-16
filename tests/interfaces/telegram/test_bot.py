"""Tests for interfaces.telegram.bot.build_application.

Building an Application does not contact Telegram's servers - only
run_polling()/initialize() do, and neither is called here.
"""

from __future__ import annotations

from telegram.ext import CommandHandler, MessageHandler

from investment_committee.conversation import ConversationMessage, ConversationResponse
from investment_committee.interfaces.telegram.config import BotConfig

FAKE_TOKEN = "123456:FAKE-BOOTSTRAP-TOKEN-not-real"


class _UnusedService:
    def handle_message(self, message: ConversationMessage) -> ConversationResponse:
        raise AssertionError("not called while building the application")


def _build() -> tuple[object, BotConfig]:
    from investment_committee.interfaces.telegram.bot import build_application

    config = BotConfig(bot_token=FAKE_TOKEN, allowed_user_ids=frozenset({111}))
    return build_application(config, _UnusedService()), config


def test_build_application_registers_ping_and_id_handlers() -> None:
    application, _ = _build()

    registered_commands: set[str] = set()
    for handlers in application.handlers.values():  # type: ignore[attr-defined]
        for handler in handlers:
            if isinstance(handler, CommandHandler):
                registered_commands.update(handler.commands)

    assert {"ping", "id"} <= registered_commands


def test_conversation_handler_is_registered_after_diagnostic_commands() -> None:
    application, _ = _build()

    handlers = application.handlers[0]  # type: ignore[attr-defined]
    kinds = [type(handler) for handler in handlers]

    assert kinds[-1] is MessageHandler
    assert kinds.index(MessageHandler) > max(i for i, kind in enumerate(kinds) if kind is CommandHandler)


def test_build_application_stores_config_in_bot_data() -> None:
    application, config = _build()

    assert application.bot_data["config"] is config  # type: ignore[attr-defined]


def test_bot_command_menu_lists_mvp_commands() -> None:
    from investment_committee.interfaces.telegram.bot import BOT_COMMANDS

    assert {command for command, _ in BOT_COMMANDS} >= {"analyze", "history", "help"}


def test_bot_command_menu_keeps_english_commands_with_spanish_descriptions() -> None:
    from investment_committee.interfaces.telegram.bot import BOT_COMMANDS

    descriptions = dict(BOT_COMMANDS)
    assert set(descriptions) == {
        "analyze", "history", "explain", "compare", "help",
        "trade", "explicar_trade", "operaciones", "backtest", "aprender", "committee", "oportunidades",
        "simular_mes", "seguimiento", "portafolio", "alertas",
    }
    assert all(command.islower() and command.replace("_", "").isalnum() for command in descriptions)  # Bot API rule
    assert descriptions["trade"].startswith("Trading simulado")
    assert descriptions["analyze"].startswith("Analizar")
    assert "EUR/USD" in descriptions["analyze"]
    assert descriptions["history"].startswith("Decisiones registradas")
    assert descriptions["explain"].startswith("Por qué")
    assert descriptions["compare"].startswith("Analizar dos activos")
    assert descriptions["help"].startswith("Qué puede hacer")
