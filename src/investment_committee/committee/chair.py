"""DeterministicCommitteeChair: reconciles three AgentReports into one InvestmentDecision.

Implements the four-step decision process from
docs/specs/spec-003-deterministic-committee-engine.md, section 5: a
decision matrix, a high-risk downgrade, a confidence floor, and a discrete
decision-confidence table. No weighted arithmetic anywhere.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Mapping, Sequence

from investment_committee.domain import Asset

from .errors import CommitteeEngineError
from .models import (
    ANALYST_ROLES,
    AgentReport,
    AgentStance,
    CommitteeRole,
    InvestmentAction,
    InvestmentDecision,
    RiskItem,
    RiskSeverity,
)

_CONFIDENCE_FLOOR = Decimal("0.50")
_DOWNGRADE_CONFIDENCE = Decimal("0.60")
_DISSENT_CONFIDENCE = {
    0: Decimal("0.90"),
    1: Decimal("0.70"),
    2: Decimal("0.50"),
    3: Decimal("0.30"),
}
_UNAVAILABLE_SUFFIX = "_unavailable"

RISK_EVIDENCE_SEVERITY: Mapping[str, RiskSeverity] = {
    "annualized_volatility_moderate": RiskSeverity.MEDIUM,
    "annualized_volatility_high": RiskSeverity.HIGH,
    "maximum_drawdown_moderate": RiskSeverity.MEDIUM,
    "maximum_drawdown_severe": RiskSeverity.HIGH,
    "rsi_14_extreme": RiskSeverity.MEDIUM,
    "return_30d_extreme": RiskSeverity.MEDIUM,
    "overextension_overbought": RiskSeverity.MEDIUM,
    "overextension_oversold": RiskSeverity.MEDIUM,
    "drawdown_concern": RiskSeverity.HIGH,
    "trend_momentum_conflict": RiskSeverity.MEDIUM,
    "weak_trend_confirmation": RiskSeverity.LOW,
    "insufficient_history": RiskSeverity.LOW,
}

_DISSENTING_STANCE_BY_ACTION = {
    InvestmentAction.BUY: AgentStance.OPPOSE,
    InvestmentAction.SELL: AgentStance.SUPPORT,
}


class DeterministicCommitteeChair:
    """Combines the three analyst reports into one InvestmentDecision."""

    role = CommitteeRole.COMMITTEE_CHAIR

    def decide(
        self,
        asset: Asset,
        reports: Sequence[AgentReport],
        generated_at: datetime,
    ) -> InvestmentDecision:
        reports = tuple(reports)
        _validate_role_coverage(reports)
        reports_by_role = {report.role: report for report in reports}
        technical = reports_by_role[CommitteeRole.TECHNICAL_ANALYST]
        risk = reports_by_role[CommitteeRole.RISK_MANAGER]
        devils_advocate = reports_by_role[CommitteeRole.DEVILS_ADVOCATE]

        baseline = _baseline_action(technical.stance, risk.stance, devils_advocate.stance)
        risks = _extract_risks(reports)

        action, downgraded = _apply_high_risk_downgrade(baseline, risks)
        action, floored = _apply_confidence_floor(action, technical, risk, devils_advocate)

        dissenting_opinions = _dissenting_opinions(action, reports_by_role)
        confidence = _decision_confidence(
            downgraded=downgraded,
            floored=floored,
            dissenting_count=len(dissenting_opinions),
            technical=technical,
            risk=risk,
            devils_advocate=devils_advocate,
        )
        summary = _summary(
            asset, action, baseline, downgraded, floored, technical, risk, devils_advocate, confidence
        )

        return InvestmentDecision(
            asset=asset,
            action=action,
            confidence=confidence,
            summary=summary,
            agent_reports=reports,
            dissenting_opinions=dissenting_opinions,
            risks=risks,
            generated_at=generated_at,
        )


def _validate_role_coverage(reports: Sequence[AgentReport]) -> None:
    roles = [report.role for report in reports]
    if len(reports) != len(ANALYST_ROLES) or set(roles) != ANALYST_ROLES:
        raise CommitteeEngineError(
            "DeterministicCommitteeChair.decide requires exactly one AgentReport per analyst role"
        )


def _baseline_action(technical: AgentStance, risk: AgentStance, devils_advocate: AgentStance) -> InvestmentAction:
    """Step 1 - the 27-row decision matrix, expressed as two ordered rules.

    See docs/specs/spec-003-deterministic-committee-engine.md, section 5.1.
    """
    if technical == AgentStance.SUPPORT and risk == AgentStance.SUPPORT and devils_advocate != AgentStance.OPPOSE:
        return InvestmentAction.BUY
    if technical == AgentStance.OPPOSE and risk != AgentStance.SUPPORT and devils_advocate != AgentStance.SUPPORT:
        return InvestmentAction.SELL
    return InvestmentAction.HOLD


def _extract_risks(reports: Sequence[AgentReport]) -> list[RiskItem]:
    """Step 5.4 - RiskItems from a closed evidence-key registry, never from prose."""
    risks: list[RiskItem] = []
    for report in reports:
        for evidence in report.evidence:
            severity = _risk_severity(evidence.key)
            if severity is None:
                continue
            risks.append(
                RiskItem(
                    key=evidence.key,
                    severity=severity,
                    description=evidence.interpretation,
                    source_role=report.role,
                )
            )
    return risks


def _risk_severity(evidence_key: str) -> RiskSeverity | None:
    if evidence_key in RISK_EVIDENCE_SEVERITY:
        return RISK_EVIDENCE_SEVERITY[evidence_key]
    if evidence_key.endswith(_UNAVAILABLE_SUFFIX):
        return RiskSeverity.LOW
    return None


def _apply_high_risk_downgrade(
    baseline: InvestmentAction, risks: Sequence[RiskItem]
) -> tuple[InvestmentAction, bool]:
    """Step 2 - a HIGH-severity risk downgrades a baseline BUY to HOLD, one level, never below HOLD."""
    if baseline == InvestmentAction.BUY and any(risk.severity == RiskSeverity.HIGH for risk in risks):
        return InvestmentAction.HOLD, True
    return baseline, False


def _apply_confidence_floor(
    action: InvestmentAction,
    technical: AgentReport,
    risk: AgentReport,
    devils_advocate: AgentReport,
) -> tuple[InvestmentAction, bool]:
    """Step 3 - force HOLD if any analyst's confidence is below the 0.50 floor."""
    if action not in (InvestmentAction.BUY, InvestmentAction.SELL):
        return action, False
    minimum_confidence = min(technical.confidence, risk.confidence, devils_advocate.confidence)
    if minimum_confidence < _CONFIDENCE_FLOOR:
        return InvestmentAction.HOLD, True
    return action, False


