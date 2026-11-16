"""Telegram Application wiring.

Builds a python-telegram-bot `Application` for long polling. `/ping` and
`/id` (the Phase 0 diagnostics) are registered first; every other text or
voice message goes to TelegramConversationAdapter -> ConversationService.
"""

from __future__ import annotations

from telegram import BotCommand
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters

from .adapter import ConversationHandler, TelegramConversationAdapter
from .config import BotConfig
from .handlers import handle_id, handle_ping

# Command names stay in English for compatibility; descriptions are es-CO.
BOT_COMMANDS: tuple[tuple[str, str], ...] = (
    ("analyze", "Analizar una acción, ETF o par Forex - /analyze AAPL o /analyze EUR/USD"),
    ("history", "Decisiones registradas - /history AAPL"),
    ("explain", "Por qué salió así la última decisión - /explain AAPL"),
    ("compare", "Analizar dos activos - /compare MSFT AAPL"),
    ("trade", "Trading simulado - /trade EUR/USD"),
    ("explicar_trade", "Explicación paso a paso de la última propuesta"),
    ("operaciones", "Operaciones simuladas y sus resultados"),
    ("backtest", "Resultados históricos simulados - /backtest EUR/USD"),
    ("aprender", "Aprender un concepto - /aprender rsi"),
    ("committee", "Comité de IA - /committee EUR/USD"),
    ("oportunidades", "Buscar candidatas de day trading (exploratorio, simulado)"),
    ("simular_mes", "Simulación histórica del mes pasado (políticas A, B y C)"),
    ("seguimiento", "Resultado hipotético de las candidatas emitidas"),
    ("portafolio", "Posiciones vigiladas, valoración y riesgos"),
    ("alertas", "Comprobar alertas del portafolio"),
    ("help", "Qué puede hacer este bot"),
)


async def _publish_command_menu(application: Application) -> None:
    await application.bot.set_my_commands([BotCommand(command, description) for command, description in BOT_COMMANDS])


def build_application(config: BotConfig, conversation_service: ConversationHandler) -> Application:
    """Construct a configured Application. Does not start polling or contact Telegram."""
    application: Application = ApplicationBuilder().token(config.bot_token).post_init(_publish_command_menu).build()
    application.bot_data["config"] = config
    application.add_handler(CommandHandler("ping", handle_ping))
    application.add_handler(CommandHandler("id", handle_id))

    adapter = TelegramConversationAdapter(conversation_service, application.bot)
    application.add_handler(MessageHandler(filters.TEXT | filters.VOICE, adapter.on_update))
    return application


def run_polling(config: BotConfig, conversation_service: ConversationHandler) -> None:
    """Build the Application and start long polling. Blocks until interrupted."""
    build_application(config, conversation_service).run_polling()
