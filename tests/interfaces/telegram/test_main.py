"""Tests for the bot entry point (python -m investment_committee.interfaces.telegram).

run_polling and load_dotenv are replaced, so nothing contacts Telegram and
the developer's real .env is never read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import investment_committee.interfaces.telegram.__main__ as entry_point
from investment_committee.conversation import ConversationService
from investment_committee.interfaces.telegram.config import BotConfig

FAKE_TOKEN = "123456:FAKE-ENTRYPOINT-TOKEN-not-real"


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(entry_point, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(entry_point, "configure_logging", lambda: None)


def test_main_builds_conversation_service_and_starts_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[tuple[BotConfig, object]] = []
    monkeypatch.setattr(entry_point, "run_polling", lambda config, service: started.append((config, service)))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "42")

    assert entry_point.main() == 0

    ((config, service),) = started
    assert config.allowed_user_ids == frozenset({42})
    assert isinstance(service, ConversationService)


def test_main_fails_fast_without_leaking_config(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(entry_point, "run_polling", lambda config, service: pytest.fail("must not start"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)

    assert entry_point.main() == 1
    assert FAKE_TOKEN not in capsys.readouterr().err
