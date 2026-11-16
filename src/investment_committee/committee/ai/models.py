"""AI committee results: per-agent outcomes and the audited final report.

Deliberately separate from the deterministic committee's AgentReport /
InvestmentDecision: the AI agents read a wider evidence packet (intraday,
macro documents, the strategy signal) and give qualitative confidence
only - there is no numeric confidence to put in an AgentReport without
inventing precision (ADR-001). The deterministic committee stays the
baseline and appears in the evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Sequence

from investment_committee.integrations.llm import LLMUsage

from .evidence import EvidencePacket
from .grounding import ValidationIssue
from .schemas import DevilsAdvocateOpinion, MacroOpinion, RiskOpinion, Synthesis, TechnicalOpinion

AI_COMMITTEE_VERSION = "ai-committee-1.0.0"


class AgentRole(str, Enum):
    TECHNICAL_ANALYST = "TECHNICAL_ANALYST"
    MACRO_ANALYST = "MACRO_ANALYST"
    RISK_MANAGER = "RISK_MANAGER"
    DEVILS_ADVOCATE = "DEVILS_ADVOCATE"
    COORDINATOR = "COORDINATOR"


class AgentStatus(str, Enum):
    OK = "OK"
    NOT_AVAILABLE = "NOT_AVAILABLE"  # nothing to analyse (no macro data): no model call
    FAILED = "FAILED"  # provider error or invalid reply
    SKIPPED_BUDGET = "SKIPPED_BUDGET"
    SKIPPED = "SKIPPED"  # an earlier fatal error stopped the run


class ValidationStatus(str, Enum):
    COMPLETE = "COMPLETE"  # every agent answered; no control changed anything
    ADJUSTED = "ADJUSTED"  # deterministic controls changed the result or removed claims
    PARTIAL = "PARTIAL"  # some agent failed or was skipped
    FAILED = "FAILED"  # no usable conclusion: deterministic fallback


@dataclass(frozen=True)
class AgentResult:
    role: AgentRole
    status: AgentStatus
    opinion: TechnicalOpinion | MacroOpinion | RiskOpinion | DevilsAdvocateOpinion | Synthesis | None
    error: str | None = None  # safe, Spanish
    usage: LLMUsage = field(default_factory=LLMUsage)
    calls: int = 0
    repaired: bool = False  # a format-repair request was needed and succeeded
    validation_errors: Sequence[str] = ()  # schema violations seen (field + rule, never model content)
    notes: Sequence[str] = ()  # deterministic normalisations applied to the reply
    provider_diagnostic: str | None = None  # safe provider failure details (type, reason, HTTP status, finish_reason, request id, tokens)


@dataclass(frozen=True)
class FinalDecision:
    """The committee's conclusion after deterministic controls."""

    conclusion: str  # FAVORABLE | DESFAVORABLE | NEUTRAL | NO_CONCLUYENTE
    trading_stance: str  # COMPRA | VENTA | NO_OPERAR | NO_APLICA
    confidence: str  # BAJA | MEDIA | ALTA (qualitative)
    executive_summary: str
    beginner_explanation: str
    evidence_for: Sequence[str]
    evidence_against: Sequence[str]
    risks: Sequence[str]
    missing_information: Sequence[str]
    disagreements: Sequence[str]
    change_conditions: Sequence[str]
    model_conclusion: str | None  # what the model proposed before controls
    # "IA": the coordinator's summary. "CONTINGENCIA_PYTHON": there was no
    # valid synthesis and Python assembled a contingency summary instead.
    summary_source: str = "IA"


@dataclass(frozen=True)
class AICommitteeReport:
    analysis_id: str
    created_at: datetime
    committee_version: str
    provider: str
    model: str
    evidence: EvidencePacket
    agents: Sequence[AgentResult]
    decision: FinalDecision
    controls: Sequence[str]  # deterministic adjustments, in Spanish
    issues: Sequence[ValidationIssue]
    validation_status: ValidationStatus
    usage: LLMUsage
    calls: int
    deterministic_baseline: str | None  # the /analyze committee's decision, for reference

    def __post_init__(self) -> None:
        object.__setattr__(self, "agents", tuple(self.agents))
        object.__setattr__(self, "controls", tuple(self.controls))
        object.__setattr__(self, "issues", tuple(self.issues))

    def agent(self, role: AgentRole) -> AgentResult | None:
        return next((result for result in self.agents if result.role is role), None)

    @property
    def estimated_cost_usd(self) -> Decimal | None:
        return self.usage.estimated_cost_usd
