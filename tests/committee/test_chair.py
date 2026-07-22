"""Tests for investment_committee.committee.chair."""

from __future__ import annotations

from decimal import Decimal

import pytest

from investment_committee.committee.chair import DeterministicCommitteeChair
from investment_committee.committee.errors import CommitteeEngineError
from investment_committee.committee.models import (
    AgentStance,
    CommitteeRole,
    EvidenceItem,
    InvestmentAction,
    RiskSeverity,
)

from ._support import UTC_NOW, make_asset, make_report

CHAIR = DeterministicCommitteeChair()

S, N, O = AgentStance.SUPPORT, AgentStance.NEUTRAL, AgentStance.OPPOSE

# The complete 27-row decision matrix from
# docs/specs/spec-003-deterministic-committee-engine.md, section 5.1.
_MATRIX_ROWS = [
    (S, S, S, InvestmentAction.BUY),
    (S, S, N, InvestmentAction.BUY),
    (S, S, O, InvestmentAction.HOLD),
    (S, N, S, InvestmentAction.HOLD),
    (S, N, N, InvestmentAction.HOLD),
    (S, N, O, InvestmentAction.HOLD),
    (S, O, S, InvestmentAction.HOLD),
    (S, O, N, InvestmentAction.HOLD),
    (S, O, O, InvestmentAction.HOLD),
    (N, S, S, InvestmentAction.HOLD),
    (N, S, N, InvestmentAction.HOLD),
    (N, S, O, InvestmentAction.HOLD),
    (N, N, S, InvestmentAction.HOLD),
    (N, N, N, InvestmentAction.HOLD),
    (N, N, O, InvestmentAction.HOLD),
    (N, O, S, InvestmentAction.HOLD),
    (N, O, N, InvestmentAction.HOLD),
    (N, O, O, InvestmentAction.HOLD),
    (O, S, S, InvestmentAction.HOLD),
    (O, S, N, InvestmentAction.HOLD),
    (O, S, O, InvestmentAction.HOLD),
    (O, N, S, InvestmentAction.HOLD),
    (O, N, N, InvestmentAction.SELL),
    (O, N, O, InvestmentAction.SELL),
    (O, O, S, InvestmentAction.HOLD),
    (O, O, N, InvestmentAction.SELL),
    (O, O, O, InvestmentAction.SELL),
]

assert len(_MATRIX_ROWS) == 27
assert sum(1 for row in _MATRIX_ROWS if row[3] == InvestmentAction.BUY) == 2
assert sum(1 for row in _MATRIX_ROWS if row[3] == InvestmentAction.SELL) == 4
assert sum(1 for row in _MATRIX_ROWS if row[3] == InvestmentAction.HOLD) == 21


def _reports(t: AgentStance, r: AgentStance, d: AgentStance, confidence: Decimal = Decimal("0.90")):
    asset = make_asset()
    return [
        make_report(CommitteeRole.TECHNICAL_ANALYST, t, confidence, asset=asset),
        make_report(CommitteeRole.RISK_MANAGER, r, confidence, asset=asset),
        make_report(CommitteeRole.DEVILS_ADVOCATE, d, confidence, asset=asset),
    ]


@pytest.mark.parametrize("t, r, d, expected", _MATRIX_ROWS)
def test_decision_matrix_all_27_rows(t, r, d, expected) -> None:
    reports = _reports(t, r, d)
    asset = reports[0].asset

    decision = CHAIR.decide(asset=asset, reports=reports, generated_at=UTC_NOW)

    assert decision.action == expected


def test_no_buy_or_sell_row_ever_has_a_dissenter() -> None:
    """By construction (spec-003, section 5.6), every BUY/SELL matrix row is unanimous."""
    for t, r, d, expected in _MATRIX_ROWS:
        if expected not in (InvestmentAction.BUY, InvestmentAction.SELL):
            continue
        reports = _reports(t, r, d)
        decision = CHAIR.decide(asset=reports[0].asset, reports=reports, generated_at=UTC_NOW)
        assert decision.dissenting_opinions == ()
        assert decision.confidence == Decimal("0.90")  # dissenting_count == 0 row


# -- Step 2: high-risk downgrade ---------------------------------------------


