"""Tests for investment_committee.committee.models."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

import pytest

from investment_committee.committee import (
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
import investment_committee.committee as committee
from investment_committee.domain import Asset, AssetType, RecommendationAction

UTC_NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


def make_asset(ticker: str = "AAPL") -> Asset:
    return Asset(
        ticker=ticker,
        name="Apple Inc.",
        asset_type=AssetType.STOCK,
        exchange="NASDAQ",
        currency="USD",
    )


def make_evidence_item(
    key: str = "rsi_14",
    value: Decimal | str = Decimal("72"),
    interpretation: str = "RSI above 70, overbought.",
) -> EvidenceItem:
    return EvidenceItem(key=key, value=value, interpretation=interpretation)


def make_agent_report(
    asset: Asset | None = None,
    role: CommitteeRole = CommitteeRole.TECHNICAL_ANALYST,
    stance: AgentStance = AgentStance.SUPPORT,
    confidence: Decimal = Decimal("0.7"),
    summary: str = "Momentum is strong.",
    reasoning: str = "Price is above all major moving averages.",
    evidence=None,
    generated_at: datetime = UTC_NOW,
) -> AgentReport:
    return AgentReport(
        asset=asset or make_asset(),
        role=role,
        stance=stance,
        confidence=confidence,
        summary=summary,
        reasoning=reasoning,
        evidence=[make_evidence_item()] if evidence is None else evidence,
        generated_at=generated_at,
    )


def make_risk_item(
    key: str = "concentration",
    severity: RiskSeverity = RiskSeverity.MEDIUM,
    description: str = "Position size is large relative to portfolio.",
    source_role: CommitteeRole = CommitteeRole.RISK_MANAGER,
) -> RiskItem:
    return RiskItem(key=key, severity=severity, description=description, source_role=source_role)


def make_agent_reports(asset: Asset | None = None) -> tuple[AgentReport, ...]:
    asset = asset or make_asset()
    return (
        make_agent_report(asset=asset, role=CommitteeRole.TECHNICAL_ANALYST),
        make_agent_report(asset=asset, role=CommitteeRole.RISK_MANAGER),
        make_agent_report(asset=asset, role=CommitteeRole.DEVILS_ADVOCATE),
    )


def make_investment_decision(
    asset: Asset | None = None,
    action: InvestmentAction = InvestmentAction.BUY,
    confidence: Decimal = Decimal("0.6"),
    summary: str = "Committee recommends a small position.",
    agent_reports=None,
    dissenting_opinions=(),
    risks=(),
    generated_at: datetime = UTC_NOW,
) -> InvestmentDecision:
    asset = asset or make_asset()
    reports = agent_reports if agent_reports is not None else make_agent_reports(asset)
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


# --- Public exports -----------------------------------------------------


def test_public_exports_match_approved_api() -> None:
    expected = {
        # spec-002: domain shapes
        "CommitteeRole",
        "ANALYST_ROLES",
        "AgentStance",
        "InvestmentAction",
        "EvidenceItem",
        "AgentReport",
        "RiskSeverity",
        "RiskItem",
        "InvestmentDecision",
        # spec-003: deterministic committee engine
        "Analyst",
        "DeterministicTechnicalAnalyst",
        "DeterministicRiskManager",
        "DeterministicDevilsAdvocate",
        "DeterministicCommitteeChair",
        "DeterministicCommitteeEngine",
        "CommitteeEngineError",
        "Clock",
    }
    assert set(committee.__all__) == expected
    for name in expected:
        assert hasattr(committee, name)


def test_investment_action_is_recommendation_action() -> None:
    assert InvestmentAction is RecommendationAction


# --- Enums ----------------------------------------------------------------


def test_committee_role_enum_values() -> None:
    assert {member.value for member in CommitteeRole} == {
        "TECHNICAL_ANALYST",
        "RISK_MANAGER",
        "DEVILS_ADVOCATE",
        "COMMITTEE_CHAIR",
    }


def test_agent_stance_enum_values() -> None:
    assert {member.value for member in AgentStance} == {"SUPPORT", "NEUTRAL", "OPPOSE"}


def test_risk_severity_enum_values() -> None:
    assert {member.value for member in RiskSeverity} == {"LOW", "MEDIUM", "HIGH"}


def test_enums_inherit_from_str_and_enum() -> None:
    for enum_cls in (CommitteeRole, AgentStance, RiskSeverity):
        assert issubclass(enum_cls, str)
        assert issubclass(enum_cls, Enum)


def test_analyst_roles_contents_and_immutability() -> None:
    assert ANALYST_ROLES == {
        CommitteeRole.TECHNICAL_ANALYST,
        CommitteeRole.RISK_MANAGER,
        CommitteeRole.DEVILS_ADVOCATE,
    }
    assert CommitteeRole.COMMITTEE_CHAIR not in ANALYST_ROLES
    assert isinstance(ANALYST_ROLES, frozenset)


# --- Frozen dataclasses -----------------------------------------------------


def test_evidence_item_is_frozen() -> None:
    item = make_evidence_item()
    with pytest.raises(FrozenInstanceError):
        item.key = "other"  # type: ignore[misc]


def test_agent_report_is_frozen() -> None:
    report = make_agent_report()
    with pytest.raises(FrozenInstanceError):
        report.summary = "other"  # type: ignore[misc]


def test_risk_item_is_frozen() -> None:
    risk = make_risk_item()
    with pytest.raises(FrozenInstanceError):
        risk.key = "other"  # type: ignore[misc]


def test_investment_decision_is_frozen() -> None:
    decision = make_investment_decision()
    with pytest.raises(FrozenInstanceError):
        decision.summary = "other"  # type: ignore[misc]


# --- EvidenceItem -----------------------------------------------------------


def test_evidence_item_valid_with_decimal_value() -> None:
    item = make_evidence_item(value=Decimal("1.5"))
    assert item.value == Decimal("1.5")


def test_evidence_item_valid_with_str_value() -> None:
    item = make_evidence_item(value="golden cross")
    assert item.value == "golden cross"


def test_evidence_item_rejects_blank_key() -> None:
    with pytest.raises(ValueError):
        make_evidence_item(key="   ")


def test_evidence_item_rejects_blank_interpretation() -> None:
    with pytest.raises(ValueError):
        make_evidence_item(interpretation="")


def test_evidence_item_rejects_blank_string_value() -> None:
    with pytest.raises(ValueError):
        make_evidence_item(value="   ")


def test_evidence_item_rejects_bool_value() -> None:
    with pytest.raises(ValueError):
        make_evidence_item(value=True)


def test_evidence_item_rejects_int_value() -> None:
    with pytest.raises(ValueError):
        make_evidence_item(value=5)


def test_evidence_item_rejects_float_value() -> None:
    with pytest.raises(ValueError):
        make_evidence_item(value=5.0)


# --- AgentReport --------------------------------------------------------


def test_agent_report_valid_construction_for_each_analyst_role() -> None:
    for role in ANALYST_ROLES:
        report = make_agent_report(role=role)
        assert report.role == role


def test_agent_report_rejects_committee_chair_role() -> None:
    with pytest.raises(ValueError):
        make_agent_report(role=CommitteeRole.COMMITTEE_CHAIR)


def test_agent_report_confidence_boundary_values_are_accepted() -> None:
    assert make_agent_report(confidence=Decimal("0")).confidence == Decimal("0")
    assert make_agent_report(confidence=Decimal("1")).confidence == Decimal("1")


def test_agent_report_confidence_below_zero_is_rejected() -> None:
    with pytest.raises(ValueError):
        make_agent_report(confidence=Decimal("-0.01"))


def test_agent_report_confidence_above_one_is_rejected() -> None:
    with pytest.raises(ValueError):
        make_agent_report(confidence=Decimal("1.01"))


def test_agent_report_rejects_float_confidence() -> None:
    with pytest.raises(ValueError):
        make_agent_report(confidence=0.5)  # type: ignore[arg-type]


def test_agent_report_rejects_blank_summary() -> None:
    with pytest.raises(ValueError):
        make_agent_report(summary="   ")


def test_agent_report_rejects_blank_reasoning() -> None:
    with pytest.raises(ValueError):
        make_agent_report(reasoning="")


def test_agent_report_rejects_timezone_naive_generated_at() -> None:
    with pytest.raises(ValueError):
        make_agent_report(generated_at=datetime(2026, 7, 21, 12, 0))


def test_agent_report_normalizes_evidence_list_to_tuple() -> None:
    report = make_agent_report(evidence=[make_evidence_item()])
    assert isinstance(report.evidence, tuple)


def test_agent_report_evidence_unaffected_by_mutating_original_list() -> None:
    evidence_list = [make_evidence_item(key="a")]
    report = make_agent_report(evidence=evidence_list)
    evidence_list.append(make_evidence_item(key="b"))
    assert len(report.evidence) == 1


def test_agent_report_accepts_empty_evidence() -> None:
    report = make_agent_report(evidence=[])
    assert report.evidence == ()


def test_agent_report_rejects_non_evidence_item_entries() -> None:
    with pytest.raises(ValueError):
        make_agent_report(evidence=["not an evidence item"])


# --- RiskItem -------------------------------------------------------------


def test_risk_item_valid_construction_for_each_severity() -> None:
    for severity in RiskSeverity:
        risk = make_risk_item(severity=severity)
        assert risk.severity == severity


def test_risk_item_valid_construction_for_each_analyst_role() -> None:
    for role in ANALYST_ROLES:
        risk = make_risk_item(source_role=role)
        assert risk.source_role == role


def test_risk_item_rejects_committee_chair_source_role() -> None:
    with pytest.raises(ValueError):
        make_risk_item(source_role=CommitteeRole.COMMITTEE_CHAIR)


def test_risk_item_rejects_blank_key() -> None:
    with pytest.raises(ValueError):
        make_risk_item(key="")


def test_risk_item_rejects_blank_description() -> None:
    with pytest.raises(ValueError):
        make_risk_item(description="   ")


# --- InvestmentDecision -----------------------------------------------------


def test_investment_decision_valid_construction() -> None:
    decision = make_investment_decision()
    assert len(decision.agent_reports) == 3
    assert {report.role for report in decision.agent_reports} == ANALYST_ROLES


def test_investment_decision_rejects_non_agent_report_entries() -> None:
    with pytest.raises(ValueError):
        make_investment_decision(agent_reports=("not a report",))


def test_investment_decision_rejects_duplicate_analyst_roles() -> None:
    asset = make_asset()
    reports = (
        make_agent_report(asset=asset, role=CommitteeRole.TECHNICAL_ANALYST),
        make_agent_report(asset=asset, role=CommitteeRole.TECHNICAL_ANALYST, summary="Second look."),
        make_agent_report(asset=asset, role=CommitteeRole.RISK_MANAGER),
    )
    with pytest.raises(ValueError):
        make_investment_decision(asset=asset, agent_reports=reports)


def test_investment_decision_rejects_missing_analyst_role() -> None:
    asset = make_asset()
    reports = (
        make_agent_report(asset=asset, role=CommitteeRole.TECHNICAL_ANALYST),
        make_agent_report(asset=asset, role=CommitteeRole.RISK_MANAGER),
    )
    with pytest.raises(ValueError):
        make_investment_decision(asset=asset, agent_reports=reports)


def test_investment_decision_rejects_extra_report_beyond_three_roles() -> None:
    asset = make_asset()
    reports = (
        make_agent_report(asset=asset, role=CommitteeRole.TECHNICAL_ANALYST),
        make_agent_report(asset=asset, role=CommitteeRole.TECHNICAL_ANALYST, summary="Second look."),
        make_agent_report(asset=asset, role=CommitteeRole.RISK_MANAGER),
        make_agent_report(asset=asset, role=CommitteeRole.DEVILS_ADVOCATE),
    )
    with pytest.raises(ValueError):
        make_investment_decision(asset=asset, agent_reports=reports)


def test_investment_decision_rejects_committee_chair_role_defense_in_depth() -> None:
    """Even if an AgentReport's role were mutated after construction (bypassing
    its own validation), InvestmentDecision's own role-set check must still
    reject it — this is a distinct check from AgentReport's, not a reliance
    on it.
    """
    asset = make_asset()
    tampered_report = make_agent_report(asset=asset, role=CommitteeRole.RISK_MANAGER)
    object.__setattr__(tampered_report, "role", CommitteeRole.COMMITTEE_CHAIR)
    reports = (
        make_agent_report(asset=asset, role=CommitteeRole.TECHNICAL_ANALYST),
        tampered_report,
        make_agent_report(asset=asset, role=CommitteeRole.DEVILS_ADVOCATE),
    )
    with pytest.raises(ValueError):
        make_investment_decision(asset=asset, agent_reports=reports)


def test_investment_decision_rejects_report_for_different_asset() -> None:
    asset = make_asset("AAPL")
    other_asset = make_asset("MSFT")
    reports = (
        make_agent_report(asset=other_asset, role=CommitteeRole.TECHNICAL_ANALYST),
        make_agent_report(asset=asset, role=CommitteeRole.RISK_MANAGER),
        make_agent_report(asset=asset, role=CommitteeRole.DEVILS_ADVOCATE),
    )
    with pytest.raises(ValueError):
        make_investment_decision(asset=asset, agent_reports=reports)


def test_investment_decision_accepts_dissenting_opinion_present_in_agent_reports() -> None:
    asset = make_asset()
    reports = make_agent_reports(asset)
    decision = make_investment_decision(asset=asset, agent_reports=reports, dissenting_opinions=(reports[2],))
    assert decision.dissenting_opinions == (reports[2],)


def test_investment_decision_rejects_dissenting_opinion_not_in_agent_reports() -> None:
    asset = make_asset()
    reports = make_agent_reports(asset)
    foreign_report = make_agent_report(asset=asset, role=CommitteeRole.DEVILS_ADVOCATE, summary="A different report.")
    with pytest.raises(ValueError):
        make_investment_decision(asset=asset, agent_reports=reports, dissenting_opinions=(foreign_report,))


def test_investment_decision_rejects_duplicate_dissenting_opinions() -> None:
    asset = make_asset()
    reports = make_agent_reports(asset)
    with pytest.raises(ValueError):
        make_investment_decision(
            asset=asset, agent_reports=reports, dissenting_opinions=(reports[2], reports[2])
        )


def test_investment_decision_accepts_empty_dissenting_opinions() -> None:
    decision = make_investment_decision(dissenting_opinions=())
    assert decision.dissenting_opinions == ()


def test_investment_decision_normalizes_sequences_to_tuples() -> None:
    asset = make_asset()
    reports_list = list(make_agent_reports(asset))
    risks_list = [make_risk_item()]
    decision = make_investment_decision(
        asset=asset,
        agent_reports=reports_list,
        dissenting_opinions=[reports_list[2]],
        risks=risks_list,
    )
    assert isinstance(decision.agent_reports, tuple)
    assert isinstance(decision.dissenting_opinions, tuple)
    assert isinstance(decision.risks, tuple)


def test_investment_decision_unaffected_by_mutating_original_lists() -> None:
    asset = make_asset()
    reports_list = list(make_agent_reports(asset))
    risks_list = [make_risk_item()]
    dissent_list = [reports_list[2]]
    decision = make_investment_decision(
        asset=asset,
        agent_reports=reports_list,
        dissenting_opinions=dissent_list,
        risks=risks_list,
    )
    reports_list.clear()
    risks_list.clear()
    dissent_list.clear()
    assert len(decision.agent_reports) == 3
    assert len(decision.risks) == 1
    assert len(decision.dissenting_opinions) == 1


def test_investment_decision_accepts_valid_risk_items() -> None:
    decision = make_investment_decision(risks=(make_risk_item(),))
    assert len(decision.risks) == 1


def test_investment_decision_rejects_non_risk_item_entries() -> None:
    with pytest.raises(ValueError):
        make_investment_decision(risks=("not a risk item",))


def test_investment_decision_rejects_duplicate_risks_by_key_and_source_role() -> None:
    risk = make_risk_item(key="concentration", source_role=CommitteeRole.RISK_MANAGER)
    duplicate = make_risk_item(
        key="concentration", source_role=CommitteeRole.RISK_MANAGER, description="Different wording."
    )
    with pytest.raises(ValueError):
        make_investment_decision(risks=(risk, duplicate))


def test_investment_decision_accepts_same_risk_key_from_different_source_roles() -> None:
    risk_from_risk_manager = make_risk_item(key="concentration", source_role=CommitteeRole.RISK_MANAGER)
    risk_from_devils_advocate = make_risk_item(key="concentration", source_role=CommitteeRole.DEVILS_ADVOCATE)
    decision = make_investment_decision(risks=(risk_from_risk_manager, risk_from_devils_advocate))
    assert len(decision.risks) == 2


def test_investment_decision_confidence_boundary_values_are_accepted() -> None:
    assert make_investment_decision(confidence=Decimal("0")).confidence == Decimal("0")
    assert make_investment_decision(confidence=Decimal("1")).confidence == Decimal("1")


def test_investment_decision_confidence_below_zero_is_rejected() -> None:
    with pytest.raises(ValueError):
        make_investment_decision(confidence=Decimal("-0.01"))


def test_investment_decision_confidence_above_one_is_rejected() -> None:
    with pytest.raises(ValueError):
        make_investment_decision(confidence=Decimal("1.01"))


def test_investment_decision_rejects_float_confidence() -> None:
    with pytest.raises(ValueError):
        make_investment_decision(confidence=0.5)  # type: ignore[arg-type]


def test_investment_decision_rejects_blank_summary() -> None:
    with pytest.raises(ValueError):
        make_investment_decision(summary="   ")


def test_investment_decision_rejects_timezone_naive_generated_at() -> None:
    with pytest.raises(ValueError):
        make_investment_decision(generated_at=datetime(2026, 7, 21, 12, 0))
