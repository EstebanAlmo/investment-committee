"""AI committee v1: four LLM-backed agents and a coordinator over a shared,
Python-computed evidence packet, with deterministic controls.

A sibling of the deterministic committee, not a replacement: /analyze keeps
using DeterministicCommitteeEngine (the baseline, docs/architecture.md
principle 1). The model is reached only through
integrations.llm.LLMClient. Nothing here fetches data, places orders, or
creates simulated trades.
"""

from __future__ import annotations

from .coordinator import AICommitteeCoordinator, AnalysisBudget, apply_controls
from .evidence import EvidenceItem, EvidencePacket, TradingSignalSummary, sanitize_external_text
from .grounding import GroundingChecker, ValidationIssue
from .models import (
    AI_COMMITTEE_VERSION,
    AgentResult,
    AgentRole,
    AgentStatus,
    AICommitteeReport,
    FinalDecision,
    ValidationStatus,
)
from .schemas import (
    AgentOutputError,
    Argument,
    DevilsAdvocateOpinion,
    MacroOpinion,
    RiskOpinion,
    Synthesis,
    TechnicalOpinion,
)

__all__ = [
    "AI_COMMITTEE_VERSION",
    "AICommitteeCoordinator",
    "AICommitteeReport",
    "AgentOutputError",
    "AgentResult",
    "AgentRole",
    "AgentStatus",
    "AnalysisBudget",
    "Argument",
    "DevilsAdvocateOpinion",
    "EvidenceItem",
    "EvidencePacket",
    "FinalDecision",
    "GroundingChecker",
    "MacroOpinion",
    "RiskOpinion",
    "Synthesis",
    "TechnicalOpinion",
    "TradingSignalSummary",
    "ValidationIssue",
    "ValidationStatus",
    "apply_controls",
    "sanitize_external_text",
]
