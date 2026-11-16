"""Tool base models and port.

See docs/specs/spec-006-conversational-interface.md S14. This phase
delivers the base models, the ToolOutcome type, and the Tool port only -
ToolRouter and every concrete Tool (AnalyzeAssetTool, ExplainDecisionTool,
etc.) are scoped to a later phase, since they call into
investment_committee.application, out of scope for this phase.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Sequence

from investment_committee.application import (
    AICommitteeReport,
    AlertCheck,
    Lesson,
    OpportunityScan,
    PortfolioView,
    ReplayReport,
    TrackerView,
    LessonCatalog,
    SegmentedBacktest,
    TradeExplanation,
    TradeJournalView,
    TradeSignal,
)
from investment_committee.memory import DecisionRecord

from ..models import ConversationSession, Intent


@dataclass(frozen=True)
class HelpContent:
    """What HelpTool returns - a static description of available intents."""

    available_intents: Sequence[str]
    description: str


@dataclass(frozen=True)
class NotYetAvailable:
    """What PortfolioSummaryTool/PerformanceSummaryTool return today -
    an honest "not built yet," never a fabricated number.
    """

    feature_name: str
    message: str


@dataclass(frozen=True)
class PolicyDeclined:
    """What UnsupportedTradeRequestTool (and any future permanently-out-of-
    scope request) returns - distinct from NotYetAvailable: this isn't
    coming later, it's a deliberate CLAUDE.md constraint.
    """

    reason: str
    message: str


ToolOutcome = (
    DecisionRecord
    | Sequence[DecisionRecord]
    | tuple[DecisionRecord, DecisionRecord]
    | HelpContent
    | NotYetAvailable
    | PolicyDeclined
    # simulated trading and lessons (application/ result types)
    | TradeSignal
    | TradeExplanation
    | SegmentedBacktest
    | TradeJournalView
    | Lesson
    | LessonCatalog
    | AICommitteeReport
    | OpportunityScan
    | ReplayReport
    | TrackerView
    | PortfolioView
    | AlertCheck
)
"""Every Tool returns one of these - all pre-existing domain/memory types
plus three small new presentation types, never a generic Any payload. This
mirrors EvidenceItem.value's restricted Decimal | str union (spec-002):
a closed set, not an open one, so ResponseSummarizer (S17) can pattern-match
exhaustively with `match outcome:` (Python 3.11, CLAUDE.md).
"""


class Tool(ABC):
    """One intent's adapter over the Application Layer. A Tool's only
    permitted collaborator is something from investment_committee.application
    (S8) - never memory/, committee/, indicators/, or market_intelligence/
    directly. No confirmation-gating mechanism exists on this ABC - Epic 8
    has no execution-capable Tool to gate (S19).
    """

    @abstractmethod
    def execute(self, intent: Intent, session: ConversationSession) -> ToolOutcome:
        """Execute this tool for `intent`. May raise ApplicationServiceError
        subclasses (S22) - a Tool does not catch its own application/
        failures; ConversationService does, once, in one place.
        """
        raise NotImplementedError
