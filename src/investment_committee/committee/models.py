"""Committee domain types: AgentReport, InvestmentDecision, and their parts.

These are frozen dataclasses with no dependency on external providers,
storage, LLMs, or orchestration — see docs/specs/spec-002-investment-committee-domain.md.
`committee/` does not compute anything; it only defines the shapes and
invariants that a future committee orchestration layer will produce and
consume.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Sequence

from investment_committee.domain import Asset, RecommendationAction

from ._validation import require_timezone_aware


class CommitteeRole(str, Enum):
    """A seat on the investment committee."""

    TECHNICAL_ANALYST = "TECHNICAL_ANALYST"
    RISK_MANAGER = "RISK_MANAGER"
    DEVILS_ADVOCATE = "DEVILS_ADVOCATE"
    COMMITTEE_CHAIR = "COMMITTEE_CHAIR"


ANALYST_ROLES: frozenset[CommitteeRole] = frozenset(
    {CommitteeRole.TECHNICAL_ANALYST, CommitteeRole.RISK_MANAGER, CommitteeRole.DEVILS_ADVOCATE}
)


class AgentStance(str, Enum):
    """An analyst's opinion of the thesis under discussion, not a trade instruction."""

    SUPPORT = "SUPPORT"
    NEUTRAL = "NEUTRAL"
    OPPOSE = "OPPOSE"


class RiskSeverity(str, Enum):
    """How severe a RiskItem is."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


# The final action a committee decision advises is the same concept as
# domain.Recommendation's action, so it is re-exported rather than
# duplicated as a second BUY/HOLD/SELL enum.
InvestmentAction = RecommendationAction


@dataclass(frozen=True)
class EvidenceItem:
    """One structured piece of evidence used by an agent."""

    key: str
    value: Decimal | str
    interpretation: str

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("EvidenceItem.key must not be blank")
        if not isinstance(self.value, (Decimal, str)):
            raise ValueError("EvidenceItem.value must be a Decimal or str")
        if isinstance(self.value, str) and not self.value.strip():
            raise ValueError("EvidenceItem.value must not be blank")
        if not self.interpretation.strip():
            raise ValueError("EvidenceItem.interpretation must not be blank")


@dataclass(frozen=True)
class AgentReport:
    """One analyst's opinion of an Asset, produced from an IndicatorSnapshot."""

    asset: Asset
    role: CommitteeRole
    stance: AgentStance
    confidence: Decimal
    summary: str
    reasoning: str
    evidence: Sequence[EvidenceItem]
    generated_at: datetime

    def __post_init__(self) -> None:
        if self.role not in ANALYST_ROLES:
            raise ValueError("AgentReport.role must be an analyst role, not COMMITTEE_CHAIR")
        if not isinstance(self.confidence, Decimal):
            raise ValueError("AgentReport.confidence must be a Decimal")
        if not (Decimal("0") <= self.confidence <= Decimal("1")):
            raise ValueError("AgentReport.confidence must be between 0 and 1")
        if not self.summary.strip():
            raise ValueError("AgentReport.summary must not be blank")
        if not self.reasoning.strip():
            raise ValueError("AgentReport.reasoning must not be blank")
        require_timezone_aware(self.generated_at, "generated_at")

        evidence = tuple(self.evidence)
        for item in evidence:
            if not isinstance(item, EvidenceItem):
                raise ValueError("AgentReport.evidence entries must be EvidenceItem instances")
        object.__setattr__(self, "evidence", evidence)


@dataclass(frozen=True)
class RiskItem:
    """A severity-rated risk raised by a specific analyst role."""

    key: str
    severity: RiskSeverity
    description: str
    source_role: CommitteeRole

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("RiskItem.key must not be blank")
        if not self.description.strip():
            raise ValueError("RiskItem.description must not be blank")
        if self.source_role not in ANALYST_ROLES:
            raise ValueError("RiskItem.source_role must be an analyst role, not COMMITTEE_CHAIR")


@dataclass(frozen=True)
class InvestmentDecision:
    """The committee's final output for an Asset, with supporting reports."""

    asset: Asset
    action: InvestmentAction
    confidence: Decimal
    summary: str
    agent_reports: Sequence[AgentReport]
    dissenting_opinions: Sequence[AgentReport]
    risks: Sequence[RiskItem]
    generated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.confidence, Decimal):
            raise ValueError("InvestmentDecision.confidence must be a Decimal")
        if not (Decimal("0") <= self.confidence <= Decimal("1")):
            raise ValueError("InvestmentDecision.confidence must be between 0 and 1")
        if not self.summary.strip():
            raise ValueError("InvestmentDecision.summary must not be blank")
        require_timezone_aware(self.generated_at, "generated_at")

        agent_reports = tuple(self.agent_reports)
        for report in agent_reports:
            if not isinstance(report, AgentReport):
                raise ValueError("InvestmentDecision.agent_reports entries must be AgentReport instances")
        roles = [report.role for report in agent_reports]
        if len(agent_reports) != len(ANALYST_ROLES) or set(roles) != ANALYST_ROLES:
            raise ValueError("InvestmentDecision.agent_reports must contain exactly one report per analyst role")
        for report in agent_reports:
            if report.asset != self.asset:
                raise ValueError("InvestmentDecision.agent_reports must reference the same asset as the decision")

        dissenting_opinions = tuple(self.dissenting_opinions)
        for opinion in dissenting_opinions:
            if not isinstance(opinion, AgentReport):
                raise ValueError("InvestmentDecision.dissenting_opinions entries must be AgentReport instances")
            if opinion not in agent_reports:
                raise ValueError("InvestmentDecision.dissenting_opinions must reference a report in agent_reports")
        dissenting_roles = [opinion.role for opinion in dissenting_opinions]
        if len(dissenting_roles) != len(set(dissenting_roles)):
            raise ValueError("InvestmentDecision.dissenting_opinions must not contain duplicate reports")

        risks = tuple(self.risks)
        for risk in risks:
            if not isinstance(risk, RiskItem):
                raise ValueError("InvestmentDecision.risks entries must be RiskItem instances")
        risk_identities = [(risk.key, risk.source_role) for risk in risks]
        if len(risk_identities) != len(set(risk_identities)):
            raise ValueError("InvestmentDecision.risks must not contain duplicate (key, source_role) entries")

        object.__setattr__(self, "agent_reports", agent_reports)
        object.__setattr__(self, "dissenting_opinions", dissenting_opinions)
        object.__setattr__(self, "risks", risks)
