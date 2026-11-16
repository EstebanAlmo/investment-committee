"""Tests for investment_committee.domain.recommendations."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_committee.domain.assets import Asset, AssetType
from investment_committee.domain.recommendations import (
    Recommendation,
    RecommendationAction,
)

UTC_NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


def make_asset() -> Asset:
    return Asset(
        ticker="AAPL",
        name="Apple Inc.",
        asset_type=AssetType.STOCK,
        exchange="NASDAQ",
        currency="USD",
    )


def make_recommendation(confidence: str = "0.5", summary: str = "Solid fundamentals.") -> Recommendation:
    return Recommendation(
        asset=make_asset(),
        created_at=UTC_NOW,
        action=RecommendationAction.BUY,
        confidence=Decimal(confidence),
        summary=summary,
        bull_case="Growing revenue.",
        bear_case="Rich valuation.",
        risk_notes="Sensitive to rate changes.",
    )


def test_confidence_below_zero_is_rejected() -> None:
    with pytest.raises(ValueError):
        make_recommendation(confidence="-0.01")


def test_confidence_above_one_is_rejected() -> None:
    with pytest.raises(ValueError):
        make_recommendation(confidence="1.01")


def test_confidence_boundary_values_are_accepted() -> None:
    assert make_recommendation(confidence="0").confidence == Decimal("0")
    assert make_recommendation(confidence="1").confidence == Decimal("1")


def test_blank_summary_is_rejected() -> None:
    with pytest.raises(ValueError):
        make_recommendation(summary="   ")


def test_timezone_naive_created_at_is_rejected() -> None:
    with pytest.raises(ValueError):
        Recommendation(
            asset=make_asset(),
            created_at=datetime(2026, 7, 21, 12, 0),
            action=RecommendationAction.HOLD,
            confidence=Decimal("0.5"),
            summary="Neutral outlook.",
            bull_case=None,
            bear_case=None,
            risk_notes=None,
        )


def test_recommendation_action_enum_values() -> None:
    assert {member.value for member in RecommendationAction} == {"BUY", "HOLD", "SELL"}
