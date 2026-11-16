"""Representative invalid replies: the output contract, its bounded
normalisations, the controlled repair round, vote/stance separation and
missing-information de-duplication. Fake LLM only - no paid calls."""

from __future__ import annotations

import json
from typing import Any

import pytest

from investment_committee.committee.ai import (
    AICommitteeCoordinator,
    AgentOutputError,
    AgentRole,
    AgentStatus,
    AnalysisBudget,
    ValidationStatus,
)
from investment_committee.committee.ai.coordinator import dedupe_missing
from investment_committee.committee.ai.prompts import COMMON_RULES
from investment_committee.committee.ai.schemas import parse_devils_advocate, parse_technical
from investment_committee.integrations.llm import LLMMessage, LLMUnavailableError
from investment_committee.market_intelligence.macro import ExternalDocument

from ._support import GOOD_REPLIES, NOW, FakeLLM, packet, replies

IDS = frozenset({"E1", "E2", "E3", "E4", "E5"})
SEVEN = [f"Riesgo adverso número {i}." for i in range(1, 8)]  # "1".."7": small counts, grounded


def _devil(**changes: Any) -> dict[str, Any]:
    return {**GOOD_REPLIES["devils_advocate"], **changes}


def _sequence(*replies_in_order: Any):  # type: ignore[no-untyped-def]
    """A scripted role that answers differently on each call (first reply,
    then the reply to the repair request)."""
    queue = list(replies_in_order)
    seen: list[list[LLMMessage]] = []

    def answer(messages: list[LLMMessage]) -> Any:
        seen.append(messages)
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    answer.seen = seen  # type: ignore[attr-defined]
    return answer


def _run(llm: FakeLLM, budget: AnalysisBudget | None = None, **packet_options: Any):  # type: ignore[no-untyped-def]
    return AICommitteeCoordinator(llm, budget=budget, clock=lambda: NOW, id_factory=lambda: "ai-x").run(packet(**packet_options))


# --- the schema contract ---


def test_too_many_items_is_an_explicit_error_not_a_silent_truncation() -> None:
    with pytest.raises(AgentOutputError, match=r"'invalidacion' has 7 items; at most 6 are allowed"):
        parse_devils_advocate(_devil(invalidacion=SEVEN), IDS)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([3], r"'invalidacion' item 1 is a number; expected a string"),
        ([["anidada"]], r"'invalidacion' item 1 is a list; expected a string"),
        ([{"texto": "x", "peso": 2}], r"'invalidacion' item 1 is a object; expected a string"),
        ([{"descripcion": "x"}], r"'invalidacion' item 1 is a object; expected a string"),
        ({"a": "b"}, r"'invalidacion' is a object; expected a list"),
        (5, r"'invalidacion' is a number; expected a list"),
        ([{"texto": "x", "evidencia": "E3"}], r"'invalidacion' item 1 has an 'evidencia' that is not a list of ids"),
    ],
)
def test_ambiguous_shapes_are_rejected_with_the_exact_violation(value: Any, message: str) -> None:
    with pytest.raises(AgentOutputError, match=message):
        parse_devils_advocate(_devil(invalidacion=value), IDS)


def test_a_single_string_becomes_a_one_item_list_and_is_reported() -> None:
    notes: list[str] = []

    opinion = parse_devils_advocate(_devil(invalidacion="Un cierre bajo la SMA50."), IDS, notes)

    assert opinion.invalidation == ("Un cierre bajo la SMA50.",)
    assert notes == ["'invalidacion': llegó como un texto suelto y se tomó como lista de un elemento"]


def test_null_list_becomes_empty_and_is_reported() -> None:
    notes: list[str] = []

    opinion = parse_devils_advocate(_devil(contradicciones=None), IDS, notes)

    assert opinion.contradictions == ()
    assert notes == ["'contradicciones': llegó vacío (null) y se tomó como lista vacía"]


def test_argument_shaped_items_become_text_keeping_only_real_citations() -> None:
    notes: list[str] = []
    items = [{"texto": "Un cierre bajo la SMA50", "evidencia": ["e3", "E99"]}, {"texto": "Un RSI en sobreventa"}]

    opinion = parse_devils_advocate(_devil(invalidacion=items), IDS, notes)

    assert opinion.invalidation == ("Un cierre bajo la SMA50 (E3)", "Un RSI en sobreventa")
    assert notes == ["'invalidacion': 2 elemento(s) con formato {texto, evidencia} se convirtieron a texto"]


