"""Investment Committee: agent reports, committee decisions, and the deterministic engine.

`models.py` defines shapes with no dependency on LLMs, orchestration,
storage, or network access (spec-002). `analysts/`, `chair.py`, and
`engine.py` add the first behavior on top of those shapes: a deterministic,
rule-checklist-based committee that turns one IndicatorSnapshot into one
InvestmentDecision, still with no LLM, persistence, or network access
(spec-003). See docs/architecture.md and
docs/specs/spec-003-deterministic-committee-engine.md for how this layer
fits in.
"""

from .analysts import (
    Analyst,
    DeterministicDevilsAdvocate,
    DeterministicRiskManager,
    DeterministicTechnicalAnalyst,
)
from .chair import DeterministicCommitteeChair
from .engine import Clock, DeterministicCommitteeEngine
from .errors import CommitteeEngineError
from .models import (
    ANALYST_ROLES,
    AgentReport,
    AgentStance,
    CommitteeRole,
    EvidenceItem,
    InvestmentAction,
    InvestmentDecision,
    RiskItem,
    RiskSeverity,
)

__all__ = [
    "CommitteeRole",
    "ANALYST_ROLES",
    "AgentStance",
    "InvestmentAction",
    "EvidenceItem",
    "AgentReport",
    "RiskSeverity",
    "RiskItem",
    "InvestmentDecision",
    "Analyst",
    "DeterministicTechnicalAnalyst",
    "DeterministicRiskManager",
    "DeterministicDevilsAdvocate",
    "DeterministicCommitteeChair",
    "DeterministicCommitteeEngine",
    "CommitteeEngineError",
    "Clock",
]
