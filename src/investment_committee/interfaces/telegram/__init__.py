"""Telegram channel adapter for the Investment Committee bot.

Run with: python -m investment_committee.interfaces.telegram

Depends on conversation/ only (spec-006 S8) - never on application/ or any
lower layer. /ping and /id remain as Phase 0 diagnostics.
"""

from __future__ import annotations
