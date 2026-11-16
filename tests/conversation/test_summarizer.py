"""Tests for investment_committee.conversation.summarizer.

See docs/specs/spec-006-conversational-interface.md S17. This phase
delivers only the ResponseSummarizer port - TemplateResponseSummarizer,
LLMResponseSummarizer, and FallbackResponseSummarizer are scoped to a later
phase, so these tests confirm the port shape only.
"""

from __future__ import annotations

import pytest

from investment_committee.conversation.models import Intent, IntentType
from investment_committee.conversation.summarizer import ResponseSummarizer
from investment_committee.conversation.tools.base import HelpContent, ToolOutcome


class _FakeSummarizer(ResponseSummarizer):
    def summarize(self, outcome: ToolOutcome, intent: Intent) -> str:
        assert isinstance(outcome, HelpContent)
        return outcome.description


def test_response_summarizer_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        ResponseSummarizer()  # type: ignore[abstract]


def test_concrete_summarizer_returns_a_string() -> None:
    outcome = HelpContent(available_intents=("Analyze an asset",), description="I'm the assistant.")
    intent = Intent(type=IntentType.HELP, parameters={}, raw_text="/help")

    summary = _FakeSummarizer().summarize(outcome, intent)

    assert summary == "I'm the assistant."


# --- TemplateResponseSummarizer (spec-006 S17, S26) ---

from investment_committee.conversation.summarizer import TemplateResponseSummarizer  # noqa: E402
from investment_committee.conversation.tools.base import NotYetAvailable, PolicyDeclined  # noqa: E402

from dataclasses import replace  # noqa: E402
from decimal import Decimal  # noqa: E402

from investment_committee.conversation.localization import role_label, stance_label  # noqa: E402

from tests.conversation.tools._support import make_record  # noqa: E402
from tests.memory._support import make_asset, make_decision_record, make_indicator_snapshot  # noqa: E402


def _intent(intent_type: IntentType, raw_text: str = "msg", **parameters: str) -> Intent:
    return Intent(type=intent_type, parameters=parameters, raw_text=raw_text)


def test_decision_record_shows_asset_date_action_confidence_and_disclaimers_in_spanish() -> None:
    record = make_record("AAPL", "rec-1")

    text = TemplateResponseSummarizer().summarize(record, _intent(IntentType.ANALYZE_ASSET, symbol="AAPL"))

    assert "Análisis del comité: AAPL" in text
    assert "Fecha del análisis: 2026-07-21 12:00 UTC" in text
    assert "Decisión del comité: MANTENER" in text  # make_record's decision is HOLD
    assert "Confianza interna: 0,90 (90 %)" in text
    assert "Opinión de cada integrante del comité:" in text
    assert "rec-1" in text
    assert "No se ejecutó ninguna operación" in text
    assert "no significa un 90 % de probabilidad de obtener ganancias" in text


def test_record_shows_each_member_with_spanish_role_and_stance() -> None:
    record = make_record("AAPL", "rec-1")

    text = TemplateResponseSummarizer().summarize(record, _intent(IntentType.ANALYZE_ASSET, symbol="AAPL"))

    for report in record.decision.agent_reports:
        assert f"- {role_label(report.role.value)}: {stance_label(report.stance.value)}" in text
    assert "- TECHNICAL_ANALYST:" not in text


def test_explain_lists_each_reports_evidence_with_values() -> None:
    record = make_record("AAPL", "rec-1")

    text = TemplateResponseSummarizer().summarize(record, _intent(IntentType.EXPLAIN_DECISION, symbol="AAPL"))

    for report in record.decision.agent_reports:
        for item in report.evidence:
            assert item.interpretation in text  # unknown (test-only) text is shown unchanged
    assert "(valor:" in text


def test_record_shows_market_data_date_and_latest_close_when_available() -> None:
    base = make_decision_record(asset=make_asset("AAPL"))
    record = replace(
        base, indicator_snapshot=make_indicator_snapshot(base.market_snapshot, latest_close=Decimal("343.42"))
    )

    text = TemplateResponseSummarizer().summarize(record, _intent(IntentType.ANALYZE_ASSET, symbol="AAPL"))

    assert "Datos de mercado hasta: 2026-07-21" in text
    assert "Último cierre: 343,4200" in text


def test_history_lists_one_line_per_record() -> None:
    records = (make_record("AAPL", "r1", days=2), make_record("AAPL", "r2", days=1))

    text = TemplateResponseSummarizer().summarize(records, _intent(IntentType.DECISION_HISTORY, symbol="AAPL"))

    assert text.count("AAPL: MANTENER") == 2
    assert "Decisiones registradas para AAPL" in text
    assert "2026-07-23" in text and "2026-07-22" in text


@pytest.mark.parametrize("raw_text", ["How many decisions have we made?", "¿Cuántas decisiones hemos tomado?"])
def test_history_count_question_includes_tally(raw_text: str) -> None:
    records = (make_record("AAPL", "r1"), make_record("MSFT", "r2", days=1))

    text = TemplateResponseSummarizer().summarize(records, _intent(IntentType.DECISION_HISTORY, raw_text=raw_text))

    assert "Conteo: MANTENER: 2" in text


def test_history_for_forex_pair_uses_canonical_symbol() -> None:
    text = TemplateResponseSummarizer().summarize((), _intent(IntentType.DECISION_HISTORY, symbol="EUR/USD"))

    assert "EURUSD=X" in text
    assert "/analyze EURUSD=X" in text


def test_empty_history_suggests_analyzing() -> None:
    text = TemplateResponseSummarizer().summarize((), _intent(IntentType.DECISION_HISTORY, symbol="AAPL"))

    assert "Todavía no hay decisiones registradas para AAPL" in text
    assert "/analyze AAPL" in text


def test_two_record_history_is_not_rendered_as_a_comparison() -> None:
    records = (make_record("AAPL", "r1", days=1), make_record("AAPL", "r2"))

    history = TemplateResponseSummarizer().summarize(records, _intent(IntentType.DECISION_HISTORY, symbol="AAPL"))
    comparison = TemplateResponseSummarizer().summarize(records, _intent(IntentType.COMPARE_ASSETS))

    assert history != comparison
    assert "Comparación" in comparison


def test_not_yet_available_and_policy_declined_messages_are_verbatim() -> None:
    summarizer = TemplateResponseSummarizer()

    assert "Paper tracking soon." in summarizer.summarize(
        NotYetAvailable(feature_name="Portfolio", message="Paper tracking soon."), _intent(IntentType.PORTFOLIO_SUMMARY)
    )
    assert summarizer.summarize(
        PolicyDeclined(reason="x", message="Declined exactly like this."), _intent(IntentType.ANALYZE_ASSET)
    ) == "Declined exactly like this."


def test_help_lists_description_and_bullets() -> None:
    text = TemplateResponseSummarizer().summarize(
        HelpContent(available_intents=("/analyze AAPL", "/help"), description="I'm the assistant."),
        _intent(IntentType.HELP),
    )

    assert text.startswith("I'm the assistant.")
    assert "- /analyze AAPL" in text and "- /help" in text


def test_unknown_with_clarification_surfaces_it_instead_of_help() -> None:
    text = TemplateResponseSummarizer().summarize(
        HelpContent(available_intents=("/help",), description="I'm the assistant."),
        _intent(IntentType.UNKNOWN, clarification="Which ticker did you mean?"),
    )

    assert text == "Which ticker did you mean?"
