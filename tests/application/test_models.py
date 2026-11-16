"""Tests for investment_committee.application.models.AnalysisResult."""

from __future__ import annotations

import dataclasses

import pytest

from investment_committee.application.models import AnalysisResult

from ._support import make_asset, make_decision, make_indicator_snapshot, make_market_snapshot


def test_valid_construction_with_matching_assets() -> None:
    asset = make_asset("MSFT")
    market_snapshot = make_market_snapshot(asset)
    indicator_snapshot = make_indicator_snapshot(market_snapshot)
    decision = make_decision(asset)

    result = AnalysisResult(
        market_snapshot=market_snapshot,
        indicator_snapshot=indicator_snapshot,
        decision=decision,
    )

    assert result.market_snapshot is market_snapshot
    assert result.indicator_snapshot is indicator_snapshot
    assert result.decision is decision


def test_asset_property_returns_decision_asset() -> None:
    asset = make_asset("MSFT")
    market_snapshot = make_market_snapshot(asset)
    indicator_snapshot = make_indicator_snapshot(market_snapshot)
    decision = make_decision(asset)

    result = AnalysisResult(
        market_snapshot=market_snapshot,
        indicator_snapshot=indicator_snapshot,
        decision=decision,
    )

    assert result.asset == asset
    assert result.asset is decision.asset


def test_asset_is_a_computed_property_not_a_stored_field() -> None:
    field_names = {field.name for field in dataclasses.fields(AnalysisResult)}
    assert field_names == {"market_snapshot", "indicator_snapshot", "decision"}
    assert "asset" not in field_names
    assert isinstance(AnalysisResult.asset, property)


def test_mismatched_decision_asset_rejected() -> None:
    market_snapshot = make_market_snapshot(make_asset("MSFT"))
    indicator_snapshot = make_indicator_snapshot(market_snapshot)
    decision = make_decision(make_asset("AAPL"))

    with pytest.raises(ValueError):
        AnalysisResult(
            market_snapshot=market_snapshot,
            indicator_snapshot=indicator_snapshot,
            decision=decision,
        )


def test_mismatched_indicator_snapshot_asset_rejected() -> None:
    asset = make_asset("MSFT")
    market_snapshot = make_market_snapshot(asset)
    other_market_snapshot = make_market_snapshot(make_asset("AAPL"))
    indicator_snapshot = make_indicator_snapshot(other_market_snapshot)
    decision = make_decision(asset)

    with pytest.raises(ValueError):
        AnalysisResult(
            market_snapshot=market_snapshot,
            indicator_snapshot=indicator_snapshot,
            decision=decision,
        )


def test_is_frozen() -> None:
    asset = make_asset("MSFT")
    market_snapshot = make_market_snapshot(asset)
    indicator_snapshot = make_indicator_snapshot(market_snapshot)
    decision = make_decision(asset)
    result = AnalysisResult(
        market_snapshot=market_snapshot,
        indicator_snapshot=indicator_snapshot,
        decision=decision,
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.decision = decision  # type: ignore[misc]
