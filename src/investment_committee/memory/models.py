"""DecisionRecord and StrategyMetadata: the durable record of one completed analysis.

DecisionRecord embeds the exact MarketSnapshot, IndicatorSnapshot, and
InvestmentDecision objects produced by one ApplicationService.analyze_asset()
call, verbatim - it never holds a reference to AnalysisResult (application/
depends on memory/, not the reverse) and never inspects any engine object.
See docs/specs/spec-005-decision-ledger.md SS9-10.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import NewType

from investment_committee.committee import InvestmentDecision
from investment_committee.domain import Asset, MarketSnapshot
from investment_committee.indicators import IndicatorSnapshot

from ._validation import require_timezone_aware

DecisionRecordId = NewType("DecisionRecordId", str)
"""A ledger-assigned identity, minted once per DecisionRecord (spec-005 S11).

A NewType wrapping str, not a full value-object dataclass: this is a single
opaque identifier with no independent validation rules of its own beyond
"non-blank" (checked in DecisionRecord.__post_init__).
"""


@dataclass(frozen=True)
class StrategyMetadata:
    """Which strategy, and which version of it, produced a DecisionRecord.

    All four fields are plain, hand-maintained strings bumped manually when
    the relevant ruleset changes - no semantic-versioning machinery, no
    auto-derivation from a git SHA (spec-005 S13).
    """

    strategy_name: str
    strategy_version: str
    indicator_engine_version: str
    application_version: str

    def __post_init__(self) -> None:
        for field_name in (
            "strategy_name",
            "strategy_version",
            "indicator_engine_version",
            "application_version",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"StrategyMetadata.{field_name} must not be blank")


@dataclass(frozen=True)
class DecisionRecord:
    """A durable, append-only record of one completed analysis.

    Never updated after construction; a corrected or improved analysis is a
    new DecisionRecord, never an edit to this one (product principle 2,
    docs/architecture.md).
    """

    record_id: DecisionRecordId
    recorded_at: datetime
    strategy_metadata: StrategyMetadata
    market_snapshot: MarketSnapshot
    indicator_snapshot: IndicatorSnapshot
    decision: InvestmentDecision

    def __post_init__(self) -> None:
        if not str(self.record_id).strip():
            raise ValueError("DecisionRecord.record_id must not be blank")
        require_timezone_aware(self.recorded_at, "recorded_at")
        if not (
            self.market_snapshot.asset
            == self.indicator_snapshot.asset
            == self.decision.asset
        ):
            raise ValueError(
                "DecisionRecord requires market_snapshot, indicator_snapshot, "
                "and decision to share the same asset"
            )
        if self.market_snapshot != self.indicator_snapshot.market_snapshot:
            raise ValueError(
                "DecisionRecord requires market_snapshot to equal "
                "indicator_snapshot.market_snapshot - serialization reconstructs "
                "indicator_snapshot.market_snapshot from the top-level market_snapshot, "
                "so a mismatch here would be silently lost on persistence"
            )

    @property
    def asset(self) -> Asset:
        """The asset this record's analysis was performed for."""
        return self.decision.asset