def _high_risk_evidence() -> list[EvidenceItem]:
    return [EvidenceItem(key="maximum_drawdown_severe", value=Decimal("-0.35"), interpretation="severe drawdown")]


def test_high_severity_risk_downgrades_buy_to_hold() -> None:
    asset = make_asset()
    reports = [
        make_report(CommitteeRole.TECHNICAL_ANALYST, S, Decimal("0.90"), asset=asset),
        make_report(
            CommitteeRole.RISK_MANAGER, S, Decimal("0.90"), asset=asset, evidence=_high_risk_evidence()
        ),
        make_report(CommitteeRole.DEVILS_ADVOCATE, S, Decimal("0.90"), asset=asset),
    ]

    decision = CHAIR.decide(asset=asset, reports=reports, generated_at=UTC_NOW)

    assert decision.action == InvestmentAction.HOLD
    assert decision.confidence == Decimal("0.60")
    assert any(risk.severity == RiskSeverity.HIGH for risk in decision.risks)


def test_medium_or_low_risk_does_not_downgrade_buy() -> None:
    asset = make_asset()
    medium_evidence = [
        EvidenceItem(key="annualized_volatility_moderate", value=Decimal("0.25"), interpretation="moderate volatility")
    ]
    reports = [
        make_report(CommitteeRole.TECHNICAL_ANALYST, S, Decimal("0.90"), asset=asset),
        make_report(
            CommitteeRole.RISK_MANAGER, S, Decimal("0.90"), asset=asset, evidence=medium_evidence
        ),
        make_report(CommitteeRole.DEVILS_ADVOCATE, S, Decimal("0.90"), asset=asset),
    ]

    decision = CHAIR.decide(asset=asset, reports=reports, generated_at=UTC_NOW)

    assert decision.action == InvestmentAction.BUY


def test_high_risk_never_downgrades_a_baseline_hold() -> None:
    asset = make_asset()
    reports = [
        make_report(CommitteeRole.TECHNICAL_ANALYST, S, Decimal("0.90"), asset=asset),
        make_report(
            CommitteeRole.RISK_MANAGER, N, Decimal("0.90"), asset=asset, evidence=_high_risk_evidence()
        ),
        make_report(CommitteeRole.DEVILS_ADVOCATE, S, Decimal("0.90"), asset=asset),
    ]

    decision = CHAIR.decide(asset=asset, reports=reports, generated_at=UTC_NOW)

    assert decision.action == InvestmentAction.HOLD  # baseline was already HOLD, not downgraded from BUY


def test_high_risk_never_turns_a_baseline_sell_into_hold() -> None:
    asset = make_asset()
    reports = [
        make_report(CommitteeRole.TECHNICAL_ANALYST, O, Decimal("0.90"), asset=asset),
        make_report(
            CommitteeRole.RISK_MANAGER, N, Decimal("0.90"), asset=asset, evidence=_high_risk_evidence()
        ),
        make_report(CommitteeRole.DEVILS_ADVOCATE, N, Decimal("0.90"), asset=asset),
    ]

    decision = CHAIR.decide(asset=asset, reports=reports, generated_at=UTC_NOW)

    assert decision.action == InvestmentAction.SELL


# -- Step 3: confidence floor --------------------------------------------------


def test_confidence_below_floor_forces_hold_from_buy() -> None:
    asset = make_asset()
    reports = [
        make_report(CommitteeRole.TECHNICAL_ANALYST, S, Decimal("0.90"), asset=asset),
        make_report(CommitteeRole.RISK_MANAGER, S, Decimal("0.49"), asset=asset),
        make_report(CommitteeRole.DEVILS_ADVOCATE, S, Decimal("0.90"), asset=asset),
    ]

    decision = CHAIR.decide(asset=asset, reports=reports, generated_at=UTC_NOW)

    assert decision.action == InvestmentAction.HOLD
    assert decision.confidence == Decimal("0.49")


def test_confidence_exactly_at_floor_does_not_force_hold() -> None:
    asset = make_asset()
    reports = [
        make_report(CommitteeRole.TECHNICAL_ANALYST, S, Decimal("0.50"), asset=asset),
        make_report(CommitteeRole.RISK_MANAGER, S, Decimal("0.90"), asset=asset),
        make_report(CommitteeRole.DEVILS_ADVOCATE, S, Decimal("0.90"), asset=asset),
    ]

    decision = CHAIR.decide(asset=asset, reports=reports, generated_at=UTC_NOW)

    assert decision.action == InvestmentAction.BUY


