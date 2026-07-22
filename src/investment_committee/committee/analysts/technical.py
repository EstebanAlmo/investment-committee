"""DeterministicTechnicalAnalyst: a fixed 7-rule checklist read of trend, momentum, and performance.

See docs/specs/spec-003-deterministic-committee-engine.md, section 2.
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

_RSI_HEALTHY_LOW = Decimal("45")
_RSI_HEALTHY_HIGH = Decimal("70")
_TOTAL_RULES = 7


class DeterministicTechnicalAnalyst(Analyst):
    """Reads trend, momentum, and performance from an IndicatorSnapshot via a fixed 7-rule checklist."""

    role = CommitteeRole.TECHNICAL_ANALYST

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
                interpretation=f"{applicable} of {_TOTAL_RULES} technical rules were evaluable",
            )
        )

        reasoning = (
            f"Technical rules: {join_rule_details(rules)}. "
            f"{applicable} of {_TOTAL_RULES} rules evaluable; {passed} passed, {failed} failed. "
            f"Stance is {stance.value} because {stance_reason}."
        )

        return AgentReport(
            asset=snapshot.asset,
            role=self.role,
            stance=stance,
            confidence=confidence,
            summary=f"{stance.value}: {passed}/{applicable} applicable technical rules passed ({na} not applicable).",
            reasoning=reasoning,
            evidence=evidence,
            generated_at=generated_at,
        )


def _evaluate_rules(snapshot: IndicatorSnapshot) -> list[RuleEvaluation]:
    return [
        _price_above_sma20(snapshot),
        _sma20_above_sma50(snapshot),
        _sma50_above_sma200(snapshot),
        _rsi14_healthy_band(snapshot),
        _return_positive_rule(snapshot.return_30d, key="return_30d_positive", label="P1 Return30"),
        _return_positive_rule(snapshot.return_90d, key="return_90d_positive", label="P2 Return90"),
        _return_positive_rule(snapshot.return_ytd, key="return_ytd_positive", label="P3 ReturnYTD"),
    ]


def _price_above_sma20(snapshot: IndicatorSnapshot) -> RuleEvaluation:
    key, label = "price_above_sma20", "T1 Price>SMA20"
    close, sma20 = snapshot.latest_close, snapshot.sma_20
    if close is None or sma20 is None:
        return RuleEvaluation(label, RuleOutcome.NOT_APPLICABLE, unavailable_evidence(key), "N/A (insufficient history)")
    if close > sma20:
        evidence = EvidenceItem(key=key, value=close, interpretation="price is above its 20-day average")
        return RuleEvaluation(label, RuleOutcome.PASS, evidence, "PASS")
    evidence = EvidenceItem(key=key, value=close, interpretation="price is not above its 20-day average")
    return RuleEvaluation(label, RuleOutcome.FAIL, evidence, "FAIL")


def _sma20_above_sma50(snapshot: IndicatorSnapshot) -> RuleEvaluation:
    key, label = "sma20_above_sma50", "T2 SMA20>SMA50"
    sma20, sma50 = snapshot.sma_20, snapshot.sma_50
    if sma20 is None or sma50 is None:
        return RuleEvaluation(label, RuleOutcome.NOT_APPLICABLE, unavailable_evidence(key), "N/A (insufficient history)")
    if sma20 > sma50:
        evidence = EvidenceItem(key=key, value=sma20, interpretation="SMA20 is above SMA50")
        return RuleEvaluation(label, RuleOutcome.PASS, evidence, "PASS")
    evidence = EvidenceItem(key=key, value=sma20, interpretation="SMA20 is not above SMA50")
    return RuleEvaluation(label, RuleOutcome.FAIL, evidence, "FAIL")


def _sma50_above_sma200(snapshot: IndicatorSnapshot) -> RuleEvaluation:
    key, label = "sma50_above_sma200", "T3 SMA50>SMA200"
    sma50, sma200 = snapshot.sma_50, snapshot.sma_200
    if sma50 is None or sma200 is None:
        return RuleEvaluation(label, RuleOutcome.NOT_APPLICABLE, unavailable_evidence(key), "N/A (insufficient history)")
    if sma50 > sma200:
        evidence = EvidenceItem(key=key, value=sma50, interpretation="SMA50 is above SMA200")
        return RuleEvaluation(label, RuleOutcome.PASS, evidence, "PASS")
    evidence = EvidenceItem(key=key, value=sma50, interpretation="SMA50 is not above SMA200")
    return RuleEvaluation(label, RuleOutcome.FAIL, evidence, "FAIL")


def _rsi14_healthy_band(snapshot: IndicatorSnapshot) -> RuleEvaluation:
    key, label = "rsi14_healthy_band", "M1 RSI healthy band"
    rsi = snapshot.rsi_14
    if rsi is None:
        return RuleEvaluation(label, RuleOutcome.NOT_APPLICABLE, unavailable_evidence(key), "N/A (insufficient history)")
    if _RSI_HEALTHY_LOW <= rsi <= _RSI_HEALTHY_HIGH:
        evidence = EvidenceItem(key=key, value=rsi, interpretation="RSI14 is within the healthy bullish band (45-70)")
        return RuleEvaluation(label, RuleOutcome.PASS, evidence, "PASS")
    evidence = EvidenceItem(key=key, value=rsi, interpretation="RSI14 is outside the healthy bullish band (45-70)")
    return RuleEvaluation(label, RuleOutcome.FAIL, evidence, "FAIL")


def _return_positive_rule(value: Decimal | None, *, key: str, label: str) -> RuleEvaluation:
    if value is None:
        return RuleEvaluation(label, RuleOutcome.NOT_APPLICABLE, unavailable_evidence(key), "N/A (insufficient history)")
    if value > 0:
        evidence = EvidenceItem(key=key, value=value, interpretation="return is positive")
        return RuleEvaluation(label, RuleOutcome.PASS, evidence, "PASS")
    evidence = EvidenceItem(key=key, value=value, interpretation="return is not positive")
    return RuleEvaluation(label, RuleOutcome.FAIL, evidence, "FAIL")
