"""Logging setup for the Telegram connectivity bootstrap (Phase 0).

python-telegram-bot's HTTP layer (httpx) logs each request's full URL at
INFO level - and the Telegram Bot API embeds the bot token directly in the
URL path (https://api.telegram.org/bot<TOKEN>/<method>). Left at its
default level, httpx's own logger would print the token in plaintext on
every request. `configure_logging` raises that specific logger's level so
this cannot happen, while leaving this project's own logging at INFO.
"""

from __future__ import annotations

import logging

_NOISY_LOGGERS_WITH_TOKEN_IN_URL = ("httpx", "httpcore")


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for logger_name in _NOISY_LOGGERS_WITH_TOKEN_IN_URL:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
