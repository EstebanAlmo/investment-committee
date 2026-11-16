"""Shared validation helpers for memory entities."""

from __future__ import annotations

from datetime import datetime


def require_timezone_aware(value: datetime, field_name: str) -> None:
    """Raise ValueError if `value` is not a timezone-aware datetime."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field_name} must be timezone-aware")
