"""Tests for investment_committee.memory.serialization: to_persisted_dict / from_persisted_dict."""

from __future__ import annotations

import copy
import json
from decimal import Decimal

import pytest

from investment_committee.committee import CommitteeRole, InvestmentAction, InvestmentDecision
from investment_committee.domain import MarketSnapshot, PriceBar
from investment_committee.memory.errors import DecisionRecordDeserializationError
from investment_committee.memory.models import DecisionRecord, DecisionRecordId
from investment_committee.memory.serialization import from_persisted_dict, to_persisted_dict

from ._support import (
    UTC_NOW,
    make_agent_report,
    make_asset,
    make_decision,
    make_decision_record,
    make_evidence_item,
    make_indicator_snapshot,
    make_market_snapshot,
    make_strategy_metadata,
)


def _record_with_evidence(evidence: list) -> DecisionRecord:
    """Build a DecisionRecord whose TECHNICAL_ANALYST report carries `evidence`."""
    asset = make_asset()
    market_snapshot = make_market_snapshot(asset)
    indicator_snapshot = make_indicator_snapshot(market_snapshot)
    decision = InvestmentDecision(
        asset=asset,
        action=InvestmentAction.HOLD,
        confidence=Decimal("0.5"),
        summary="HOLD",
        agent_reports=[
            make_agent_report(asset=asset, role=CommitteeRole.TECHNICAL_ANALYST, evidence=evidence),
            make_agent_report(asset=asset, role=CommitteeRole.RISK_MANAGER),
            make_agent_report(asset=asset, role=CommitteeRole.DEVILS_ADVOCATE),
        ],
        dissenting_opinions=(),
        risks=(),
        generated_at=UTC_NOW,
    )
    return DecisionRecord(
        record_id=DecisionRecordId("11111111-1111-4111-8111-111111111111"),
        recorded_at=UTC_NOW,
        strategy_metadata=make_strategy_metadata(),
        market_snapshot=market_snapshot,
        indicator_snapshot=indicator_snapshot,
        decision=decision,
    )


def test_round_trip_reconstructs_an_equal_decision_record() -> None:
    record = make_decision_record()

    data = to_persisted_dict(record)
    reconstructed = from_persisted_dict(data)

    assert reconstructed == record


def test_round_trip_produces_plain_json_compatible_values_not_asdict_output() -> None:
    record = make_decision_record()

    data = to_persisted_dict(record)

    # A hand-rolled serializer produces plain, JSON-compatible values only -
    # dataclasses.asdict() would instead recursively copy the dataclass tree,
    # leaving raw Decimal/datetime/Enum objects (and, for MarketSnapshot's
    # MappingProxyType metrics field, an object json.dumps() cannot even
    # serialize) rather than str.
    assert isinstance(data["record_id"], str)
    assert isinstance(data["recorded_at"], str)
    assert isinstance(data["market_snapshot"]["price_bars"][0]["open"], str)
    assert data["market_snapshot"]["asset"]["asset_type"] == "STOCK"
    json.dumps(data)  # must not raise - every value is JSON-serializable


def test_decimal_evidence_item_round_trips_as_decimal() -> None:
    evidence = [make_evidence_item(key="close_price", value=Decimal("151.02"))]
    record = _record_with_evidence(evidence)

    data = to_persisted_dict(record)
    evidence_dict = data["decision"]["agent_reports"][0]["evidence"][0]
    assert evidence_dict["value_type"] == "decimal"
    assert evidence_dict["value"] == "151.02"

    reconstructed = from_persisted_dict(data)
    reconstructed_value = reconstructed.decision.agent_reports[0].evidence[0].value
    assert isinstance(reconstructed_value, Decimal)
    assert reconstructed_value == Decimal("151.02")


def test_numeric_looking_string_evidence_value_round_trips_as_str() -> None:
    evidence = [make_evidence_item(key="checklist_coverage", value="42")]
    record = _record_with_evidence(evidence)

    data = to_persisted_dict(record)
    evidence_dict = data["decision"]["agent_reports"][0]["evidence"][0]
    assert evidence_dict["value_type"] == "string"
    assert evidence_dict["value"] == "42"

    reconstructed = from_persisted_dict(data)
    reconstructed_value = reconstructed.decision.agent_reports[0].evidence[0].value
    assert reconstructed_value == "42"
    assert isinstance(reconstructed_value, str)
    assert not isinstance(reconstructed_value, Decimal)


