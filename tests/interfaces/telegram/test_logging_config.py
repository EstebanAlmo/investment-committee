"""Tests for interfaces.telegram.logging_config.

httpx logs each request's full URL at INFO, and the Telegram Bot API
embeds the bot token in that URL - so httpx (and httpcore, which httpx
itself delegates to) must never be left at INFO or below.
"""

from __future__ import annotations

import logging

from investment_committee.interfaces.telegram.logging_config import configure_logging


def test_configure_logging_silences_loggers_that_would_leak_the_token_in_a_url() -> None:
    logging.getLogger("httpx").setLevel(logging.INFO)
    logging.getLogger("httpcore").setLevel(logging.INFO)

    configure_logging()

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
