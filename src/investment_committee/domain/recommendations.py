"""Recommendation: the committee's output for an Asset at a point in time."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from ._validation import require_timezone_aware
from .assets import Asset


class RecommendationAction(str, Enum):
    """The action a Recommendation advises."""

    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"


@dataclass(frozen=True)
class Recommendation:
    """A committee recommendation for an asset, with supporting rationale."""

    asset: Asset
    created_at: datetime
    action: RecommendationAction
    confidence: Decimal
    summary: str
    bull_case: str | None
    bear_case: str | None
    risk_notes: str | None

    def __post_init__(self) -> None:
        require_timezone_aware(self.created_at, "created_at")
        if not self.summary.strip():
            raise ValueError("summary must not be blank")
        if not (Decimal("0") <= self.confidence <= Decimal("1")):
            raise ValueError("confidence must be between 0 and 1")
