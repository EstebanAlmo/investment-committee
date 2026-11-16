"""DeterministicDevilsAdvocate: a fixed 5-check read of counterarguments.

Consumes only IndicatorSnapshot — never the Technical Analyst's or Risk
Manager's AgentReport. See docs/specs/spec-003-deterministic-committee-engine.md,
section 4, for the dependency-direction rationale.

Internally this module reuses `_shared.RuleOutcome` (PASS/FAIL/NOT_APPLICABLE)
to represent CLEAR/CONCERN/N-A: PASS means CLEAR (no concern), FAIL means
CONCERN. This analyst does not use the shared stance/confidence tables from
`_shared.py`, since its own PASS ("no concern") is not a directional
bullish/bearish signal the way the other two analysts' rules are — it gets
its own tables, defined below.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from investment_committee.indicators import IndicatorSnapshot

from ..models import AgentReport, AgentStance, CommitteeRole, EvidenceItem
from ._shared import RuleEvaluation, RuleOutcome, join_rule_details, unavailable_evidence
from .base import Analyst

_OVEREXTENSION_LOW = Decimal("25")
_OVEREXTENSION_HIGH = Decimal("75")
_SEVERE_DRAWDOWN = Decimal("-0.30")
_INSUFFICIENT_HISTORY_THRESHOLD = 3
_TOTAL_CHECKS = 5

_CONFIDENCE_BY_CONCERN_COUNT = {
    1: Decimal("0.60"),
    2: Decimal("0.50"),
}

_NAMED_INDICATORS = (
    "latest_close",
    "sma_20",
    "sma_50",
    "sma_200",
    "return_30d",
    "return_90d",
    "return_ytd",
    "rsi_14",
)


class DeterministicDevilsAdvocate(Analyst):
    """Surfaces credible counterarguments from an IndicatorSnapshot via a fixed 5-check checklist."""

    role = CommitteeRole.DEVILS_ADVOCATE

    def analyze(self, snapshot: IndicatorSnapshot, generated_at: datetime) -> AgentReport:
        checks = _evaluate_checks(snapshot)
        applicable = sum(1 for check in checks if check.outcome is not RuleOutcome.NOT_APPLICABLE)
        concern_count = sum(1 for check in checks if check.outcome is RuleOutcome.FAIL)

        stance = _stance(concern_count)
        confidence = _confidence(concern_count, applicable)

        evidence = [check.evidence for check in checks]

        reasoning = (
            f"Devil's Advocate checks: {join_rule_details(checks)}. "
            f"{applicable} of {_TOTAL_CHECKS} checks applicable; {concern_count} concern(s) raised. "
            f"Stance is {stance.value} because {_stance_reason(concern_count)}."
        )

        return AgentReport(
            asset=snapshot.asset,
            role=self.role,
            stance=stance,
            confidence=confidence,
            summary=f"{stance.value}: {concern_count} of {applicable} applicable checks raised a concern.",
            reasoning=reasoning,
            evidence=evidence,
            generated_at=generated_at,
        )


def _stance(concern_count: int) -> AgentStance:
    if concern_count == 0:
        return AgentStance.SUPPORT
    if concern_count == 1:
        return AgentStance.NEUTRAL
    return AgentStance.OPPOSE


def _stance_reason(concern_count: int) -> str:
    if concern_count == 0:
        return "no credible counterargument was found"
    if concern_count == 1:
        return "exactly one caution flag was raised"
    return "multiple credible counterarguments were found"


def _confidence(concern_count: int, applicable: int) -> Decimal:
    if concern_count == 0:
        return Decimal("0.90") if applicable == _TOTAL_CHECKS else Decimal("0.75")
    if concern_count >= 3:
        return Decimal("0.75")
    return _CONFIDENCE_BY_CONCERN_COUNT[concern_count]


def _evaluate_checks(snapshot: IndicatorSnapshot) -> list[RuleEvaluation]:
    return [
        _overextension(snapshot),
        _severe_drawdown(snapshot),
        _trend_momentum_conflict(snapshot),
        _weak_trend_confirmation(snapshot),
        _insufficient_history(snapshot),
    ]


def _overextension(snapshot: IndicatorSnapshot) -> RuleEvaluation:
    label = "D1 No overextension"
    rsi = snapshot.rsi_14
    if rsi is None:
        return RuleEvaluation(label, RuleOutcome.NOT_APPLICABLE, unavailable_evidence("overextension"), "N/A (insufficient history)")
    if rsi > _OVEREXTENSION_HIGH:
        evidence = EvidenceItem(key="overextension_overbought", value=rsi, interpretation="RSI14 indicates overbought overextension")
        return RuleEvaluation(label, RuleOutcome.FAIL, evidence, f"CONCERN (overbought, {rsi})")
    if rsi < _OVEREXTENSION_LOW:
        evidence = EvidenceItem(key="overextension_oversold", value=rsi, interpretation="RSI14 indicates oversold overextension")
        return RuleEvaluation(label, RuleOutcome.FAIL, evidence, f"CONCERN (oversold, {rsi})")
    evidence = EvidenceItem(key="rsi_14_within_range", value=rsi, interpretation="RSI14 is within the non-extended range (25-75)")
    return RuleEvaluation(label, RuleOutcome.PASS, evidence, f"CLEAR ({rsi})")


def _severe_drawdown(snapshot: IndicatorSnapshot) -> RuleEvaluation:
    label = "D2 No severe drawdown"
    drawdown = snapshot.maximum_drawdown
    if drawdown is None:
        return RuleEvaluation(label, RuleOutcome.NOT_APPLICABLE, unavailable_evidence("drawdown_concern"), "N/A (insufficient history)")
    if drawdown < _SEVERE_DRAWDOWN:
        evidence = EvidenceItem(key="drawdown_concern", value=drawdown, interpretation="maximum drawdown is severe")
        return RuleEvaluation(label, RuleOutcome.FAIL, evidence, f"CONCERN ({drawdown})")
    evidence = EvidenceItem(key="drawdown_contained", value=drawdown, interpretation="maximum drawdown is not severe")
    return RuleEvaluation(label, RuleOutcome.PASS, evidence, f"CLEAR ({drawdown})")


def _trend_momentum_conflict(snapshot: IndicatorSnapshot) -> RuleEvaluation:
    label = "D3 Trend/momentum aligned"
    close, sma200, return_30d = snapshot.latest_close, snapshot.sma_200, snapshot.return_30d
    if close is None or sma200 is None or return_30d is None:
        return RuleEvaluation(label, RuleOutcome.NOT_APPLICABLE, unavailable_evidence("trend_momentum_conflict"), "N/A (insufficient history)")
    conflict = (close > sma200 and return_30d < 0) or (close < sma200 and return_30d > 0)
    if conflict:
        evidence = EvidenceItem(key="trend_momentum_conflict", value=return_30d, interpretation="long-term trend and short-term momentum disagree")
        return RuleEvaluation(label, RuleOutcome.FAIL, evidence, f"CONCERN ({return_30d})")
    evidence = EvidenceItem(key="trend_momentum_aligned", value=return_30d, interpretation="long-term trend and short-term momentum agree")
    return RuleEvaluation(label, RuleOutcome.PASS, evidence, f"CLEAR ({return_30d})")


def _weak_trend_confirmation(snapshot: IndicatorSnapshot) -> RuleEvaluation:
    label = "D4 Trend confirmed by moving averages"
    sma20, sma50, sma200 = snapshot.sma_20, snapshot.sma_50, snapshot.sma_200
    if sma20 is None or sma50 is None or sma200 is None:
        return RuleEvaluation(label, RuleOutcome.NOT_APPLICABLE, unavailable_evidence("weak_trend_confirmation"), "N/A (insufficient history)")
    cleanly_ordered = (sma20 > sma50 > sma200) or (sma20 < sma50 < sma200)
    if not cleanly_ordered:
        evidence = EvidenceItem(key="weak_trend_confirmation", value="not cleanly ordered", interpretation="moving averages are not cleanly ordered")
        return RuleEvaluation(label, RuleOutcome.FAIL, evidence, "CONCERN")
    evidence = EvidenceItem(key="trend_confirmed", value="cleanly ordered", interpretation="moving averages are cleanly ordered")
    return RuleEvaluation(label, RuleOutcome.PASS, evidence, "CLEAR")


def _insufficient_history(snapshot: IndicatorSnapshot) -> RuleEvaluation:
    label = "D5 Sufficient history"
    missing = sum(1 for name in _NAMED_INDICATORS if getattr(snapshot, name) is None)
    if missing >= _INSUFFICIENT_HISTORY_THRESHOLD:
        evidence = EvidenceItem(key="insufficient_history", value=str(missing), interpretation=f"{missing} of 8 named indicators are unavailable")
        return RuleEvaluation(label, RuleOutcome.FAIL, evidence, f"CONCERN ({missing} missing)")
    evidence = EvidenceItem(key="sufficient_history", value=str(missing), interpretation=f"only {missing} of 8 named indicators are unavailable")
    return RuleEvaluation(label, RuleOutcome.PASS, evidence, f"CLEAR ({missing} missing)")