@pytest.mark.parametrize("bad_value_type", [None, "float", "int", ""])
def test_evidence_item_unrecognized_value_type_fails(bad_value_type: object) -> None:
    record = make_decision_record()
    data = to_persisted_dict(record)
    data["decision"]["agent_reports"][0]["evidence"][0]["value_type"] = bad_value_type

    with pytest.raises(DecisionRecordDeserializationError):
        from_persisted_dict(data)


def test_evidence_item_missing_value_type_fails() -> None:
    record = make_decision_record()
    data = to_persisted_dict(record)
    del data["decision"]["agent_reports"][0]["evidence"][0]["value_type"]

    with pytest.raises(DecisionRecordDeserializationError):
        from_persisted_dict(data)


def test_evidence_item_invalid_decimal_string_fails() -> None:
    record = make_decision_record()
    data = to_persisted_dict(record)
    data["decision"]["agent_reports"][0]["evidence"][0]["value_type"] = "decimal"
    data["decision"]["agent_reports"][0]["evidence"][0]["value"] = "not-a-number"

    with pytest.raises(DecisionRecordDeserializationError) as exc_info:
        from_persisted_dict(data)
    assert exc_info.value.__cause__ is not None


def test_malformed_enum_value_fails() -> None:
    record = make_decision_record()
    data = to_persisted_dict(record)
    data["decision"]["action"] = "NOT_A_REAL_ACTION"

    with pytest.raises(DecisionRecordDeserializationError) as exc_info:
        from_persisted_dict(data)
    assert exc_info.value.__cause__ is not None


def test_malformed_datetime_fails() -> None:
    record = make_decision_record()
    data = to_persisted_dict(record)
    data["recorded_at"] = "not-a-datetime"

    with pytest.raises(DecisionRecordDeserializationError) as exc_info:
        from_persisted_dict(data)
    assert exc_info.value.__cause__ is not None


@pytest.mark.parametrize("bad_schema_version", [0, 2, "1", None])
def test_unsupported_schema_version_fails(bad_schema_version: object) -> None:
    record = make_decision_record()
    data = to_persisted_dict(record)
    data["schema_version"] = bad_schema_version

    with pytest.raises(DecisionRecordDeserializationError):
        from_persisted_dict(data)


def test_missing_required_field_fails() -> None:
    record = make_decision_record()
    data = to_persisted_dict(record)
    del data["market_snapshot"]

    with pytest.raises(DecisionRecordDeserializationError) as exc_info:
        from_persisted_dict(data)
    assert exc_info.value.__cause__ is not None


def test_unknown_top_level_keys_are_ignored() -> None:
    record = make_decision_record()
    data = to_persisted_dict(record)
    data["some_future_field"] = "unrecognized but harmless"

    reconstructed = from_persisted_dict(data)

    assert reconstructed == record


def test_semantically_invalid_reconstructed_value_fails() -> None:
    record = make_decision_record()
    data = to_persisted_dict(record)
    # Confidence outside [0, 1] fails InvestmentDecision's own __post_init__.
    data["decision"]["confidence"] = "1.5"

    with pytest.raises(DecisionRecordDeserializationError) as exc_info:
        from_persisted_dict(data)
    assert exc_info.value.__cause__ is not None


def test_from_persisted_dict_does_not_mutate_input() -> None:
    record = make_decision_record()
    data = to_persisted_dict(record)
    original = copy.deepcopy(data)

    from_persisted_dict(data)

    assert data == original


def test_a_diverging_market_snapshot_cannot_reach_serialization_at_all() -> None:
    """Regression test: to_persisted_dict()/from_persisted_dict() reconstruct
    indicator_snapshot.market_snapshot from the top-level market_snapshot alone -
    if market_snapshot and indicator_snapshot.market_snapshot were ever allowed to
    diverge, that reconstruction would silently discard indicator_snapshot's real
    originating market data. DecisionRecord.__post_init__ now closes this off at
    construction time, so no such record can ever exist to be serialized."""
    asset = make_asset("MSFT")
    market_snapshot = make_market_snapshot(asset)
    diverging_market_snapshot = MarketSnapshot(
        asset=asset,
        generated_at=UTC_NOW,
        price_bars=[
            PriceBar(
                timestamp=UTC_NOW,
                open=Decimal("500"),
                high=Decimal("501"),
                low=Decimal("499"),
                close=Decimal("500"),
                volume=9_999,
            )
        ],
    )

    with pytest.raises(ValueError, match="market_snapshot"):
        DecisionRecord(
            record_id=DecisionRecordId("id-1"),
            recorded_at=UTC_NOW,
            strategy_metadata=make_strategy_metadata(),
            market_snapshot=market_snapshot,
            indicator_snapshot=make_indicator_snapshot(diverging_market_snapshot),
            decision=make_decision(asset),
        )