def test_confidence_below_floor_forces_hold_from_sell() -> None:
    asset = make_asset()
    reports = [
        make_report(CommitteeRole.TECHNICAL_ANALYST, O, Decimal("0.90"), asset=asset),
        make_report(CommitteeRole.RISK_MANAGER, N, Decimal("0.30"), asset=asset),
        make_report(CommitteeRole.DEVILS_ADVOCATE, N, Decimal("0.90"), asset=asset),
    ]

    decision = CHAIR.decide(asset=asset, reports=reports, generated_at=UTC_NOW)

    assert decision.action == InvestmentAction.HOLD
    assert decision.confidence == Decimal("0.30")


# -- Risk extraction ------------------------------------------------------------


def test_registry_exact_match_severity() -> None:
    asset = make_asset()
    reports = [
        make_report(CommitteeRole.TECHNICAL_ANALYST, N, asset=asset),
        make_report(
            CommitteeRole.RISK_MANAGER,
            N,
            asset=asset,
            evidence=[
                EvidenceItem(key="annualized_volatility_high", value=Decimal("0.5"), interpretation="high vol"),
                EvidenceItem(key="weak_trend_confirmation", value="x", interpretation="weak trend"),
            ],
        ),
        make_report(CommitteeRole.DEVILS_ADVOCATE, N, asset=asset),
    ]

    decision = CHAIR.decide(asset=asset, reports=reports, generated_at=UTC_NOW)
    risks_by_key = {risk.key: risk for risk in decision.risks}

    assert risks_by_key["annualized_volatility_high"].severity == RiskSeverity.HIGH
    assert risks_by_key["weak_trend_confirmation"].severity == RiskSeverity.LOW


def test_unavailable_suffix_maps_to_low_severity() -> None:
    asset = make_asset()
    reports = [
        make_report(
            CommitteeRole.TECHNICAL_ANALYST,
            N,
            asset=asset,
            evidence=[EvidenceItem(key="return_ytd_positive_unavailable", value="insufficient_history", interpretation="n/a")],
        ),
        make_report(CommitteeRole.RISK_MANAGER, N, asset=asset),
        make_report(CommitteeRole.DEVILS_ADVOCATE, N, asset=asset),
    ]

    decision = CHAIR.decide(asset=asset, reports=reports, generated_at=UTC_NOW)

    assert decision.risks[0].key == "return_ytd_positive_unavailable"
    assert decision.risks[0].severity == RiskSeverity.LOW


def test_two_roles_flagging_same_key_both_survive_as_distinct_risk_items() -> None:
    asset = make_asset()
    shared_evidence = EvidenceItem(key="drawdown_concern", value=Decimal("-0.4"), interpretation="severe")
    reports = [
        make_report(CommitteeRole.TECHNICAL_ANALYST, N, asset=asset),
        make_report(CommitteeRole.RISK_MANAGER, N, asset=asset, evidence=[shared_evidence]),
        make_report(CommitteeRole.DEVILS_ADVOCATE, N, asset=asset, evidence=[shared_evidence]),
    ]

    decision = CHAIR.decide(asset=asset, reports=reports, generated_at=UTC_NOW)

    assert len(decision.risks) == 2
    assert {risk.source_role for risk in decision.risks} == {
        CommitteeRole.RISK_MANAGER,
        CommitteeRole.DEVILS_ADVOCATE,
    }


def test_technical_analyst_evidence_key_produces_no_risk_item() -> None:
    asset = make_asset()
    reports = [
        make_report(
            CommitteeRole.TECHNICAL_ANALYST,
            N,
            asset=asset,
            evidence=[EvidenceItem(key="price_above_sma20", value=Decimal("100"), interpretation="bearish read")],
        ),
        make_report(CommitteeRole.RISK_MANAGER, N, asset=asset),
        make_report(CommitteeRole.DEVILS_ADVOCATE, N, asset=asset),
    ]

    decision = CHAIR.decide(asset=asset, reports=reports, generated_at=UTC_NOW)

    assert decision.risks == ()


