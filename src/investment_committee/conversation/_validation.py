"""Shared validation helpers for conversation entities.

Duplicated from application/_validation.py, memory/_validation.py,
committee/_validation.py, and domain/_validation.py rather than imported -
see docs/specs/spec-006-conversational-interface.md S23 ("D4 precedent"):
each layer keeps its own tiny copy rather than depending on another layer
just for this one helper.
"""

from __future__ import annotations

from datetime import datetime


def require_timezone_aware(value: datetime, field_name: str) -> None:
    """Raise ValueError if `value` is not a timezone-aware datetime."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field_name} must be timezone-aware")
