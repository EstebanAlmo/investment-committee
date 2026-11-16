"""Configuration loading for the Telegram connectivity bootstrap (Phase 0).

See docs/plans/epic-8-conversational-interface-implementation-plan.md,
"Phase 0". `load_config` reads from an explicit `Mapping[str, str]` rather
than `os.environ` directly, so tests can supply fabricated credentials
without touching real environment state or a real .env file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .errors import ConfigurationError

TELEGRAM_BOT_TOKEN = "TELEGRAM_BOT_TOKEN"
TELEGRAM_ALLOWED_USER_IDS = "TELEGRAM_ALLOWED_USER_IDS"


@dataclass(frozen=True)
class BotConfig:
    """Validated Phase 0 bootstrap configuration.

    `bot_token` is never logged or included in any error message - see
    ConfigurationError's docstring.
    """

    bot_token: str
    allowed_user_ids: frozenset[int]


def load_config(env: Mapping[str, str]) -> BotConfig:
    """Build a validated BotConfig from `env`.

    Raises ConfigurationError if TELEGRAM_BOT_TOKEN is missing/blank, or if
    TELEGRAM_ALLOWED_USER_IDS is missing, blank, or contains anything that
    is not a comma-separated list of integer Telegram user ids. There is no
    safe default allowlist: an unset or malformed value always fails fast
    rather than falling back to "allow everyone" or "allow no one" as a
    silent default.
    """
    bot_token = env.get(TELEGRAM_BOT_TOKEN, "").strip()
    if not bot_token:
        raise ConfigurationError(f"{TELEGRAM_BOT_TOKEN} is not set")

    raw_ids = env.get(TELEGRAM_ALLOWED_USER_IDS, "").strip()
    if not raw_ids:
        raise ConfigurationError(f"{TELEGRAM_ALLOWED_USER_IDS} is not set")

    allowed_user_ids: set[int] = set()
    for token in raw_ids.split(","):
        candidate = token.strip()
        if not candidate:
            continue
        if not _is_int(candidate):
            raise ConfigurationError(
                f"{TELEGRAM_ALLOWED_USER_IDS} contains a non-integer value: {candidate!r}"
            )
        allowed_user_ids.add(int(candidate))

    if not allowed_user_ids:
        raise ConfigurationError(f"{TELEGRAM_ALLOWED_USER_IDS} must contain at least one user id")

    return BotConfig(bot_token=bot_token, allowed_user_ids=frozenset(allowed_user_ids))


def _is_int(candidate: str) -> bool:
    if candidate.startswith(("+", "-")):
        candidate = candidate[1:]
    return candidate.isdigit()