# -- Dissenting opinions ---------------------------------------------------------


def test_dissenting_opinions_for_buy() -> None:
    reports = _reports(S, S, N)  # BUY baseline

    decision = CHAIR.decide(asset=reports[0].asset, reports=reports, generated_at=UTC_NOW)

    assert decision.action == InvestmentAction.BUY
    assert decision.dissenting_opinions == ()


def test_dissenting_opinions_for_hold_lists_every_non_neutral_report() -> None:
    asset = make_asset()
    reports = [
        make_report(CommitteeRole.TECHNICAL_ANALYST, S, Decimal("0.90"), asset=asset),
        make_report(CommitteeRole.RISK_MANAGER, O, Decimal("0.90"), asset=asset),
        make_report(CommitteeRole.DEVILS_ADVOCATE, N, Decimal("0.90"), asset=asset),
    ]

    decision = CHAIR.decide(asset=asset, reports=reports, generated_at=UTC_NOW)

    assert decision.action == InvestmentAction.HOLD
    dissenting_roles = {report.role for report in decision.dissenting_opinions}
    assert dissenting_roles == {CommitteeRole.TECHNICAL_ANALYST, CommitteeRole.RISK_MANAGER}


def test_dissenting_opinions_for_sell() -> None:
    reports = _reports(O, N, N)  # SELL baseline

    decision = CHAIR.decide(asset=reports[0].asset, reports=reports, generated_at=UTC_NOW)

    assert decision.action == InvestmentAction.SELL
    assert decision.dissenting_opinions == ()


# -- Decision confidence table ---------------------------------------------------


@pytest.mark.parametrize(
    "stances, expected_confidence",
    [
        ((N, N, N), Decimal("0.90")),  # dissenting_count == 0 (unanimous NEUTRAL HOLD)
        ((S, N, N), Decimal("0.70")),  # dissenting_count == 1
        ((S, O, N), Decimal("0.50")),  # dissenting_count == 2
        ((S, O, S), Decimal("0.30")),  # dissenting_count == 3
    ],
)
def test_decision_confidence_by_dissenting_count(stances, expected_confidence) -> None:
    t, r, d = stances
    reports = _reports(t, r, d)

    decision = CHAIR.decide(asset=reports[0].asset, reports=reports, generated_at=UTC_NOW)

    assert decision.action == InvestmentAction.HOLD
    assert decision.confidence == expected_confidence


# -- Malformed input --------------------------------------------------------------


def test_missing_a_role_raises_committee_engine_error() -> None:
    asset = make_asset()
    reports = [
        make_report(CommitteeRole.TECHNICAL_ANALYST, S, asset=asset),
        make_report(CommitteeRole.RISK_MANAGER, S, asset=asset),
    ]

    with pytest.raises(CommitteeEngineError):
        CHAIR.decide(asset=asset, reports=reports, generated_at=UTC_NOW)


def test_duplicate_role_raises_committee_engine_error() -> None:
    asset = make_asset()
    reports = [
        make_report(CommitteeRole.TECHNICAL_ANALYST, S, asset=asset),
        make_report(CommitteeRole.TECHNICAL_ANALYST, S, asset=asset),
        make_report(CommitteeRole.DEVILS_ADVOCATE, S, asset=asset),
    ]

    with pytest.raises(CommitteeEngineError):
        CHAIR.decide(asset=asset, reports=reports, generated_at=UTC_NOW)


def test_report_for_a_different_asset_raises_value_error() -> None:
    committee_asset = make_asset("AAPL")
    other_asset = make_asset("MSFT")
    reports = [
        make_report(CommitteeRole.TECHNICAL_ANALYST, S, asset=other_asset),
        make_report(CommitteeRole.RISK_MANAGER, S, asset=committee_asset),
        make_report(CommitteeRole.DEVILS_ADVOCATE, S, asset=committee_asset),
    ]

    with pytest.raises(ValueError):
        CHAIR.decide(asset=committee_asset, reports=reports, generated_at=UTC_NOW)


def test_generated_at_is_preserved_on_the_decision() -> None:
    reports = _reports(N, N, N)

    decision = CHAIR.decide(asset=reports[0].asset, reports=reports, generated_at=UTC_NOW)

    assert decision.generated_at == UTC_NOW