def test_bare_string_arguments_are_dropped_and_reported() -> None:
    notes: list[str] = []
    technical = {**GOOD_REPLIES["technical"], "argumentos": ["El precio sube.", {"texto": "RSI comprador.", "evidencia": ["E4"]}]}

    opinion = parse_technical(technical, IDS, notes)

    assert [a.text for a in opinion.arguments] == ["RSI comprador."]
    assert notes == ["'argumentos': se descartó un argumento sin evidencia citada"]


def test_prompt_states_the_list_limits_and_types() -> None:
    assert "Cada lista tiene como máximo 4 elementos" in COMMON_RULES
    assert "sin repetir la misma idea" in COMMON_RULES
    assert "las listas de texto contienen solo cadenas, nunca objetos" in COMMON_RULES


# --- controlled repair ---


def test_invalid_devil_reply_is_repaired_once_and_the_committee_stays_coherent() -> None:
    devil = _sequence(_devil(invalidacion=SEVEN), _devil(invalidacion=SEVEN[:6]))
    llm = FakeLLM(replies(devils_advocate=devil))

    report = _run(llm)

    result = report.agent(AgentRole.DEVILS_ADVOCATE)
    assert result is not None and result.status is AgentStatus.OK and result.repaired
    assert result.calls == 2 and report.calls == 5
    assert result.validation_errors == ("'invalidacion' has 7 items; at most 6 are allowed",)
    assert result.notes[0].startswith("la primera respuesta no cumplió el formato")
    assert len(result.opinion.invalidation) == 6  # type: ignore[union-attr]
    assert report.validation_status is ValidationStatus.COMPLETE
    assert report.decision.conclusion == "FAVORABLE"  # coherent with the agents
    assert report.agent(AgentRole.COORDINATOR).status is AgentStatus.OK  # type: ignore[union-attr]


def test_repair_request_carries_the_invalid_json_and_the_violation_only() -> None:
    invalid = _devil(invalidacion=SEVEN)
    devil = _sequence(invalid, _devil())
    _run(FakeLLM(replies(devils_advocate=devil)))

    repair = devil.seen[1]  # type: ignore[attr-defined]
    assert [m.role for m in repair[-2:]] == ["assistant", "user"]
    assert json.loads(repair[-2].content) == invalid
    assert "'invalidacion' has 7 items; at most 6 are allowed" in repair[-1].content
    assert "sin agregar contenido nuevo" in repair[-1].content


def test_unrecoverable_reply_keeps_partial_validation_and_the_other_agents() -> None:
    devil = _sequence(_devil(invalidacion=SEVEN), _devil(invalidacion=[{"texto": "x", "peso": 1}]))

    report = _run(FakeLLM(replies(devils_advocate=devil)))

    result = report.agent(AgentRole.DEVILS_ADVOCATE)
    assert result is not None and result.status is AgentStatus.FAILED and not result.repaired
    assert result.error == "la respuesta del modelo no cumplió el formato exigido (también tras una corrección)"
    assert len(result.validation_errors) == 2
    assert report.validation_status is ValidationStatus.PARTIAL
    assert report.agent(AgentRole.COORDINATOR).status is AgentStatus.OK  # type: ignore[union-attr]
    assert report.calls == 5


def test_no_repair_when_the_call_budget_cannot_pay_for_it() -> None:
    llm = FakeLLM(replies(devils_advocate=_devil(invalidacion=SEVEN)))

    report = _run(llm, budget=AnalysisBudget(max_calls=4))

    assert report.calls == 4  # the synthesis still ran; no repair call was made
    assert report.agent(AgentRole.DEVILS_ADVOCATE).status is AgentStatus.FAILED  # type: ignore[union-attr]
    assert report.agent(AgentRole.COORDINATOR).status is AgentStatus.OK  # type: ignore[union-attr]


def test_repair_never_starves_later_agents() -> None:
    document = ExternalDocument("Agencia", NOW, "Tasas", "El banco central mantuvo su tasa.")
    llm = FakeLLM(replies(technical=_sequence({**GOOD_REPLIES["technical"], "riesgos": SEVEN}, GOOD_REPLIES["technical"])))

    report = _run(llm, documents=(document,))  # five agent calls planned: no room for a repair

    assert report.calls == 5
    assert all(a.status is AgentStatus.OK for a in report.agents if a.role is not AgentRole.TECHNICAL_ANALYST)


