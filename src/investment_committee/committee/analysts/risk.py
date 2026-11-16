"""DeterministicRiskManager: a fixed 4-rule checklist read of risk profile.

See docs/specs/spec-003-deterministic-committee-engine.md, section 3.

Each rule carries two independent classifications computed from the same
comparison: a checklist gate (PASS/FAIL/N-A, used for this analyst's own
stance and confidence) and a finer evidence band (low/moderate/high or
similar, always recorded, used later by the chair's risk-extraction
registry). See the spec section for why these are kept separate.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from investment_committee.indicators import IndicatorSnapshot

from ..models import AgentReport, CommitteeRole, EvidenceItem
from ._shared import (
    RuleEvaluation,
    RuleOutcome,
    checklist_stance,
    count_outcomes,
    join_rule_details,
    rule_strength_confidence,
    unavailable_evidence,
)
from .base import Analyst

_VOLATILITY_LOW = Decimal("0.20")
_VOLATILITY_HIGH = Decimal("0.40")
_DRAWDOWN_SHALLOW = Decimal("-0.10")
_DRAWDOWN_SEVERE = Decimal("-0.30")
_RSI_STABLE_LOW = Decimal("30")
_RSI_STABLE_HIGH = Decimal("70")
_RETURN_CONTAINED = Decimal("0.10")
_RETURN_ELEVATED = Decimal("0.25")
_TOTAL_RULES = 4


class DeterministicRiskManager(Analyst):
    """Reads risk profile from an IndicatorSnapshot via a fixed 4-rule checklist."""

    role = CommitteeRole.RISK_MANAGER

    def analyze(self, snapshot: IndicatorSnapshot, generated_at: datetime) -> AgentReport:
        rules = _evaluate_rules(snapshot)
        passed, failed, na = count_outcomes(rules)
        applicable = passed + failed

        stance, stance_reason = checklist_stance(passed=passed, failed=failed, applicable=applicable)
        confidence = rule_strength_confidence(passed=passed, failed=failed, na=na)

        evidence = [rule.evidence for rule in rules]
        evidence.append(
            EvidenceItem(
                key="checklist_coverage",
                value=f"{applicable}/{_TOTAL_RULES}",
                interpretation=f"{applicable} of {_TOTAL_RULES} risk rules were evaluable",
            )
        )

        reasoning = (
            f"Risk rules: {join_rule_details(rules)}. "
            f"{applicable} of {_TOTAL_RULES} rules evaluable; {passed} passed, {failed} failed. "
            f"Stance is {stance.value} because {stance_reason}."
        )

        return AgentReport(
            asset=snapshot.asset,
            role=self.role,
            stance=stance,
            confidence=confidence,
            summary=f"{stance.value}: {passed}/{applicable} applicable risk rules passed ({na} not applicable).",
            reasoning=reasoning,
            evidence=evidence,
            generated_at=generated_at,
        )


def _evaluate_rules(snapshot: IndicatorSnapshot) -> list[RuleEvaluation]:
    return [
        _volatility_contained(snapshot),
        _drawdown_contained(snapshot),
        _rsi_stable(snapshot),
        _short_term_move_contained(snapshot),
    ]


def _volatility_contained(snapshot: IndicatorSnapshot) -> RuleEvaluation:
    label = "R1 Volatility contained"
    value = snapshot.annualized_volatility
    if value is None:
        return RuleEvaluation(label, RuleOutcome.NOT_APPLICABLE, unavailable_evidence("annualized_volatility"), "N/A (insufficient history)")
    if value < _VOLATILITY_LOW:
        band = "low"
    elif value < _VOLATILITY_HIGH:
        band = "moderate"
    else:
        band = "high"
    outcome = RuleOutcome.FAIL if band == "high" else RuleOutcome.PASS
    evidence = EvidenceItem(key=f"annualized_volatility_{band}", value=value, interpretation=f"{band} annualized volatility")
    return RuleEvaluation(label, outcome, evidence, f"{outcome.value} ({band} band, {value})")


def _drawdown_contained(snapshot: IndicatorSnapshot) -> RuleEvaluation:
    label = "R2 Drawdown contained"
    value = snapshot.maximum_drawdown
    if value is None:
        return RuleEvaluation(label, RuleOutcome.NOT_APPLICABLE, unavailable_evidence("maximum_drawdown"), "N/A (insufficient history)")
    if value <= _DRAWDOWN_SEVERE:
        band = "severe"
    elif value > _DRAWDOWN_SHALLOW:
        band = "shallow"
    else:
        band = "moderate"
    outcome = RuleOutcome.FAIL if band == "severe" else RuleOutcome.PASS
    evidence = EvidenceItem(key=f"maximum_drawdown_{band}", value=value, interpretation=f"{band} maximum drawdown")
    return RuleEvaluation(label, outcome, evidence, f"{outcome.value} ({band} band, {value})")


def _rsi_stable(snapshot: IndicatorSnapshot) -> RuleEvaluation:
    label = "R3 RSI stable"
    value = snapshot.rsi_14
    if value is None:
        return RuleEvaluation(label, RuleOutcome.NOT_APPLICABLE, unavailable_evidence("rsi_14"), "N/A (insufficient history)")
    if _RSI_STABLE_LOW <= value <= _RSI_STABLE_HIGH:
        evidence = EvidenceItem(key="rsi_14_stable", value=value, interpretation="RSI14 is within the stable range (30-70)")
        return RuleEvaluation(label, RuleOutcome.PASS, evidence, f"PASS (stable, {value})")
    evidence = EvidenceItem(key="rsi_14_extreme", value=value, interpretation="RSI14 is outside the stable range (30-70)")
    return RuleEvaluation(label, RuleOutcome.FAIL, evidence, f"FAIL (extreme, {value})")


def _short_term_move_contained(snapshot: IndicatorSnapshot) -> RuleEvaluation:
    label = "R4 Short-term move contained"
    value = snapshot.return_30d
    if value is None:
        return RuleEvaluation(label, RuleOutcome.NOT_APPLICABLE, unavailable_evidence("return_30d"), "N/A (insufficient history)")
    magnitude = abs(value)
    if magnitude <= _RETURN_CONTAINED:
        band = "contained"
    elif magnitude <= _RETURN_ELEVATED:
        band = "elevated"
    else:
        band = "extreme"
    outcome = RuleOutcome.FAIL if band == "extreme" else RuleOutcome.PASS
    evidence = EvidenceItem(key=f"return_30d_{band}", value=value, interpretation=f"{band} 30-day return magnitude")
    return RuleEvaluation(label, outcome, evidence, f"{outcome.value} ({band} band, {value})")
