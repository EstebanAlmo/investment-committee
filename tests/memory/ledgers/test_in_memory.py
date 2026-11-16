"""InMemoryDecisionLedger-specific behavior not covered by the conformance suite."""

from __future__ import annotations

from investment_committee.memory.ledgers.in_memory import InMemoryDecisionLedger

from .._support import make_decision_record


def test_each_instance_owns_independent_state() -> None:
    first_ledger = InMemoryDecisionLedger()
    second_ledger = InMemoryDecisionLedger()

    first_ledger.append(make_decision_record())

    assert first_ledger.list_all() != ()
    assert second_ledger.list_all() == ()


def test_no_hidden_singleton_or_shared_global_state() -> None:
    assert InMemoryDecisionLedger()._records is not InMemoryDecisionLedger()._records
