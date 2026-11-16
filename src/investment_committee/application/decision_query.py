"""DecisionQueryService: read-only facade over the Decision Ledger.

For callers (conversation/) that must not depend on memory/ directly. See
docs/specs/spec-006-conversational-interface.md S16.
"""

from __future__ import annotations

from typing import Sequence

from investment_committee.domain import Asset, AssetType
from investment_committee.memory import (
    DecisionLedger,
    DecisionNotFoundError,
    DecisionRecord,
    DecisionRecordId,
    LedgerReadError,
)

from .errors import ApplicationServiceError


class DecisionNotFoundInApplicationError(ApplicationServiceError):
    """Wraps memory.DecisionNotFoundError from DecisionLedger.get."""


class DecisionQueryFailedError(ApplicationServiceError):
    """Wraps memory.LedgerReadError from any DecisionLedger read operation."""


class DecisionQueryService:
    """Read-only facade over the Decision Ledger, for callers (conversation/)
    that must not depend on memory/ directly (spec-006 S8)."""

    def __init__(self, ledger: DecisionLedger) -> None:
        self._ledger = ledger

    def get(self, record_id: DecisionRecordId) -> DecisionRecord:
        try:
            return self._ledger.get(record_id)
        except DecisionNotFoundError as exc:
            raise DecisionNotFoundInApplicationError(str(exc)) from exc
        except LedgerReadError as exc:
            raise DecisionQueryFailedError(str(exc)) from exc

    def list_by_asset(self, symbol: str) -> Sequence[DecisionRecord]:
        ticker = symbol.strip().upper()
        if not ticker:
            raise DecisionQueryFailedError("symbol must not be blank")
        asset = Asset(ticker=ticker, name=None, asset_type=AssetType.STOCK, exchange=None, currency=None)
        try:
            return self._ledger.find_by_asset(asset)
        except LedgerReadError as exc:
            raise DecisionQueryFailedError(str(exc)) from exc

    def list_recent(self, limit: int) -> Sequence[DecisionRecord]:
        try:
            records = self._ledger.list_all()
        except LedgerReadError as exc:
            raise DecisionQueryFailedError(str(exc)) from exc
        return tuple(sorted(records, key=lambda r: r.recorded_at, reverse=True))[:limit]
