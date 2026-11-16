"""Allowlist check for the Telegram connectivity bootstrap (Phase 0).

A small, pure function kept separate from `handlers.py` so it is testable
with no Telegram objects at all - mirrors this codebase's convention of
keeping decision logic free of I/O concerns (e.g. IndicatorEngine).
"""

from __future__ import annotations


def is_authorized(user_id: int, allowed_user_ids: frozenset[int]) -> bool:
    """Never raises. An unrecognized id is simply unauthorized."""
    return user_id in allowed_user_ids
