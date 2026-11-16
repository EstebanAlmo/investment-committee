"""Errors for the Telegram connectivity bootstrap (Phase 0).

See docs/plans/epic-8-conversational-interface-implementation-plan.md,
"Phase 0". This is a small, standalone hierarchy - it predates
conversation/errors.py and is not related to it.
"""

from __future__ import annotations


class TelegramBootstrapError(Exception):
    """Base class for every error the Phase 0 bootstrap bot can raise."""


class ConfigurationError(TelegramBootstrapError):
    """Raised when required configuration is missing or malformed.

    Never includes the value that failed validation - only which variable
    and why - so a raised ConfigurationError is always safe to log or print
    in full without risking a leaked secret.
    """
