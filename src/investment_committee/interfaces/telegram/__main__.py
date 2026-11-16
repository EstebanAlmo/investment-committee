"""Entry point: python -m investment_committee.interfaces.telegram

Loads .env, validates configuration, builds the deterministic
ConversationService, and starts the bot on long polling. Run it from the
repository root: the Decision Ledger is written to data/ledger/decisions.jsonl
relative to the working directory.
"""

from __future__ import annotations

import logging
import os
import sys

from dotenv import find_dotenv, load_dotenv

from investment_committee.conversation import build_default_conversation_service

from .bot import run_polling
from .config import load_config
from .errors import ConfigurationError
from .logging_config import configure_logging

_logger = logging.getLogger(__name__)


def main() -> int:
    configure_logging()

    # Look for .env in the working directory only (the repository root), the
    # same directory the ledger path is relative to - not by walking up from
    # this file's location, which would silently pick up the repo's .env
    # from any working directory.
    load_dotenv(find_dotenv(usecwd=True))
    try:
        config = load_config(os.environ)
    except ConfigurationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    conversation_service = build_default_conversation_service(
        allowed_user_ids=frozenset(str(user_id) for user_id in config.allowed_user_ids),
    )
    _logger.info("Starting Investment Committee bot for %d allowed user(s)", len(config.allowed_user_ids))
    run_polling(config, conversation_service)
    return 0


if __name__ == "__main__":
    sys.exit(main())
