"""Tests for conversation.localization and the Spanish rendering of real
committee output.

The deterministic committee writes its reasoning in English from a fixed set
of templates; these tests run the real committee over several indicator
scenarios and check that every reply the user sees is Spanish, while the
recorded values (enums, evidence keys, English text) stay unchanged.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from investment_committee.committee import (
    DeterministicCommitteeChair,
    DeterministicCommitteeEngine,
    DeterministicDevilsAdvocate,
    DeterministicRiskManager,
    DeterministicTechnicalAnalyst,
    InvestmentAction,
)
from investment_committee.conversation.localization import (
    INTERFACE_LOCALE,
    action_label,
    decision_adjustment,
    format_confidence,
    role_label,
    severity_label,
    stance_label,
    translate_interpretation,
    translate_report_summary,
)
from investment_committee.conversation.models import Intent, IntentType
from investment_committee.conversation.summarizer import TemplateResponseSummarizer
from investment_committee.memory import DecisionRecord, DecisionRecordId

from tests.memory._support import (
    UTC_NOW,
    make_asset,
    make_indicator_snapshot,
    make_market_snapshot,
    make_strategy_metadata,
)


def test_default_interface_locale_is_colombian_spanish() -> None:
    assert INTERFACE_LOCALE == "es-CO"


@pytest.mark.parametrize(
    ("label", "value", "expected"),
    [
        (action_label, "BUY", "COMPRAR"),
        (action_label, "SELL", "VENDER"),
        (action_label, "HOLD", "MANTENER"),
        (stance_label, "SUPPORT", "A FAVOR"),
        (stance_label, "OPPOSE", "EN CONTRA"),
        (stance_label, "NEUTRAL", "NEUTRAL"),
        (role_label, "TECHNICAL_ANALYST", "Analista técnico"),
        (role_label, "RISK_MANAGER", "Gestor de riesgo"),
        (role_label, "DEVILS_ADVOCATE", "Abogado del diablo"),
        (severity_label, "HIGH", "ALTO"),
        (severity_label, "MEDIUM", "MEDIO"),
        (severity_label, "LOW", "BAJO"),
    ],
)
def test_presentation_labels(label, value: str, expected: str) -> None:  # type: ignore[no-untyped-def]
    assert label(value) == expected


def test_unknown_label_and_text_fall_back_unchanged() -> None:
    assert action_label("SOMETHING_NEW") == "SOMETHING_NEW"
    assert translate_interpretation("new_key", "brand new rule text") == "brand new rule text"
    assert translate_report_summary("brand new summary") == "brand new summary"


def test_return_interpretation_names_its_period_from_the_key() -> None:
    assert translate_interpretation("return_90d_positive", "return is positive") == "el rendimiento de 90 días es positivo"
    assert (
        translate_interpretation("return_ytd_positive", "return is not positive")
        == "el rendimiento en lo corrido del año no es positivo"
    )


def test_confidence_is_shown_with_decimal_comma_and_percent() -> None:
    assert format_confidence(Decimal("0.90")) == "0,90 (90 %)"
    assert format_confidence(Decimal("0.35")) == "0,35 (35 %)"


def test_chair_overrides_are_explained_in_spanish() -> None:
    downgraded = "HOLD AAPL: Technical: SUPPORT, Risk: SUPPORT, Devil's Advocate: NEUTRAL. Downgraded from BUY (Step 1 baseline) due to a HIGH-severity risk. Decision confidence: 0.50."
    floored = "HOLD AAPL: Technical: SUPPORT, Risk: SUPPORT, Devil's Advocate: SUPPORT. Reduced from BUY (Step 1 baseline) because an analyst's confidence was below the 0.50 floor. Decision confidence: 0.35."

    assert decision_adjustment(downgraded, "HOLD") == (
        "La decisión base era COMPRAR, pero se rebajó a MANTENER por un riesgo de severidad ALTA."
    )
    assert "mínimo de 0,50" in (decision_adjustment(floored, "HOLD") or "")
    assert decision_adjustment("BUY AAPL: Technical: SUPPORT. Decision confidence: 0.90.", "BUY") is None


# --- Real committee output, rendered ---

_BULLISH = dict(
    latest_close=Decimal("110"), sma_20=Decimal("105"), sma_50=Decimal("100"), sma_200=Decimal("90"),
    return_1d=Decimal("0.01"), return_30d=Decimal("0.05"), return_90d=Decimal("0.10"), return_ytd=Decimal("0.12"),
    annualized_volatility=Decimal("0.20"), maximum_drawdown=Decimal("-0.08"), rsi_14=Decimal("60"),
)
_BEARISH_HIGH_RISK = dict(
    latest_close=Decimal("80"), sma_20=Decimal("85"), sma_50=Decimal("90"), sma_200=Decimal("100"),
    return_1d=Decimal("-0.02"), return_30d=Decimal("-0.25"), return_90d=Decimal("-0.30"), return_ytd=Decimal("-0.35"),
    annualized_volatility=Decimal("0.70"), maximum_drawdown=Decimal("-0.45"), rsi_14=Decimal("20"),
)
_MIXED = dict(
    latest_close=Decimal("100"), sma_20=Decimal("101"), sma_50=Decimal("99"), sma_200=Decimal("102"),
    return_1d=Decimal("0"), return_30d=Decimal("0.02"), return_90d=Decimal("-0.01"), return_ytd=Decimal("0.03"),
    annualized_volatility=Decimal("0.35"), maximum_drawdown=Decimal("-0.20"), rsi_14=Decimal("80"),
)
_NO_HISTORY: dict[str, Decimal] = {}

# Fragments of the committee's English templates and raw enum values that must
# never reach the user.
_ENGLISH_MARKERS = (
    "rules passed", "applicable", "could not be evaluated", "insufficient history", "is above", "is not above",
    "Stance", "SUPPORT", "OPPOSE", "BUY", "SELL", "HOLD", "confidence ", "Risks", "Committee:", "Recorded ",
    "volatility", "drawdown", "return is", "RSI14", "moving averages", "momentum", "named indicators",
    "TECHNICAL_ANALYST", "RISK_MANAGER", "DEVILS_ADVOCATE", "HIGH", "MEDIUM", "LOW", "not financial advice",
)


def _committee_record(indicators: dict[str, Decimal], ticker: str = "EURUSD=X") -> DecisionRecord:
    asset = make_asset(ticker)
    snapshot = make_indicator_snapshot(make_market_snapshot(asset), **indicators)
    engine = DeterministicCommitteeEngine(
        technical_analyst=DeterministicTechnicalAnalyst(),
        risk_manager=DeterministicRiskManager(),
        devils_advocate=DeterministicDevilsAdvocate(),
        chair=DeterministicCommitteeChair(),
        clock=lambda: UTC_NOW,
    )
    decision = engine.run(snapshot)
    return DecisionRecord(
        record_id=DecisionRecordId("22222222-2222-4222-8222-222222222222"),
        recorded_at=UTC_NOW + timedelta(minutes=1),
        strategy_metadata=make_strategy_metadata(),
        market_snapshot=snapshot.market_snapshot,
        indicator_snapshot=snapshot,
        decision=decision,
    )


@pytest.mark.parametrize("indicators", [_BULLISH, _BEARISH_HIGH_RISK, _MIXED, _NO_HISTORY], ids=["bullish", "bearish", "mixed", "no-history"])
@pytest.mark.parametrize("intent_type", [IntentType.ANALYZE_ASSET, IntentType.EXPLAIN_DECISION])
def test_real_committee_output_is_rendered_entirely_in_spanish(indicators: dict[str, Decimal], intent_type: IntentType) -> None:
    record = _committee_record(indicators)

    text = TemplateResponseSummarizer().summarize(record, Intent(type=intent_type, parameters={}, raw_text="msg"))

    leaked = [marker for marker in _ENGLISH_MARKERS if marker in text]
    assert leaked == [], f"English leaked into the reply: {leaked}\n{text}"
    assert f"Decisión del comité: {action_label(record.decision.action.value)}" in text


def test_rendering_does_not_change_the_recorded_values() -> None:
    record = _committee_record(_BULLISH)
    before = (record.decision.action, record.decision.summary, [r.summary for r in record.decision.agent_reports])

    TemplateResponseSummarizer().summarize(record, Intent(type=IntentType.EXPLAIN_DECISION, parameters={}, raw_text="m"))

    assert record.decision.action is InvestmentAction.BUY
    assert (record.decision.action, record.decision.summary, [r.summary for r in record.decision.agent_reports]) == before


def test_risks_are_listed_in_spanish_for_a_high_risk_scenario() -> None:
    record = _committee_record(_BEARISH_HIGH_RISK)

    text = TemplateResponseSummarizer().summarize(record, Intent(type=IntentType.ANALYZE_ASSET, parameters={}, raw_text="m"))

    assert record.decision.risks  # the scenario really produces risks
    assert "Riesgos identificados:" in text
    assert "- ALTO: volatilidad anualizada alta (señalado por: Gestor de riesgo)" in text
