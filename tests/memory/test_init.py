"""Tests for investment_committee.memory's package-level exports."""

from __future__ import annotations

import investment_committee.memory as memory


def test_public_exports_are_exactly_the_expected_set() -> None:
    expected = {
        "DecisionRecord",
        "DecisionRecordId",
        "StrategyMetadata",
        "DecisionLedger",
        "InMemoryDecisionLedger",
        "JsonLinesDecisionLedger",
        "DEFAULT_LEDGER_PATH",
        "LedgerError",
        "DuplicateDecisionError",
        "DecisionNotFoundError",
        "LedgerWriteError",
        "LedgerReadError",
        "DecisionRecordDeserializationError",
        "DETERMINISTIC_STRATEGY_METADATA",
    }
    assert set(memory.__all__) == expected
    for name in expected:
        assert hasattr(memory, name)
