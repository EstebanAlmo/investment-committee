"""DecisionLedger: the append-only port every DecisionLedger adapter implements.

Mirrors the existing port + adapter precedent
(market_intelligence/providers/base.py's MarketDataProvider). See
docs/specs/spec-005-decision-ledger.md S14.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from investment_committee.domain import Asset

from ..models import DecisionRecord, DecisionRecordId


class DecisionLedger(ABC):
    """Durable, append-only store of DecisionRecords.

    No update or delete operation exists on this interface, anywhere - a
    corrected or improved analysis is recorded as a new DecisionRecord, per
    product principle 2 (docs/architecture.md). See spec-005 SS 11, 19.
    """

    @abstractmethod
    def append(self, record: DecisionRecord) -> None:
        """Durably persist `record`.

        Raises DuplicateDecisionError if a record with the same record_id
        already exists. Raises LedgerWriteError for any other failure to
        persist (e.g. I/O failure). Never partially writes a record.
        """
        raise NotImplementedError

    @abstractmethod
    def get(self, record_id: DecisionRecordId) -> DecisionRecord:
        """Return the DecisionRecord with `record_id`.

        Raises DecisionNotFoundError if no such record exists.
        """
        raise NotImplementedError

    @abstractmethod
    def find_by_asset(self, asset: Asset) -> Sequence[DecisionRecord]:
        """Return every DecisionRecord for `asset`, in append order.

        Matching is by Asset equality. Returns an empty sequence, never
        raises, if no record matches.
        """
        raise NotImplementedError

    @abstractmethod
    def list_all(self) -> Sequence[DecisionRecord]:
        """Return every DecisionRecord in the ledger, in append order.

        Returns an empty sequence, never raises, if the ledger is empty.
        """
        raise NotImplementedError
