"""Conformance suite run against every DecisionLedger adapter.

Asserts InMemoryDecisionLedger and JsonLinesDecisionLedger behave
identically for every port operation - this is what protects the "swap in
a future adapter with zero caller changes" claim (spec-005 S15, S24).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from investment_committee.memory.errors import DecisionNotFoundError, DuplicateDecisionError
from investment_committee.memory.ledgers.base import DecisionLedger
from investment_committee.memory.ledgers.in_memory import InMemoryDecisionLedger
from investment_committee.memory.ledgers.jsonl import JsonLinesDecisionLedger
from investment_committee.memory.models import DecisionRecordId

from .._support import make_asset, make_decision_record

LedgerFactory = Callable[[Path], DecisionLedger]


def _in_memory_factory(tmp_path: Path) -> DecisionLedger:
    return InMemoryDecisionLedger()


def _jsonl_factory(tmp_path: Path) -> DecisionLedger:
    return JsonLinesDecisionLedger(tmp_path / "decisions.jsonl")


LEDGER_FACTORIES = [_in_memory_factory, _jsonl_factory]


@pytest.fixture(params=LEDGER_FACTORIES, ids=["in_memory", "jsonl"])
def ledger(request: pytest.FixtureRequest, tmp_path: Path) -> DecisionLedger:
    factory: LedgerFactory = request.param
    return factory(tmp_path)


def test_append_then_get_returns_the_same_record(ledger: DecisionLedger) -> None:
    record = make_decision_record()

    ledger.append(record)

    assert ledger.get(record.record_id) == record


def test_append_duplicate_record_id_raises(ledger: DecisionLedger) -> None:
    record = make_decision_record()
    ledger.append(record)

    duplicate = make_decision_record()  # same default record_id
    with pytest.raises(DuplicateDecisionError):
        ledger.append(duplicate)


def test_get_missing_id_raises_not_found(ledger: DecisionLedger) -> None:
    with pytest.raises(DecisionNotFoundError):
        ledger.get(DecisionRecordId("does-not-exist"))


def test_find_by_asset_filters_by_asset_equality(ledger: DecisionLedger) -> None:
    msft = make_asset("MSFT")
    aapl = make_asset("AAPL")
    msft_record = make_decision_record(asset=msft, record_id="msft-record")
    aapl_record = make_decision_record(asset=aapl, record_id="aapl-record")

    ledger.append(msft_record)
    ledger.append(aapl_record)

    assert ledger.find_by_asset(msft) == (msft_record,)


def test_find_by_asset_matches_a_differently_normalized_but_equal_asset(ledger: DecisionLedger) -> None:
    record = make_decision_record(asset=make_asset("msft"))
    ledger.append(record)

    # Asset.__post_init__ normalizes the ticker - "MSFT" and "msft" produce
    # equal Assets, not the ledger performing its own normalization.
    assert ledger.find_by_asset(make_asset("MSFT")) == (record,)


def test_find_by_asset_returns_empty_tuple_for_no_match(ledger: DecisionLedger) -> None:
    ledger.append(make_decision_record(asset=make_asset("MSFT")))

    assert ledger.find_by_asset(make_asset("AAPL")) == ()


def test_list_all_returns_empty_tuple_when_empty(ledger: DecisionLedger) -> None:
    assert ledger.list_all() == ()


def test_list_all_returns_records_in_append_order(ledger: DecisionLedger) -> None:
    first = make_decision_record(record_id="record-1")
    second = make_decision_record(record_id="record-2")
    third = make_decision_record(record_id="record-3")

    ledger.append(first)
    ledger.append(second)
    ledger.append(third)

    assert ledger.list_all() == (first, second, third)


def test_find_by_asset_returns_records_in_append_order(ledger: DecisionLedger) -> None:
    asset = make_asset("MSFT")
    first = make_decision_record(asset=asset, record_id="record-1")
    second = make_decision_record(asset=asset, record_id="record-2")

    ledger.append(first)
    ledger.append(second)

    assert ledger.find_by_asset(asset) == (first, second)


def test_ledger_has_no_update_delete_or_exists_method(ledger: DecisionLedger) -> None:
    assert not hasattr(ledger, "update")
    assert not hasattr(ledger, "delete")
    assert not hasattr(ledger, "exists")
    assert not hasattr(ledger, "count")