def test_repairs_are_capped_per_analysis() -> None:
    llm = FakeLLM(replies(devils_advocate=_devil(invalidacion=SEVEN)))

    report = _run(llm, budget=AnalysisBudget(max_calls=10, max_repairs=0))

    assert report.calls == 4


def test_provider_errors_are_not_repaired() -> None:
    devil = _sequence(LLMUnavailableError("503"))

    report = _run(FakeLLM(replies(devils_advocate=devil)))

    assert report.calls == 4
    assert report.agent(AgentRole.DEVILS_ADVOCATE).validation_errors == ()  # type: ignore[union-attr]


def test_repair_respects_the_token_budget_and_keeps_tokens_for_the_synthesis() -> None:
    repairable = _sequence(_devil(invalidacion=SEVEN), _devil())
    roomy = _run(FakeLLM(replies(devils_advocate=repairable)), budget=AnalysisBudget(max_total_tokens=10_000))
    tight = _run(FakeLLM(replies(devils_advocate=_sequence(_devil(invalidacion=SEVEN), _devil()))), budget=AnalysisBudget(max_total_tokens=7_000))

    assert roomy.agent(AgentRole.DEVILS_ADVOCATE).repaired is True  # type: ignore[union-attr]
    assert tight.calls == 4  # the repair would not leave tokens for the synthesis, so it was skipped
    assert tight.agent(AgentRole.DEVILS_ADVOCATE).status is AgentStatus.FAILED  # type: ignore[union-attr]
    assert tight.agent(AgentRole.COORDINATOR).status is AgentStatus.OK  # type: ignore[union-attr]


# --- deterministic votes vs AI stances ---


def test_a_deterministic_vote_passed_off_as_an_ai_position_is_removed() -> None:
    confusing = "El analista técnico es bajista mientras el gestor de riesgo se muestra A FAVOR y el abogado del diablo NEUTRAL, sin consenso claro."
    explicit = "El gestor de riesgo determinístico votó A FAVOR porque sus reglas se cumplen."
    report = _run(FakeLLM(replies(synthesis={"desacuerdos": [confusing, explicit]})))

    assert confusing not in report.decision.disagreements
    # An explicit restatement of a deterministic vote is not an AI position
    # either: it leaves the AI lists (the renderer shows the votes apart).
    assert explicit not in report.decision.disagreements
    assert any("repetían votos del comité determinístico" in control for control in report.controls)
    assert any("voto del comité determinístico" in issue.detail for issue in report.issues)


def test_prompt_explains_that_votes_are_not_directional() -> None:
    assert "son VOTOS de reglas fijas, no opiniones de los agentes de IA" in COMMON_RULES
    assert "No son posturas alcistas o bajistas ni recomendaciones de compra o venta" in COMMON_RULES


# --- missing information ---


def test_missing_information_is_deduplicated_by_topic_and_wording() -> None:
    real_world = [
        "volumen: Yahoo Finance no lo publica para Forex",
        "noticias, tasas de interés, inflación, empleo y calendario económico: no hay proveedor integrado",
        "Volumen de negociación para Forex, no publicado por Yahoo Finance.",
        "Noticias, tasas de interés, inflación, empleo y calendario económico, sin proveedor integrado.",
        "Documentos macroeconómicos o noticias externas, no recuperados en este análisis.",
        "SMA200 de 1 hora no calculada",
        "SMA200 de 1 hora no calculada.",
    ]

    assert dedupe_missing(real_world) == (
        "volumen: Yahoo Finance no lo publica para Forex",
        "noticias, tasas de interés, inflación, empleo y calendario económico: no hay proveedor integrado",
        "SMA200 de 1 hora no calculada",
    )


def test_report_missing_information_has_no_paraphrased_duplicates() -> None:
    agents_repeat = replies(
        technical={"informacion_faltante": ["Volumen de negociación para Forex, no publicado por Yahoo Finance."]},
        devils_advocate={"informacion_insuficiente": ["Documentos macroeconómicos o noticias externas, no recuperados."]},
    )
    report = _run(
        FakeLLM(agents_repeat),
        missing=("volumen: Yahoo Finance no lo publica para Forex", "noticias, tasas de interés, inflación, empleo y calendario económico: no hay proveedor integrado"),
    )

    assert report.decision.missing_information == (
        "volumen: Yahoo Finance no lo publica para Forex",
        "noticias, tasas de interés, inflación, empleo y calendario económico: no hay proveedor integrado",
    )
