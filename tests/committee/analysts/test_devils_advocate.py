"""Tests for investment_committee.committee.analysts.devils_advocate."""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from decimal import Decimal

from investment_committee.committee.analysts.devils_advocate import DeterministicDevilsAdvocate
from investment_committee.committee.models import AgentStance, CommitteeRole

from .._support import UTC_NOW, make_asset, make_snapshot

ANALYST = DeterministicDevilsAdvocate()
OTHER_TIME = datetime(2026, 8, 1, tzinfo=timezone.utc)

_CLEAN = dict(
    latest_close=Decimal("120"),
    sma_20=Decimal("115"),
    sma_50=Decimal("110"),
    sma_200=Decimal("100"),
    return_30d=Decimal("0.02"),
    return_90d=Decimal("0.05"),
    return_ytd=Decimal("0.08"),
    rsi_14=Decimal("55"),
    maximum_drawdown=Decimal("-0.05"),
)


def test_zero_concerns_yields_support_and_full_confidence() -> None:
    report = ANALYST.analyze(make_snapshot(**_CLEAN), UTC_NOW)

    assert report.stance == AgentStance.SUPPORT
    assert report.confidence == Decimal("0.90")
    assert report.role == CommitteeRole.DEVILS_ADVOCATE


def test_zero_concerns_with_partial_coverage_is_lower_confidence() -> None:
    fields = dict(_CLEAN)
    fields["maximum_drawdown"] = None  # D2 becomes N/A; applicable drops to 4
    report = ANALYST.analyze(make_snapshot(**fields), UTC_NOW)

    assert report.stance == AgentStance.SUPPORT
    assert report.confidence == Decimal("0.75")


def test_one_concern_yields_neutral() -> None:
    fields = dict(_CLEAN)
    fields["maximum_drawdown"] = Decimal("-0.35")  # severe drawdown concern
    report = ANALYST.analyze(make_snapshot(**fields), UTC_NOW)

    assert report.stance == AgentStance.NEUTRAL
    assert report.confidence == Decimal("0.60")


def test_two_concerns_yields_oppose() -> None:
    fields = dict(_CLEAN)
    fields["maximum_drawdown"] = Decimal("-0.35")  # concern 1
    fields["rsi_14"] = Decimal("80")  # concern 2 (overbought)
    report = ANALYST.analyze(make_snapshot(**fields), UTC_NOW)

    assert report.stance == AgentStance.OPPOSE
    assert report.confidence == Decimal("0.50")


def test_three_or_more_concerns_yields_oppose_with_higher_confidence_than_two() -> None:
    fields = dict(_CLEAN)
    fields["maximum_drawdown"] = Decimal("-0.35")  # concern 1
    fields["rsi_14"] = Decimal("80")  # concern 2
    fields["sma_20"], fields["sma_50"], fields["sma_200"] = (
        Decimal("100"),
        Decimal("110"),
        Decimal("105"),
    )  # not cleanly ordered -> concern 3
    report = ANALYST.analyze(make_snapshot(**fields), UTC_NOW)

    assert report.stance == AgentStance.OPPOSE
    assert report.confidence == Decimal("0.75")  # deliberate non-monotonic jump vs. two concerns


def test_insufficient_history_concern_key() -> None:
    report = ANALYST.analyze(make_snapshot(), UTC_NOW)  # everything None: 8/8 missing

    evidence = {item.key: item for item in report.evidence}
    assert "insufficient_history" in evidence
    # Only D5 is applicable when every named indicator is missing; the stance
    # table's concern_count == 1 row is NEUTRAL (not OPPOSE, despite the
    # spec's own illustrative prose describing this exact scenario as
    # "a confident OPPOSE" elsewhere in section 4 - the normative stance
    # table is authoritative and is what is implemented here).
    assert report.stance == AgentStance.NEUTRAL
    assert report.confidence == Decimal("0.60")


def test_trend_momentum_conflict_price_above_sma200_but_momentum_negative() -> None:
    fields = dict(_CLEAN)
    fields["return_30d"] = Decimal("-0.02")  # close (120) > sma_200 (100), momentum negative
    report = ANALYST.analyze(make_snapshot(**fields), UTC_NOW)

    evidence = {item.key: item for item in report.evidence}
    assert "trend_momentum_conflict" in evidence


def test_trend_momentum_conflict_price_below_sma200_but_momentum_positive() -> None:
    fields = dict(_CLEAN)
    fields["latest_close"] = Decimal("90")  # below sma_200 (100), momentum stays positive
    report = ANALYST.analyze(make_snapshot(**fields), UTC_NOW)

    evidence = {item.key: item for item in report.evidence}
    assert "trend_momentum_conflict" in evidence


def test_no_conflict_when_trend_and_momentum_agree() -> None:
    report = ANALYST.analyze(make_snapshot(**_CLEAN), UTC_NOW)

    evidence = {item.key: item for item in report.evidence}
    assert "trend_momentum_aligned" in evidence
    assert "trend_momentum_conflict" not in evidence


def test_weak_trend_confirmation_when_smas_not_cleanly_ordered() -> None:
    fields = dict(_CLEAN)
    fields["sma_20"], fields["sma_50"], fields["sma_200"] = (
        Decimal("100"),
        Decimal("110"),
        Decimal("105"),
    )
    report = ANALYST.analyze(make_snapshot(**fields), UTC_NOW)

    evidence = {item.key: item for item in report.evidence}
    assert "weak_trend_confirmation" in evidence


def test_overextension_overbought_and_oversold() -> None:
    overbought = ANALYST.analyze(make_snapshot(**{**_CLEAN, "rsi_14": Decimal("80")}), UTC_NOW)
    oversold = ANALYST.analyze(make_snapshot(**{**_CLEAN, "rsi_14": Decimal("15")}), UTC_NOW)

    overbought_evidence = {item.key for item in overbought.evidence}
    oversold_evidence = {item.key for item in oversold.evidence}
    assert "overextension_overbought" in overbought_evidence
    assert "overextension_oversold" in oversold_evidence


def test_does_not_depend_on_other_analyst_reports() -> None:
    parameters = list(inspect.signature(DeterministicDevilsAdvocate.analyze).parameters)

    assert parameters == ["self", "snapshot", "generated_at"]


def test_role_asset_and_generated_at_propagate() -> None:
    asset = make_asset("MSFT")
    snapshot = make_snapshot(asset=asset, **_CLEAN)

    report = ANALYST.analyze(snapshot, OTHER_TIME)

    assert report.role == CommitteeRole.DEVILS_ADVOCATE
    assert report.asset == asset
    assert report.generated_at == OTHER_TIME
