"""Tests for investment_committee.committee.analysts.risk."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from investment_committee.committee.analysts.risk import DeterministicRiskManager
from investment_committee.committee.models import AgentStance, CommitteeRole

from .._support import UTC_NOW, make_asset, make_snapshot

ANALYST = DeterministicRiskManager()
OTHER_TIME = datetime(2026, 8, 1, tzinfo=timezone.utc)

_LOW_RISK = dict(
    annualized_volatility=Decimal("0.10"),
    maximum_drawdown=Decimal("-0.05"),
    rsi_14=Decimal("50"),
    return_30d=Decimal("0.03"),
)
_HIGH_RISK = dict(
    annualized_volatility=Decimal("0.50"),
    maximum_drawdown=Decimal("-0.40"),
    rsi_14=Decimal("85"),
    return_30d=Decimal("0.40"),
)


def test_low_risk_case_yields_support_and_top_confidence() -> None:
    report = ANALYST.analyze(make_snapshot(**_LOW_RISK), UTC_NOW)

    assert report.stance == AgentStance.SUPPORT
    assert report.confidence == Decimal("0.90")
    assert report.role == CommitteeRole.RISK_MANAGER


def test_high_risk_case_yields_oppose_and_lowest_confidence() -> None:
    report = ANALYST.analyze(make_snapshot(**_HIGH_RISK), UTC_NOW)

    assert report.stance == AgentStance.OPPOSE
    assert report.confidence == Decimal("0.20")


def test_mixed_case_two_pass_two_fail_yields_neutral() -> None:
    fields = dict(
        annualized_volatility=Decimal("0.10"),  # pass
        maximum_drawdown=Decimal("-0.05"),  # pass
        rsi_14=Decimal("85"),  # fail
        return_30d=Decimal("0.40"),  # fail
    )
    report = ANALYST.analyze(make_snapshot(**fields), UTC_NOW)

    assert report.stance == AgentStance.NEUTRAL
    assert report.confidence == Decimal("0.60")


def test_missing_all_indicators_yields_neutral_and_zero_confidence() -> None:
    report = ANALYST.analyze(make_snapshot(), UTC_NOW)

    assert report.stance == AgentStance.NEUTRAL
    assert report.confidence == Decimal("0.00")


def test_missing_one_indicator_still_supports_with_partial_coverage() -> None:
    fields = dict(_LOW_RISK)
    fields["return_30d"] = None
    report = ANALYST.analyze(make_snapshot(**fields), UTC_NOW)

    assert report.stance == AgentStance.SUPPORT
    assert report.confidence == Decimal("0.75")
    assert any(item.key == "return_30d_unavailable" for item in report.evidence)


def test_single_dissent_requires_full_coverage_for_support() -> None:
    fields = dict(_LOW_RISK)
    fields["rsi_14"] = Decimal("85")  # the single failure, full coverage (applicable == 4)
    report = ANALYST.analyze(make_snapshot(**fields), UTC_NOW)

    assert report.stance == AgentStance.SUPPORT
    assert report.confidence == Decimal("0.75")


def test_single_dissent_without_full_coverage_is_neutral() -> None:
    fields = dict(_LOW_RISK)
    fields["rsi_14"] = Decimal("85")  # failure
    fields["return_30d"] = None  # applicable drops to 3
    report = ANALYST.analyze(make_snapshot(**fields), UTC_NOW)

    assert report.stance == AgentStance.NEUTRAL


# -- volatility (R1): checklist gate boundary vs. evidence band boundary ----


def test_volatility_gate_boundary_at_0_40_is_fail() -> None:
    report = ANALYST.analyze(make_snapshot(annualized_volatility=Decimal("0.40")), UTC_NOW)

    evidence = {item.key: item for item in report.evidence}
    assert "annualized_volatility_high" in evidence
    assert "annualized_volatility_moderate" not in evidence


def test_volatility_band_boundary_at_0_20_is_moderate() -> None:
    report = ANALYST.analyze(make_snapshot(annualized_volatility=Decimal("0.20")), UTC_NOW)

    evidence = {item.key: item for item in report.evidence}
    assert "annualized_volatility_moderate" in evidence
    assert "annualized_volatility_low" not in evidence


def test_volatility_just_below_0_20_is_low() -> None:
    report = ANALYST.analyze(make_snapshot(annualized_volatility=Decimal("0.19")), UTC_NOW)

    evidence = {item.key: item for item in report.evidence}
    assert "annualized_volatility_low" in evidence


# -- drawdown (R2) ------------------------------------------------------------


def test_drawdown_gate_boundary_at_minus_0_30_is_fail() -> None:
    report = ANALYST.analyze(make_snapshot(maximum_drawdown=Decimal("-0.30")), UTC_NOW)

    evidence = {item.key: item for item in report.evidence}
    assert "maximum_drawdown_severe" in evidence


def test_drawdown_just_above_minus_0_30_is_moderate() -> None:
    report = ANALYST.analyze(make_snapshot(maximum_drawdown=Decimal("-0.29")), UTC_NOW)

    evidence = {item.key: item for item in report.evidence}
    assert "maximum_drawdown_moderate" in evidence


def test_drawdown_boundary_at_minus_0_10_is_shallow() -> None:
    report = ANALYST.analyze(make_snapshot(maximum_drawdown=Decimal("-0.10")), UTC_NOW)

    evidence = {item.key: item for item in report.evidence}
    assert "maximum_drawdown_shallow" not in evidence
    assert "maximum_drawdown_moderate" in evidence


def test_drawdown_just_above_minus_0_10_is_shallow() -> None:
    report = ANALYST.analyze(make_snapshot(maximum_drawdown=Decimal("-0.09")), UTC_NOW)

    evidence = {item.key: item for item in report.evidence}
    assert "maximum_drawdown_shallow" in evidence


# -- RSI (R3) -----------------------------------------------------------------


def test_rsi_stability_boundaries_are_inclusive() -> None:
    low = ANALYST.analyze(make_snapshot(rsi_14=Decimal("30")), UTC_NOW)
    high = ANALYST.analyze(make_snapshot(rsi_14=Decimal("70")), UTC_NOW)
    just_below = ANALYST.analyze(make_snapshot(rsi_14=Decimal("29.99")), UTC_NOW)
    just_above = ANALYST.analyze(make_snapshot(rsi_14=Decimal("70.01")), UTC_NOW)

    assert {item.key for item in low.evidence} & {"rsi_14_stable"}
    assert {item.key for item in high.evidence} & {"rsi_14_stable"}
    assert {item.key for item in just_below.evidence} & {"rsi_14_extreme"}
    assert {item.key for item in just_above.evidence} & {"rsi_14_extreme"}


# -- short-term move (R4) ------------------------------------------------------


def test_return_30d_gate_boundary_at_0_25_is_pass() -> None:
    report = ANALYST.analyze(make_snapshot(return_30d=Decimal("0.25")), UTC_NOW)

    evidence = {item.key: item for item in report.evidence}
    assert "return_30d_elevated" in evidence
    assert "return_30d_extreme" not in evidence


def test_return_30d_just_above_0_25_is_extreme_and_fails() -> None:
    report = ANALYST.analyze(make_snapshot(return_30d=Decimal("0.26")), UTC_NOW)

    evidence = {item.key: item for item in report.evidence}
    assert "return_30d_extreme" in evidence


def test_return_30d_boundary_at_0_10_is_contained() -> None:
    report = ANALYST.analyze(make_snapshot(return_30d=Decimal("0.10")), UTC_NOW)

    evidence = {item.key: item for item in report.evidence}
    assert "return_30d_contained" in evidence


def test_return_30d_negative_uses_absolute_magnitude() -> None:
    report = ANALYST.analyze(make_snapshot(return_30d=Decimal("-0.26")), UTC_NOW)

    evidence = {item.key: item for item in report.evidence}
    assert "return_30d_extreme" in evidence


def test_evidence_keys_and_checklist_coverage() -> None:
    report = ANALYST.analyze(make_snapshot(**_LOW_RISK), UTC_NOW)
    evidence = {item.key: item for item in report.evidence}

    assert evidence["checklist_coverage"].value == "4/4"
    assert len(report.evidence) == 5


def test_role_asset_and_generated_at_propagate() -> None:
    asset = make_asset("MSFT")
    snapshot = make_snapshot(asset=asset, **_LOW_RISK)

    report = ANALYST.analyze(snapshot, OTHER_TIME)

    assert report.role == CommitteeRole.RISK_MANAGER
    assert report.asset == asset
    assert report.generated_at == OTHER_TIME
