"""Tests for interfaces.telegram.config.load_config.

All credentials here are fabricated - never real Telegram tokens or ids.
"""

from __future__ import annotations

import pytest

from investment_committee.interfaces.telegram.config import BotConfig, load_config
from investment_committee.interfaces.telegram.errors import ConfigurationError

FAKE_TOKEN = "123456:FAKE-BOOTSTRAP-TOKEN-not-real"


def test_load_config_valid() -> None:
    config = load_config(
        {
            "TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
            "TELEGRAM_ALLOWED_USER_IDS": "111, 222,333",
        }
    )

    assert config == BotConfig(bot_token=FAKE_TOKEN, allowed_user_ids=frozenset({111, 222, 333}))


def test_load_config_single_allowed_id() -> None:
    config = load_config({"TELEGRAM_BOT_TOKEN": FAKE_TOKEN, "TELEGRAM_ALLOWED_USER_IDS": "999"})

    assert config.allowed_user_ids == frozenset({999})


def test_load_config_missing_token_raises() -> None:
    with pytest.raises(ConfigurationError, match="TELEGRAM_BOT_TOKEN"):
        load_config({"TELEGRAM_ALLOWED_USER_IDS": "111"})


def test_load_config_blank_token_raises() -> None:
    with pytest.raises(ConfigurationError, match="TELEGRAM_BOT_TOKEN"):
        load_config({"TELEGRAM_BOT_TOKEN": "   ", "TELEGRAM_ALLOWED_USER_IDS": "111"})


def test_load_config_missing_allowlist_raises() -> None:
    with pytest.raises(ConfigurationError, match="TELEGRAM_ALLOWED_USER_IDS"):
        load_config({"TELEGRAM_BOT_TOKEN": FAKE_TOKEN})


def test_load_config_blank_allowlist_raises() -> None:
    with pytest.raises(ConfigurationError, match="TELEGRAM_ALLOWED_USER_IDS"):
        load_config({"TELEGRAM_BOT_TOKEN": FAKE_TOKEN, "TELEGRAM_ALLOWED_USER_IDS": "   "})


def test_load_config_non_integer_allowlist_entry_raises() -> None:
    with pytest.raises(ConfigurationError, match="non-integer"):
        load_config({"TELEGRAM_BOT_TOKEN": FAKE_TOKEN, "TELEGRAM_ALLOWED_USER_IDS": "111,not-a-number"})


def test_configuration_error_never_contains_the_token() -> None:
    try:
        load_config({"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_ALLOWED_USER_IDS": "111"})
    except ConfigurationError as exc:
        assert FAKE_TOKEN not in str(exc)
