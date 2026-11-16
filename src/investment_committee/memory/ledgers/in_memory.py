"""InMemoryDecisionLedger: a process-local, non-durable DecisionLedger adapter.

For unit tests, dependency injection, and short-lived scripts - not durable.
See docs/specs/spec-005-decision-ledger.md S15.
"""

from __future__ import annotations

from typing import Sequence

from investment_committee.domain import Asset

from ..errors import DecisionNotFoundError, DuplicateDecisionError
from ..models import DecisionRecord, DecisionRecordId
from .base import DecisionLedger


class InMemoryDecisionLedger(DecisionLedger):
    """Process-local, non-durable DecisionLedger. For tests and short scripts only.

    Each instance owns its own state - no shared mutable global state, no
    hidden singleton.
    """

    def __init__(self) -> None:
        self._records: dict[DecisionRecordId, DecisionRecord] = {}

    def append(self, record: DecisionRecord) -> None:
        if record.record_id in self._records:
            raise DuplicateDecisionError(f"record_id already exists: {record.record_id}")
        self._records[record.record_id] = record

    def get(self, record_id: DecisionRecordId) -> DecisionRecord:
        try:
            return self._records[record_id]
        except KeyError as exc:
            raise DecisionNotFoundError(f"no such record_id: {record_id}") from exc

    def find_by_asset(self, asset: Asset) -> Sequence[DecisionRecord]:
        return tuple(record for record in self._records.values() if record.asset == asset)

    def list_all(self) -> Sequence[DecisionRecord]:
        return tuple(self._records.values())
