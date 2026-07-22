"""Internal helpers shared by the deterministic analysts.

Not part of the committee package's public API — see
`committee/analysts/__init__.py` for what is actually exported. Factored
out because the Rule Strength Table and the SUPPORT/NEUTRAL/OPPOSE lookup
are identical, literal duplicates between `DeterministicTechnicalAnalyst`
and `DeterministicRiskManager` — see
docs/specs/spec-003-deterministic-committee-engine.md, "Shared analyst
conventions."
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import NamedTuple, Sequence

from ..models import AgentStance, EvidenceItem

_MIN_APPLICABLE_FOR_SINGLE_DISSENT = 4


class RuleOutcome(Enum):
    """One checklist rule's result: PASS, FAIL, or NOT_APPLICABLE (missing data)."""

    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class RuleEvaluation(NamedTuple):
    """One evaluated checklist rule, ready to feed into evidence and reasoning."""

    label: str
    outcome: RuleOutcome
    evidence: EvidenceItem
    detail: str


def unavailable_evidence(key: str) -> EvidenceItem:
    """The fixed evidence item emitted when a rule's required indicator(s) are None."""
    return EvidenceItem(
        key=f"{key}_unavailable",
        value="insufficient_history",
        interpretation=f"{key} could not be evaluated: insufficient history",
    )


def count_outcomes(rules: Sequence[RuleEvaluation]) -> tuple[int, int, int]:
    """Return (passed, failed, not_applicable) counts for `rules`."""
    passed = sum(1 for rule in rules if rule.outcome is RuleOutcome.PASS)
    failed = sum(1 for rule in rules if rule.outcome is RuleOutcome.FAIL)
    na = sum(1 for rule in rules if rule.outcome is RuleOutcome.NOT_APPLICABLE)
    return passed, failed, na


def join_rule_details(rules: Sequence[RuleEvaluation]) -> str:
    """Render every rule's label and detail as one comma-separated reasoning clause."""
    return ", ".join(f"{rule.label} {rule.detail}" for rule in rules)


def checklist_stance(passed: int, failed: int, applicable: int) -> tuple[AgentStance, str]:
    """Shared SUPPORT/NEUTRAL/OPPOSE lookup for Technical Analyst and Risk Manager.

    See docs/specs/spec-003-deterministic-committee-engine.md, sections 2-3
    ("Stance table"). Returns the stance plus a short reason clause for use
    in `reasoning` text.
    """
    if applicable == 0:
        return AgentStance.NEUTRAL, "no rule could be evaluated"
    if failed == 0:
        return AgentStance.SUPPORT, "every applicable rule passed"
    if failed == 1 and applicable >= _MIN_APPLICABLE_FOR_SINGLE_DISSENT:
        return AgentStance.SUPPORT, "at most one applicable rule failed"
    if passed == 0:
        return AgentStance.OPPOSE, "every applicable rule failed"
    if passed == 1 and applicable >= _MIN_APPLICABLE_FOR_SINGLE_DISSENT:
        return AgentStance.OPPOSE, "at most one applicable rule passed"
    return AgentStance.NEUTRAL, "the applicable rules were mixed, with no clear majority"


def rule_strength_confidence(passed: int, failed: int, na: int) -> Decimal:
    """Shared Rule Strength Table for Technical Analyst and Risk Manager.

    See docs/specs/spec-003-deterministic-committee-engine.md, "The Rule
    Strength Table." Evaluated top to bottom; the first matching row wins.
    """
    applicable = passed + failed
    if applicable == 0:
        return Decimal("0.00")
    if failed == 0 and na == 0:
        return Decimal("0.90")
    if failed == 0 and na > 0:
        return Decimal("0.75")
    if failed == 1:
        return Decimal("0.75")
    if failed == 2:
        return Decimal("0.60")
    if failed >= 3 and passed >= failed:
        return Decimal("0.50")
    if 0 < passed < failed:
        return Decimal("0.35")
    return Decimal("0.20")  # passed == 0 and applicable > 0
