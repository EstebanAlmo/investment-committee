"""RecordDecisionUseCase: durably records a completed analysis into the Decision Ledger.

Two entry points converge on the same append: record() takes an
already-computed AnalysisResult and never re-analyzes; analyze_and_record()
is the thin composition of analyze_asset() followed by record(). See
docs/specs/spec-005-decision-ledger.md S17.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from investment_committee.memory import (
    DecisionLedger,
    DecisionRecord,
    DecisionRecordId,
    LedgerError,
    StrategyMetadata,
)

from ._validation import require_timezone_aware
from .errors import ApplicationServiceError
from .models import AnalysisResult
from .service import ApplicationService

Clock = Callable[[], datetime]
IdFactory = Callable[[], DecisionRecordId]
# Duplicated one-line type aliases rather than importing from committee/indicators -
# same rationale as committee/_validation.py duplicating domain/_validation.py
# (spec-002, approved decision D4): this module's Clock/IdFactory are conceptually
# its own, even though their shapes happen to match other layers'.


class DecisionRecordingError(ApplicationServiceError):
    """Raised when analysis succeeded but the DecisionRecord could not be appended.

    Never raised by record() for any reason related to analysis - record()
    performs no analysis. The completed AnalysisResult is attached as
    `analysis_result` so a caller can inspect or retry recording without
    re-running the analysis. See spec-005 S19.
    """

    def __init__(self, message: str, analysis_result: AnalysisResult) -> None:
        super().__init__(message)
        self.analysis_result = analysis_result


@dataclass(frozen=True)
class RecordDecisionResult:
    """What one record()/analyze_and_record() call produced.

    persisted_at is timezone-aware UTC and is constructed only after
    DecisionLedger.append() has returned without raising - see spec-005 S12.
    """

    record: DecisionRecord
    persisted_at: datetime

    def __post_init__(self) -> None:
        require_timezone_aware(self.persisted_at, "persisted_at")


class RecordDecisionUseCase:
    """Durably records DecisionRecords, either from an existing AnalysisResult
    (record()) or by running a fresh analysis first (analyze_and_record())."""

    def __init__(
        self,
        application_service: ApplicationService,
        ledger: DecisionLedger,
        strategy_metadata: StrategyMetadata,
        clock: Clock = lambda: datetime.now(timezone.utc),
        id_factory: IdFactory = lambda: DecisionRecordId(str(uuid.uuid4())),
    ) -> None:
        self._application_service = application_service
        self._ledger = ledger
        self._strategy_metadata = strategy_metadata
        self._clock = clock
        self._id_factory = id_factory

    def record(self, analysis_result: AnalysisResult) -> RecordDecisionResult:
        """Durably record `analysis_result` exactly as given.

        Never calls ApplicationService.analyze_asset() and never fetches
        market data - `analysis_result` is used verbatim. Raises
        DecisionRecordingError, with `analysis_result` attached, if
        DecisionLedger.append() fails.
        """
        record = DecisionRecord(
            record_id=self._id_factory(),
            recorded_at=self._clock(),
            strategy_metadata=self._strategy_metadata,
            market_snapshot=analysis_result.market_snapshot,
            indicator_snapshot=analysis_result.indicator_snapshot,
            decision=analysis_result.decision,
        )

        try:
            self._ledger.append(record)
        except LedgerError as exc:
            raise DecisionRecordingError(
                f"failed to record decision for {analysis_result.asset.ticker}: {exc}",
                analysis_result=analysis_result,
            ) from exc

        return RecordDecisionResult(record=record, persisted_at=self._clock())

    def analyze_and_record(self, symbol: str) -> RecordDecisionResult:
        """Run one analysis pass for `symbol`, then durably record it.

        Equivalent to `self.record(self._application_service.analyze_asset(symbol))`.
        Raises the same ApplicationServiceError subclasses as
        ApplicationService.analyze_asset() if analysis itself fails - no
        DecisionRecord is constructed and record() is never reached in that
        case.
        """
        analysis_result = self._application_service.analyze_asset(symbol)
        return self.record(analysis_result)
