"""Tests for investment_committee.committee.analysts.technical."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from investment_committee.committee.analysts.technical import DeterministicTechnicalAnalyst
from investment_committee.committee.models import AgentStance, CommitteeRole

from .._support import UTC_NOW, make_asset, make_snapshot

ANALYST = DeterministicTechnicalAnalyst()
OTHER_TIME = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _trend_chain(t1: str, t2: str, t3: str) -> dict[str, Decimal]:
    """Independently controllable T1/T2/T3 outcomes via a value chain."""
    sma_200 = Decimal("100")
    sma_50 = sma_200 + Decimal("10") if t3 == "pass" else sma_200 - Decimal("10")
    sma_20 = sma_50 + Decimal("10") if t2 == "pass" else sma_50 - Decimal("10")
    latest_close = sma_20 + Decimal("10") if t1 == "pass" else sma_20 - Decimal("10")
    return {"latest_close": latest_close, "sma_20": sma_20, "sma_50": sma_50, "sma_200": sma_200}


def _returns(p1: str, p2: str, p3: str) -> dict[str, Decimal]:
    value = {"pass": Decimal("0.05"), "fail": Decimal("-0.05")}
    return {"return_30d": value[p1], "return_90d": value[p2], "return_ytd": value[p3]}


def test_all_rules_pass_yields_support_and_top_confidence() -> None:
    fields = _trend_chain("pass", "pass", "pass")
    fields.update(_returns("pass", "pass", "pass"))
    fields["rsi_14"] = Decimal("55")
    snapshot = make_snapshot(**fields)

    report = ANALYST.analyze(snapshot, UTC_NOW)

    assert report.stance == AgentStance.SUPPORT
    assert report.confidence == Decimal("0.90")
    assert report.role == CommitteeRole.TECHNICAL_ANALYST


def test_all_rules_fail_yields_oppose_and_lowest_confidence() -> None:
    fields = _trend_chain("fail", "fail", "fail")
    fields.update(_returns("fail", "fail", "fail"))
    fields["rsi_14"] = Decimal("80")
    snapshot = make_snapshot(**fields)

    report = ANALYST.analyze(snapshot, UTC_NOW)

    assert report.stance == AgentStance.OPPOSE
    assert report.confidence == Decimal("0.20")


def test_mixed_five_pass_two_fail_yields_neutral() -> None:
    fields = _trend_chain("pass", "pass", "pass")
    fields.update(_returns("pass", "fail", "fail"))
    fields["rsi_14"] = Decimal("55")
    snapshot = make_snapshot(**fields)

    report = ANALYST.analyze(snapshot, UTC_NOW)

    assert report.stance == AgentStance.NEUTRAL
    assert report.confidence == Decimal("0.60")


def test_missing_sma200_still_supports_with_partial_coverage_confidence() -> None:
    fields = _trend_chain("pass", "pass", "pass")
    fields["sma_200"] = None  # forces T3 to N/A regardless of the chain value
    fields.update(_returns("pass", "pass", "pass"))
    fields["rsi_14"] = Decimal("55")
    snapshot = make_snapshot(**fields)

    report = ANALYST.analyze(snapshot, UTC_NOW)

    assert report.stance == AgentStance.SUPPORT
    assert report.confidence == Decimal("0.75")
    assert any(item.key == "sma50_above_sma200_unavailable" for item in report.evidence)


def test_missing_rsi_still_supports_with_partial_coverage_confidence() -> None:
    fields = _trend_chain("pass", "pass", "pass")
    fields.update(_returns("pass", "pass", "pass"))
    fields["rsi_14"] = None
    snapshot = make_snapshot(**fields)

    report = ANALYST.analyze(snapshot, UTC_NOW)

    assert report.stance == AgentStance.SUPPORT
    assert report.confidence == Decimal("0.75")
    assert any(item.key == "rsi14_healthy_band_unavailable" for item in report.evidence)


def test_missing_all_returns_still_supports_with_partial_coverage_confidence() -> None:
    fields = _trend_chain("pass", "pass", "pass")
    fields["rsi_14"] = Decimal("55")
    snapshot = make_snapshot(**fields)  # return_30d/90d/ytd default to None

    report = ANALYST.analyze(snapshot, UTC_NOW)

    assert report.stance == AgentStance.SUPPORT
    assert report.confidence == Decimal("0.75")


def test_no_data_at_all_yields_neutral_and_zero_confidence() -> None:
    snapshot = make_snapshot()

    report = ANALYST.analyze(snapshot, UTC_NOW)

    assert report.stance == AgentStance.NEUTRAL
    assert report.confidence == Decimal("0.00")


def test_rule_strength_row_failed_equals_one() -> None:
    fields = _trend_chain("pass", "pass", "pass")
    fields.update(_returns("pass", "pass", "pass"))
    fields["rsi_14"] = Decimal("80")  # the single failure
    snapshot = make_snapshot(**fields)

    report = ANALYST.analyze(snapshot, UTC_NOW)

    assert report.stance == AgentStance.SUPPORT
    assert report.confidence == Decimal("0.75")


def test_rule_strength_row_failed_at_least_three_and_passed_at_least_failed() -> None:
    fields = _trend_chain("pass", "pass", "pass")
    fields.update(_returns("fail", "fail", "fail"))
    fields["rsi_14"] = Decimal("55")
    snapshot = make_snapshot(**fields)  # passed=4, failed=3

    report = ANALYST.analyze(snapshot, UTC_NOW)

    assert report.stance == AgentStance.NEUTRAL
    assert report.confidence == Decimal("0.50")


def test_rule_strength_row_more_failed_than_passed() -> None:
    fields = _trend_chain("pass", "fail", "fail")
    fields.update(_returns("fail", "fail", "fail"))
    fields["rsi_14"] = Decimal("55")
    snapshot = make_snapshot(**fields)  # passed=2, failed=5

    report = ANALYST.analyze(snapshot, UTC_NOW)

    assert report.stance == AgentStance.NEUTRAL
    assert report.confidence == Decimal("0.35")


def test_single_dissent_still_supports_when_applicable_at_least_four() -> None:
    fields = _trend_chain("pass", "pass", "pass")
    fields["rsi_14"] = Decimal("80")  # the single failure; applicable == 4
    snapshot = make_snapshot(**fields)  # returns default to None (na=3)

    report = ANALYST.analyze(snapshot, UTC_NOW)

    assert report.stance == AgentStance.SUPPORT
    assert report.confidence == Decimal("0.75")


def test_single_dissent_does_not_support_when_applicable_below_four() -> None:
    fields = _trend_chain("pass", "pass", "fail")  # applicable == 3, one failure
    snapshot = make_snapshot(**fields)  # rsi and returns default to None

    report = ANALYST.analyze(snapshot, UTC_NOW)

    assert report.stance == AgentStance.NEUTRAL


def test_single_agreement_still_opposes_when_applicable_at_least_four() -> None:
    fields = _trend_chain("pass", "fail", "fail")
    fields["rsi_14"] = Decimal("80")  # applicable == 4: 1 passed, 3 failed
    snapshot = make_snapshot(**fields)

    report = ANALYST.analyze(snapshot, UTC_NOW)

    assert report.stance == AgentStance.OPPOSE
    assert report.confidence == Decimal("0.35")


def test_single_agreement_does_not_oppose_when_applicable_below_four() -> None:
    fields = _trend_chain("pass", "fail", "fail")  # applicable == 3: 1 passed, 2 failed
    snapshot = make_snapshot(**fields)

    report = ANALYST.analyze(snapshot, UTC_NOW)

    assert report.stance == AgentStance.NEUTRAL


def test_price_equal_to_sma20_is_fail_not_pass() -> None:
    snapshot = make_snapshot(latest_close=Decimal("100"), sma_20=Decimal("100"))

    report = ANALYST.analyze(snapshot, UTC_NOW)

    evidence = {item.key: item for item in report.evidence}
    assert "not above" in evidence["price_above_sma20"].interpretation


def test_rsi_boundaries_are_inclusive() -> None:
    low_boundary = ANALYST.analyze(make_snapshot(rsi_14=Decimal("45")), UTC_NOW)
    high_boundary = ANALYST.analyze(make_snapshot(rsi_14=Decimal("70")), UTC_NOW)
    just_below = ANALYST.analyze(make_snapshot(rsi_14=Decimal("44.99")), UTC_NOW)
    just_above = ANALYST.analyze(make_snapshot(rsi_14=Decimal("70.01")), UTC_NOW)

    assert low_boundary.stance == AgentStance.SUPPORT
    assert high_boundary.stance == AgentStance.SUPPORT
    assert just_below.stance == AgentStance.OPPOSE
    assert just_above.stance == AgentStance.OPPOSE


def test_return_30d_zero_is_fail_not_pass() -> None:
    report = ANALYST.analyze(make_snapshot(return_30d=Decimal("0")), UTC_NOW)

    evidence = {item.key: item for item in report.evidence}
    assert "not positive" in evidence["return_30d_positive"].interpretation


def test_evidence_keys_and_values_are_correct() -> None:
    fields = _trend_chain("pass", "pass", "pass")
    fields.update(_returns("pass", "pass", "pass"))
    fields["rsi_14"] = Decimal("55")
    snapshot = make_snapshot(**fields)

    report = ANALYST.analyze(snapshot, UTC_NOW)
    evidence = {item.key: item for item in report.evidence}

    assert evidence["price_above_sma20"].value == fields["latest_close"]
    assert evidence["sma20_above_sma50"].value == fields["sma_20"]
    assert evidence["sma50_above_sma200"].value == fields["sma_50"]
    assert evidence["rsi14_healthy_band"].value == Decimal("55")
    assert evidence["return_30d_positive"].value == fields["return_30d"]
    assert evidence["return_90d_positive"].value == fields["return_90d"]
    assert evidence["return_ytd_positive"].value == fields["return_ytd"]
    assert evidence["checklist_coverage"].value == "7/7"
    assert len(report.evidence) == 8


def test_role_asset_and_generated_at_propagate() -> None:
    asset = make_asset("MSFT")
    snapshot = make_snapshot(asset=asset)

    report = ANALYST.analyze(snapshot, OTHER_TIME)

    assert report.role == CommitteeRole.TECHNICAL_ANALYST
    assert report.asset == asset
    assert report.generated_at == OTHER_TIME
    assert report.generated_at != snapshot.generated_at
