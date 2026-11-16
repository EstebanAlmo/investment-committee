"""Tests for investment_committee.memory.models: DecisionRecord, StrategyMetadata."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_committee.domain import MarketSnapshot, PriceBar
from investment_committee.memory.models import DecisionRecord, DecisionRecordId, StrategyMetadata

from ._support import (
    UTC_NOW,
    make_asset,
    make_decision,
    make_decision_record,
    make_indicator_snapshot,
    make_market_snapshot,
    make_strategy_metadata,
)


# --- StrategyMetadata ---------------------------------------------------


def test_strategy_metadata_valid_construction() -> None:
    metadata = make_strategy_metadata()
    assert metadata.strategy_name == "deterministic_committee"


def test_strategy_metadata_is_frozen() -> None:
    metadata = make_strategy_metadata()
    with pytest.raises(dataclasses.FrozenInstanceError):
        metadata.strategy_name = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "field_name",
    ["strategy_name", "strategy_version", "indicator_engine_version", "application_version"],
)
@pytest.mark.parametrize("blank_value", ["", "   ", "\t\n"])
def test_strategy_metadata_rejects_blank_fields(field_name: str, blank_value: str) -> None:
    with pytest.raises(ValueError):
        make_strategy_metadata(**{field_name: blank_value})


# --- DecisionRecord ------------------------------------------------------


def test_decision_record_valid_construction() -> None:
    record = make_decision_record()
    assert record.record_id == DecisionRecordId("11111111-1111-4111-8111-111111111111")


def test_decision_record_is_frozen() -> None:
    record = make_decision_record()
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.recorded_at = UTC_NOW  # type: ignore[misc]


def test_decision_record_asset_is_computed_property_not_stored_field() -> None:
    field_names = {field.name for field in dataclasses.fields(DecisionRecord)}
    assert field_names == {
        "record_id",
        "recorded_at",
        "strategy_metadata",
        "market_snapshot",
        "indicator_snapshot",
        "decision",
    }
    assert "asset" not in field_names
    assert isinstance(DecisionRecord.asset, property)


def test_decision_record_asset_returns_decision_asset() -> None:
    asset = make_asset("AAPL")
    market_snapshot = make_market_snapshot(asset)
    indicator_snapshot = make_indicator_snapshot(market_snapshot)
    decision = make_decision(asset)
    record = DecisionRecord(
        record_id=DecisionRecordId("id-1"),
        recorded_at=UTC_NOW,
        strategy_metadata=make_strategy_metadata(),
        market_snapshot=market_snapshot,
        indicator_snapshot=indicator_snapshot,
        decision=decision,
    )
    assert record.asset == asset
    assert record.asset is decision.asset


@pytest.mark.parametrize("blank_id", ["", "   ", "\t\n"])
def test_decision_record_rejects_blank_record_id(blank_id: str) -> None:
    asset = make_asset()
    market_snapshot = make_market_snapshot(asset)
    with pytest.raises(ValueError):
        DecisionRecord(
            record_id=DecisionRecordId(blank_id),
            recorded_at=UTC_NOW,
            strategy_metadata=make_strategy_metadata(),
            market_snapshot=market_snapshot,
            indicator_snapshot=make_indicator_snapshot(market_snapshot),
            decision=make_decision(asset),
        )


def test_decision_record_rejects_naive_recorded_at() -> None:
    asset = make_asset()
    market_snapshot = make_market_snapshot(asset)
    with pytest.raises(ValueError):
        DecisionRecord(
            record_id=DecisionRecordId("id-1"),
            recorded_at=datetime(2026, 7, 21, 12, 0),  # naive
            strategy_metadata=make_strategy_metadata(),
            market_snapshot=market_snapshot,
            indicator_snapshot=make_indicator_snapshot(market_snapshot),
            decision=make_decision(asset),
        )


def test_decision_record_rejects_mismatched_indicator_snapshot_asset() -> None:
    market_snapshot = make_market_snapshot(make_asset("MSFT"))
    other_market_snapshot = make_market_snapshot(make_asset("AAPL"))
    indicator_snapshot = make_indicator_snapshot(other_market_snapshot)
    decision = make_decision(make_asset("MSFT"))
    with pytest.raises(ValueError):
        DecisionRecord(
            record_id=DecisionRecordId("id-1"),
            recorded_at=UTC_NOW,
            strategy_metadata=make_strategy_metadata(),
            market_snapshot=market_snapshot,
            indicator_snapshot=indicator_snapshot,
            decision=decision,
        )


def test_decision_record_rejects_mismatched_decision_asset() -> None:
    asset = make_asset("MSFT")
    market_snapshot = make_market_snapshot(asset)
    indicator_snapshot = make_indicator_snapshot(market_snapshot)
    decision = make_decision(make_asset("AAPL"))
    with pytest.raises(ValueError):
        DecisionRecord(
            record_id=DecisionRecordId("id-1"),
            recorded_at=UTC_NOW,
            strategy_metadata=make_strategy_metadata(),
            market_snapshot=market_snapshot,
            indicator_snapshot=indicator_snapshot,
            decision=decision,
        )


def test_decision_record_accepts_equal_but_distinct_market_snapshot_objects() -> None:
    """market_snapshot and indicator_snapshot.market_snapshot need not be the same
    object - only equal (matching the equality-based, not identity-based, pattern
    used everywhere else in this codebase, e.g. find_by_asset's Asset matching)."""
    asset = make_asset("MSFT")
    market_snapshot = make_market_snapshot(asset)
    equal_but_distinct_market_snapshot = make_market_snapshot(asset)
    assert market_snapshot is not equal_but_distinct_market_snapshot
    assert market_snapshot == equal_but_distinct_market_snapshot

    indicator_snapshot = make_indicator_snapshot(equal_but_distinct_market_snapshot)
    decision = make_decision(asset)

    record = DecisionRecord(
        record_id=DecisionRecordId("id-1"),
        recorded_at=UTC_NOW,
        strategy_metadata=make_strategy_metadata(),
        market_snapshot=market_snapshot,
        indicator_snapshot=indicator_snapshot,
        decision=decision,
    )

    assert record.market_snapshot == record.indicator_snapshot.market_snapshot


def test_decision_record_rejects_market_snapshot_diverging_from_indicator_snapshots(
) -> None:
    """Same asset, but different price_bars: DecisionRecord must reject this outright
    rather than let it round-trip through serialization, which reconstructs
    indicator_snapshot.market_snapshot from the top-level market_snapshot and would
    otherwise silently discard indicator_snapshot's real originating market data."""
    asset = make_asset("MSFT")
    market_snapshot = make_market_snapshot(asset)

    diverging_bar = PriceBar(
        timestamp=UTC_NOW,
        open=Decimal("500"),
        high=Decimal("501"),
        low=Decimal("499"),
        close=Decimal("500"),
        volume=9_999,
    )
    diverging_market_snapshot = MarketSnapshot(
        asset=asset, generated_at=UTC_NOW, price_bars=[diverging_bar]
    )
    assert market_snapshot != diverging_market_snapshot
    assert market_snapshot.asset == diverging_market_snapshot.asset  # same asset - only the invariant under test differs

    indicator_snapshot = make_indicator_snapshot(diverging_market_snapshot)
    decision = make_decision(asset)

    with pytest.raises(ValueError, match="market_snapshot"):
        DecisionRecord(
            record_id=DecisionRecordId("id-1"),
            recorded_at=UTC_NOW,
            strategy_metadata=make_strategy_metadata(),
            market_snapshot=market_snapshot,
            indicator_snapshot=indicator_snapshot,
            decision=decision,
        )
