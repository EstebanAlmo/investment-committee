"""AI committee behaviour when the provider fails: unusable replies
(truncated, empty, filtered), honest call/token accounting, the deterministic
contingency summary when the coordinator fails, and de-duplicated lists.
Fake LLM only - no network, no paid calls."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from investment_committee.committee.ai import AgentRole, AgentStatus, AICommitteeCoordinator, AnalysisBudget
from investment_committee.committee.ai.coordinator import dedupe_similar, is_vote_restatement
from investment_committee.integrations.llm import LLMCallInfo, LLMMessage, LLMResponseError, LLMUsage

from ._support import NOW, FakeLLM, packet, replies, GOOD_REPLIES

FAILED_USAGE = LLMUsage(3000, 900, 0, 3000, Decimal("0.002"))


def _unusable(reason: str, attempts: int = 1) -> LLMResponseError:
    return LLMResponseError(
        "unusable", reason=reason, usage=FAILED_USAGE, attempts=attempts, info=LLMCallInfo(200, "req-1", "length" if reason == "truncated" else "stop", "fake-model")
    )


def _sequence(*answers: Any):  # type: ignore[no-untyped-def]
    queue = list(answers)

    def answer(messages: list[LLMMessage]) -> Any:
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return answer


def _run(llm: FakeLLM, budget: AnalysisBudget | None = None, **options: Any):  # type: ignore[no-untyped-def]
    coordinator = AICommitteeCoordinator(llm, budget=budget, clock=lambda: NOW, id_factory=lambda: "ai-f")
    return coordinator.run(packet(**options), deterministic_baseline="MANTENER (confianza interna 0,50)")


# --- unusable replies ---


def test_truncated_reply_gets_one_shorter_retry_that_succeeds() -> None:
    llm = FakeLLM(replies(technical=_sequence(_unusable("truncated"), GOOD_REPLIES["technical"])))

    report = _run(llm)

    technical = report.agent(AgentRole.TECHNICAL_ANALYST)
    assert technical is not None and technical.status is AgentStatus.OK and technical.repaired
    assert technical.calls == 2
    assert "reason=truncated" in (technical.provider_diagnostic or "")
    assert "finish_reason=length" in (technical.provider_diagnostic or "")
    retry_prompt = llm.calls[1][1][-1].content
    assert "se cortó" in retry_prompt and "máximo 3 elementos" in retry_prompt
    assert report.calls == 5  # four agents + one recovery


def test_empty_reply_after_adapter_retries_gets_one_recovery() -> None:
    llm = FakeLLM(replies(synthesis=_sequence(_unusable("empty_content", attempts=2), GOOD_REPLIES["synthesis"])))

    report = _run(llm, budget=AnalysisBudget(max_calls=7))

    assert report.agent(AgentRole.COORDINATOR).status is AgentStatus.OK  # type: ignore[union-attr]
    assert report.decision.summary_source == "IA"
    assert report.calls == 4 + 2  # the two adapter attempts are counted as calls


def test_persistently_truncated_agent_fails_with_diagnostic_and_the_run_continues() -> None:
    llm = FakeLLM(replies(technical=_sequence(_unusable("truncated"), _unusable("truncated"))))

    report = _run(llm)

    technical = report.agent(AgentRole.TECHNICAL_ANALYST)
    assert technical is not None and technical.status is AgentStatus.FAILED
    assert technical.error == "la respuesta del proveedor de IA no se pudo usar: se cortó por el límite de longitud"
    assert "tras reintento" in (technical.provider_diagnostic or "")
    assert report.agent(AgentRole.RISK_MANAGER).status is AgentStatus.OK  # type: ignore[union-attr]
    assert report.decision.conclusion == "NO_CONCLUYENTE"


def test_content_filter_and_broken_bodies_are_not_retried() -> None:
    for reason in (LLMResponseError.UNEXPECTED_FINISH, LLMResponseError.INVALID_BODY):
        llm = FakeLLM(replies(technical=_unusable(reason)))

        report = _run(llm)

        assert [role for role, _ in llm.calls].count("technical") == 1
        assert report.agent(AgentRole.TECHNICAL_ANALYST).status is AgentStatus.FAILED  # type: ignore[union-attr]


def test_failed_calls_still_count_their_billed_tokens_and_attempts() -> None:
    llm = FakeLLM(replies(technical=_unusable(LLMResponseError.UNEXPECTED_FINISH, attempts=2)))

    report = _run(llm)

    technical = report.agent(AgentRole.TECHNICAL_ANALYST)
    assert technical is not None and technical.calls == 2 and technical.usage.total_tokens == 3900
    assert report.calls == 2 + 3
    assert report.usage.total_tokens == 3900 + 3 * 1200


def test_recovery_is_skipped_when_it_would_starve_later_agents() -> None:
    llm = FakeLLM(replies(technical=_sequence(_unusable("truncated"), GOOD_REPLIES["technical"])))

    report = _run(llm, budget=AnalysisBudget(max_calls=4))

    assert report.agent(AgentRole.TECHNICAL_ANALYST).status is AgentStatus.FAILED  # type: ignore[union-attr]
    assert report.calls == 4 and len(llm.calls) == 4


def test_adapter_retries_are_capped_by_the_remaining_call_budget() -> None:
    llm = FakeLLM()

    _run(llm, budget=AnalysisBudget(max_calls=5))

    # four agents planned, five calls: the first agent may spend one retry,
    # and every later agent keeps at least its own call.
    assert llm.max_attempts[0] == 2
    assert all(cap is not None and cap >= 1 for cap in llm.max_attempts)


def test_tight_call_budget_allows_no_adapter_retries() -> None:
    llm = FakeLLM()

    _run(llm, budget=AnalysisBudget(max_calls=4))

    assert llm.max_attempts == [1, 1, 1, 1]


# --- coordinator failure: deterministic contingency summary ---


def test_coordinator_failure_gives_a_labelled_python_contingency_summary() -> None:
    llm = FakeLLM(replies(technical=_unusable("unexpected_finish"), synthesis=_unusable("unexpected_finish")))

    report = _run(llm)

    decision = report.decision
    assert decision.summary_source == "CONTINGENCIA_PYTHON"
    assert decision.conclusion == "NO_CONCLUYENTE" and decision.trading_stance == "NO_OPERAR"
    assert decision.executive_summary.startswith("El coordinador de IA no produjo una síntesis válida")
    assert "gestor de riesgo: riesgo MEDIO" in decision.executive_summary
    assert "analista técnico (la respuesta del proveedor de IA no se pudo usar" in decision.executive_summary
    assert "no es una opinión de la IA" in decision.beginner_explanation
    assert decision.evidence_for == () and decision.evidence_against == ()


def test_contingency_summary_is_rendered_as_python_not_ai() -> None:
    from investment_committee.conversation.ai_render import render_ai_committee

    report = _run(FakeLLM(replies(synthesis=_unusable("unexpected_finish"))))

    text = render_ai_committee(report)
    assert "Resumen de contingencia (generado por Python, no por la IA): " in text
    assert "Resumen del coordinador de IA" not in text


# --- duplicates and deterministic votes ---


def test_restated_ideas_are_listed_once() -> None:
    synthesis = {
        "condiciones_de_cambio": [
            "Un cierre diario por debajo del soporte de 1,1050 invalidaría el escenario de rebote.",
            "Un cierre diario por encima de la SMA50 cambiaría la conclusión.",
        ],
        "desacuerdos": ["El gestor de riesgo ve estructura diaria bajista frente a la tendencia alcista de 1 hora que sustenta la señal LONG."],
    }
    devil = {
        "invalidacion": ["Un cierre diario por debajo del soporte de 1,1050 (E1) invalidaría el escenario de rebote."],
        "objecion_material": True,
        "contradicciones": [
            "El gestor de riesgo señala estructura diaria bajista (E1, E2) frente a la tendencia alcista de 1 hora (E4) que sustenta la señal LONG."
        ],
    }
    report = _run(FakeLLM(replies(synthesis=synthesis, devils_advocate=devil)))

    conditions = report.decision.change_conditions
    assert len([c for c in conditions if "1,1050" in c]) == 1
    assert "Un cierre diario por encima de la SMA50 cambiaría la conclusión." in conditions
    assert len([d for d in report.decision.disagreements if "estructura diaria bajista" in d]) == 1


def test_a_risk_already_listed_as_evidence_against_is_not_repeated() -> None:
    against = "El RSI14 de 55,62 no muestra sobreventa."
    synthesis = {"evidencia_en_contra": [{"texto": against, "evidencia": ["E1"]}]}
    risk = {"riesgos": [{"texto": "RSI14 de 55,62 que no muestra sobreventa (E4).", "evidencia": ["E1"]}, {"texto": "El ATR de 9,5 pips implica movimientos moderados.", "evidencia": ["E5"]}]}

    report = _run(FakeLLM(replies(synthesis=synthesis, risk=risk)))

    assert report.decision.evidence_against == (against,)
    assert report.decision.risks[0] == "El ATR de 9,5 pips implica movimientos moderados."
    assert not any("sobreventa" in r for r in report.decision.risks)


def test_dedupe_keeps_opposite_conditions_apart() -> None:
    items = ["Un cierre diario por encima de la SMA20 invalidaría la hipótesis.", "Un cierre diario por debajo del soporte invalidaría la hipótesis."]

    assert dedupe_similar(items) == tuple(items)


def test_vote_restatements_are_recognised() -> None:
    assert is_vote_restatement("El gestor de riesgo determinístico vota A FAVOR (riesgo aceptable según sus reglas).")
    assert is_vote_restatement("El comité determinístico decidió MANTENER con confianza interna de 0,30.")
    assert not is_vote_restatement("El gestor de riesgo califica el riesgo como MEDIO.")
    assert not is_vote_restatement("La señal de la estrategia Python es LONG.")


def test_deterministic_votes_get_their_own_section_in_the_report() -> None:
    from dataclasses import replace

    from investment_committee.committee.ai import EvidenceItem
    from investment_committee.conversation.ai_render import render_ai_committee

    base = packet()
    vote = EvidenceItem("E6", "comité determinístico (votos de reglas, no posturas del comité IA)", "voto del gestor de riesgo determinístico",
                        "A FAVOR (riesgo aceptable según sus reglas; no es una recomendación de compra)", "", "comité determinístico", NOW)
    with_votes = replace(base, items=(*base.items, vote))
    restated = "El gestor de riesgo determinístico vota A FAVOR."
    llm = FakeLLM(replies(synthesis={"evidencia_a_favor": [{"texto": restated, "evidencia": ["E6"]}, {"texto": "Precio sobre la SMA50.", "evidencia": ["E1", "E3"]}]}))
    report = AICommitteeCoordinator(llm, clock=lambda: NOW, id_factory=lambda: "ai-v").run(with_votes, deterministic_baseline="MANTENER")

    assert restated not in report.decision.evidence_for
    text = render_ai_committee(report)
    section = text.split("Comité determinístico (/analyze, reglas fijas; sus votos no son opiniones de la IA):")[1]
    assert "- Decisión: MANTENER" in section
    assert "- Voto del gestor de riesgo determinístico: A FAVOR" in section
    assert "Opinión de cada agente de IA:" in text


def test_separating_vote_restatements_marks_the_run_as_adjusted() -> None:
    from investment_committee.committee.ai import ValidationStatus

    restated = "El comité determinístico decidió MANTENER."
    report = _run(FakeLLM(replies(synthesis={"desacuerdos": [restated], "conclusion": "NEUTRAL", "postura_trading": "NO_OPERAR"})))

    assert restated not in report.decision.disagreements
    assert report.validation_status is ValidationStatus.ADJUSTED
