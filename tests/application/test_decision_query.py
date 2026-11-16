"""Tests for application.decision_query.DecisionQueryService. See spec-006 S16, S26."""

from __future__ import annotations

from datetime import timedelta
from typing import Sequence

import pytest

from investment_committee.application import (
    DecisionNotFoundInApplicationError,
    DecisionQueryFailedError,
    DecisionQueryService,
    build_default_decision_query_service,
    build_default_ledger,
    build_default_record_decision_use_case,
)
from investment_committee.domain import Asset
from investment_committee.memory import (
    DecisionLedger,
    DecisionNotFoundError,
    DecisionRecord,
    DecisionRecordId,
    InMemoryDecisionLedger,
    JsonLinesDecisionLedger,
    LedgerReadError,
)

from tests.memory._support import UTC_NOW, make_asset, make_decision_record


class _FailingLedger(DecisionLedger):
    def __init__(self, error: Exception) -> None:
        self._error = error

    def append(self, record: DecisionRecord) -> None:
        raise AssertionError("DecisionQueryService must never write")

    def get(self, record_id: DecisionRecordId) -> DecisionRecord:
        raise self._error

    def find_by_asset(self, asset: Asset) -> Sequence[DecisionRecord]:
        raise self._error

    def list_all(self) -> Sequence[DecisionRecord]:
        raise self._error


def test_get_returns_the_record() -> None:
    ledger = InMemoryDecisionLedger()
    record = make_decision_record()
    ledger.append(record)

    assert DecisionQueryService(ledger).get(record.record_id) is record


def test_get_wraps_not_found_with_cause() -> None:
    cause = DecisionNotFoundError("missing")
    with pytest.raises(DecisionNotFoundInApplicationError) as excinfo:
        DecisionQueryService(_FailingLedger(cause)).get(DecisionRecordId("x"))
    assert excinfo.value.__cause__ is cause


@pytest.mark.parametrize("call", ["get", "list_by_asset", "list_recent"])
def test_read_errors_are_wrapped_with_cause(call: str) -> None:
    cause = LedgerReadError("disk unreadable")
    service = DecisionQueryService(_FailingLedger(cause))
    with pytest.raises(DecisionQueryFailedError) as excinfo:
        if call == "get":
            service.get(DecisionRecordId("x"))
        elif call == "list_by_asset":
            service.list_by_asset("AAPL")
        else:
            service.list_recent(5)
    assert excinfo.value.__cause__ is cause


def test_list_by_asset_normalizes_ticker() -> None:
    ledger = InMemoryDecisionLedger()
    aapl = make_decision_record(asset=make_asset("AAPL"), record_id="a")
    msft = make_decision_record(asset=make_asset("MSFT"), record_id="m")
    ledger.append(aapl)
    ledger.append(msft)

    assert tuple(DecisionQueryService(ledger).list_by_asset("  aapl ")) == (aapl,)


def test_list_by_asset_rejects_blank_symbol() -> None:
    with pytest.raises(DecisionQueryFailedError):
        DecisionQueryService(InMemoryDecisionLedger()).list_by_asset("   ")


def test_list_recent_is_most_recent_first_and_limited() -> None:
    ledger = InMemoryDecisionLedger()
    oldest = make_decision_record(record_id="1", recorded_at=UTC_NOW)
    newest = make_decision_record(record_id="2", recorded_at=UTC_NOW + timedelta(days=2))
    middle = make_decision_record(record_id="3", recorded_at=UTC_NOW + timedelta(days=1))
    for record in (oldest, newest, middle):
        ledger.append(record)

    assert tuple(DecisionQueryService(ledger).list_recent(2)) == (newest, middle)


def test_build_default_ledger_is_a_jsonl_ledger_without_io() -> None:
    assert isinstance(build_default_ledger(), JsonLinesDecisionLedger)


def test_query_service_and_use_case_share_an_injected_ledger() -> None:
    ledger = InMemoryDecisionLedger()
    record = make_decision_record()
    ledger.append(record)

    query_service = build_default_decision_query_service(ledger=ledger)
    build_default_record_decision_use_case(ledger=ledger)

    assert query_service.get(record.record_id) is record