def _dissenting_opinions(
    action: InvestmentAction, reports_by_role: Mapping[CommitteeRole, AgentReport]
) -> list[AgentReport]:
    """Step 5.5 - dissent computed explicitly from the final action, never inferred elsewhere."""
    ordered_reports = [
        reports_by_role[CommitteeRole.TECHNICAL_ANALYST],
        reports_by_role[CommitteeRole.RISK_MANAGER],
        reports_by_role[CommitteeRole.DEVILS_ADVOCATE],
    ]
    if action == InvestmentAction.HOLD:
        return [report for report in ordered_reports if report.stance != AgentStance.NEUTRAL]
    dissenting_stance = _DISSENTING_STANCE_BY_ACTION[action]
    return [report for report in ordered_reports if report.stance == dissenting_stance]


def _decision_confidence(
    *,
    downgraded: bool,
    floored: bool,
    dissenting_count: int,
    technical: AgentReport,
    risk: AgentReport,
    devils_advocate: AgentReport,
) -> Decimal:
    """Step 5.6 - discrete, count-based decision confidence with fixed overrides."""
    if downgraded:
        return _DOWNGRADE_CONFIDENCE
    if floored:
        return min(technical.confidence, risk.confidence, devils_advocate.confidence)
    return _DISSENT_CONFIDENCE[dissenting_count]


def _summary(
    asset: Asset,
    action: InvestmentAction,
    baseline: InvestmentAction,
    downgraded: bool,
    floored: bool,
    technical: AgentReport,
    risk: AgentReport,
    devils_advocate: AgentReport,
    confidence: Decimal,
) -> str:
    stances = (
        f"Technical: {technical.stance.value}, Risk: {risk.stance.value}, "
        f"Devil's Advocate: {devils_advocate.stance.value}."
    )
    if downgraded:
        override = f" Downgraded from {baseline.value} (Step 1 baseline) due to a HIGH-severity risk."
    elif floored:
        override = (
            f" Reduced from {baseline.value} (Step 1 baseline) because an analyst's "
            f"confidence was below the {_CONFIDENCE_FLOOR} floor."
        )
    else:
        override = ""
    return f"{action.value} {asset.ticker}: {stances}{override} Decision confidence: {confidence}."
