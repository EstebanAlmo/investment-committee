"""AICommitteeCoordinator: one bounded pass of the AI committee.

Sequence (at most five model calls, usually four):
  1. technical analyst
  2. macro analyst - only if macro documents were retrieved; otherwise it
     reports "not available" deterministically, with no model call
  3. risk manager (reads the technical opinion)
  4. devil's advocate - the single critical-review round (reads 1-3)
  5. coordinator synthesis (reads everything)
Each call is checked against the per-analysis budget first (calls, tokens,
estimated cost). A failing agent does not stop the others unless the error
is fatal (no key, bad key, no balance). Replies are validated against the
agent schema (with the few unambiguous normalisations schemas.py lists),
then every unsupported number is removed (grounding.py), and so is any
statement that passes a deterministic committee vote (A FAVOR / EN CONTRA)
off as an AI agent's position.

Every provider attempt counts as a call and every billed token counts,
failed replies included; the adapter's own retries are capped per call
(max_attempts) so the agents still to run keep their share of the budget.
An unusable reply (truncated, empty, or not a JSON object) gets ONE more
request with a stricter length limit, under the same repair budget below;
content filters, unknown finish reasons and broken HTTP bodies do not.
Every provider failure leaves a safe diagnostic (error type, reason, HTTP
status, finish_reason, request id, tokens) on the agent's result.

A reply with an invalid structure gets ONE controlled repair request
(prompts.repair_messages: the invalid JSON plus the exact violation), only
if the budget can pay for it without starving the agents still to run, and
at most `max_repairs` per analysis. If the repair is also invalid, the agent
is recorded as FAILED with both violations and the analysis continues as
PARTIAL.

Deterministic controls then decide what the user sees (apply_controls):
  - stale or critically missing data, or no technical opinion, or no
    synthesis -> NO_CONCLUYENTE;
  - FAVORABLE needs a bullish technical view, risk not ALTO and no material
    objection; DESFAVORABLE needs a bearish view or risk ALTO; otherwise a
    directional conclusion becomes NO_CONCLUYENTE;
  - the trading stance can only be the Python strategy's own direction or
    NO_OPERAR (NO_APLICA without a strategy signal): the AI never widens
    what the risk engine allows, and never creates an order or a trade;
  - qualitative confidence is capped at MEDIA when anything was missing,
    removed or failed, and is BAJA for NO_CONCLUYENTE;
  - without a valid synthesis the summary is a deterministic contingency
    summary assembled by Python (summary_source CONTINGENCIA_PYTHON), never
    presented as the AI's conclusion;
  - restatements of the deterministic committee's votes leave the AI lists
    (the report shows those votes apart), and the same idea restated by
    several agents is listed once.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Sequence

from investment_committee.integrations.llm import (
    LLMAuthenticationError,
    LLMBudgetExceededError,
    LLMClient,
    LLMError,
    LLMNotConfiguredError,
    LLMQuotaError,
    LLMResponseError,
    LLMTimeoutError,
    LLMUsage,
)

from . import prompts
from .evidence import EvidencePacket
from .grounding import GroundingChecker, ValidationIssue, VoteConfusionFilter
from .models import (
    AI_COMMITTEE_VERSION,
    AgentResult,
    AgentRole,
    AgentStatus,
    AICommitteeReport,
    FinalDecision,
    ValidationStatus,
)
from .schemas import (
    AgentOutputError,
    DevilsAdvocateOpinion,
    MacroOpinion,
    RiskOpinion,
    Synthesis,
    TechnicalOpinion,
    parse_devils_advocate,
    parse_macro,
    parse_risk,
    parse_synthesis,
    parse_technical,
)

_logger = logging.getLogger(__name__)
_FATAL = (LLMNotConfiguredError, LLMAuthenticationError, LLMQuotaError)
_FORMAT_ERROR = "la respuesta del modelo no cumplió el formato exigido"
_MAX_LISTED = 8
# Unusable replies worth ONE more request (with an instruction that targets
# the failure), inside the repair budget. Content filters, unknown finish
# reasons and broken HTTP bodies are not: asking again would not change them.
_RECOVERABLE = frozenset(
    {LLMResponseError.TRUNCATED, LLMResponseError.EMPTY_CONTENT, LLMResponseError.MALFORMED_JSON, LLMResponseError.NOT_OBJECT}
)
_REASONS = {
    LLMResponseError.TRUNCATED: "se cortó por el límite de longitud",
    LLMResponseError.EMPTY_CONTENT: "llegó vacía",
    LLMResponseError.MALFORMED_JSON: "no era JSON válido",
    LLMResponseError.NOT_OBJECT: "no era un objeto JSON",
    LLMResponseError.UNEXPECTED_FINISH: "el proveedor la interrumpió",
    LLMResponseError.INVALID_BODY: "la respuesta HTTP no tenía el formato del proveedor",
}


@dataclass(frozen=True)
class AnalysisBudget:
    max_calls: int = 5
    max_total_tokens: int = 40_000
    max_cost_usd: Decimal = Decimal("0.05")
    max_prompt_tokens: int = 12_000  # per call, estimated as characters / 3
    max_repairs: int = 2  # format-repair requests per analysis, within max_calls


class AICommitteeCoordinator:
    def __init__(
        self,
        llm: LLMClient,
        budget: AnalysisBudget | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self._llm = llm
        self._budget = budget or AnalysisBudget()
        self._clock = clock
        self._id_factory = id_factory

    def run(self, packet: EvidencePacket, deterministic_baseline: str | None = None) -> AICommitteeReport:
        run = _Run(self._llm, self._budget, packet)
        if packet.critical_missing:
            results = [AgentResult(role, AgentStatus.SKIPPED, None, "no hay datos suficientes para analizar") for role in AgentRole]
        else:
            run.planned = 4 + (1 if packet.macro_available else 0)
            technical = run.call("technical", AgentRole.TECHNICAL_ANALYST, parse_technical)
            macro = run.call("macro", AgentRole.MACRO_ANALYST, parse_macro) if packet.macro_available else _macro_not_available(packet)
            risk = run.call("risk", AgentRole.RISK_MANAGER, parse_risk, {"analista_tecnico": _as_data(technical)})
            devil = run.call(
                "devils_advocate",
                AgentRole.DEVILS_ADVOCATE,
                parse_devils_advocate,
                {"analista_tecnico": _as_data(technical), "analista_macro": _as_data(macro), "gestor_de_riesgo": _as_data(risk)},
            )
            synthesis = run.call(
                "synthesis",
                AgentRole.COORDINATOR,
                parse_synthesis,
                {
                    "analista_tecnico": _as_data(technical),
                    "analista_macro": _as_data(macro),
                    "gestor_de_riesgo": _as_data(risk),
                    "abogado_del_diablo": _as_data(devil),
                },
            )
            results = [technical, macro, risk, devil, synthesis]

        decision, controls, status = apply_controls(packet, results, run.issues)
        return AICommitteeReport(
            analysis_id=self._id_factory(),
            created_at=self._clock(),
            committee_version=AI_COMMITTEE_VERSION,
            provider=self._llm.provider_name,
            model=self._llm.model,
            evidence=packet,
            agents=results,
            decision=decision,
            controls=controls,
            issues=run.issues,
            validation_status=status,
            usage=run.usage,
            calls=run.calls,
            deterministic_baseline=deterministic_baseline,
        )


class _Run:
    def __init__(self, llm: LLMClient, budget: AnalysisBudget, packet: EvidencePacket) -> None:
        self.llm = llm
        self.budget = budget
        self.packet = packet
        self.evidence_text = packet.render()
        self.evidence_ids = packet.item_ids()
        self.filters = (GroundingChecker(self.evidence_text), VoteConfusionFilter())
        self.usage = LLMUsage(estimated_cost_usd=Decimal("0"))
        self.calls = 0
        self.planned = 0  # agent calls the coordinator intends to make
        self.started = 0  # agent calls attempted so far (repairs not counted)
        self.repairs = 0
        self.issues: list[ValidationIssue] = []
        self.fatal: str | None = None

    def call(self, key: str, role: AgentRole, parser: Callable[..., Any], prior: dict[str, Any] | None = None) -> AgentResult:
        if self.fatal is not None:
            return AgentResult(role, AgentStatus.SKIPPED, None, self.fatal)
        self.started += 1
        messages = prompts.messages_for(key, self.evidence_text, prior)
        try:
            self._check_budget(messages)
        except LLMBudgetExceededError as exc:
            _logger.info("AI committee budget reached before %s", role.value)
            return AgentResult(role, AgentStatus.SKIPPED_BUDGET, None, str(exc))

        response, failure, error = self._complete(role, messages)
        notes: list[str] = []
        usage, calls = LLMUsage(), 0
        first_diagnostic: str | None = None
        if failure is not None:
            if not (isinstance(error, LLMResponseError) and error.reason in _RECOVERABLE):
                return failure
            retry_messages = prompts.unusable_reply_messages(messages, error.reason)
            if not self._may_repair(retry_messages):
                return failure
            self.repairs += 1
            _logger.warning("ai_committee_recovery agent=%s reason=%s", role.value, error.reason)
            response, second, _ = self._complete(role, retry_messages)
            if second is not None:
                return dataclasses.replace(
                    second,
                    usage=failure.usage + second.usage,
                    calls=failure.calls + second.calls,
                    provider_diagnostic=f"{failure.provider_diagnostic} | tras reintento: {second.provider_diagnostic}",
                )
            usage, calls, first_diagnostic = failure.usage, failure.calls, failure.provider_diagnostic
            notes.append(f"la primera respuesta del proveedor fue inutilizable ({_REASONS.get(error.reason, error.reason)}); se pidió de nuevo y la segunda fue válida")
        assert response is not None
        usage, calls = usage + response.usage, calls + response.attempts
        recovered = bool(notes)

        try:
            opinion = parser(response.data, self.evidence_ids, notes)
        except AgentOutputError as first:
            _logger.warning("AI agent %s returned an invalid structure: %s", role.value, first)
            errors = [str(first)]
            repair = prompts.repair_messages(messages, response.data, str(first))
            if not self._may_repair(repair):
                return AgentResult(role, AgentStatus.FAILED, None, _FORMAT_ERROR, usage, calls, validation_errors=tuple(errors))
            self.repairs += 1
            retry, failure, _ = self._complete(role, repair)
            if failure is not None:
                return dataclasses.replace(failure, usage=usage + failure.usage, calls=calls + failure.calls, validation_errors=tuple(errors))
            assert retry is not None
            usage, calls = usage + retry.usage, calls + retry.attempts
            notes = notes[:1] if recovered else []
            try:
                opinion = parser(retry.data, self.evidence_ids, notes)
            except AgentOutputError as second:
                _logger.warning("AI agent %s repair was still invalid: %s", role.value, second)
                errors.append(str(second))
                return AgentResult(role, AgentStatus.FAILED, None, _FORMAT_ERROR + " (también tras una corrección)", usage, calls, validation_errors=tuple(errors))
            notes.insert(0, "la primera respuesta no cumplió el formato; se pidió una corrección y la segunda sí lo cumplió")
            return self._accepted(role, opinion, usage, calls, notes, repaired=True, errors=errors, diagnostic=first_diagnostic)
        return self._accepted(role, opinion, usage, calls, notes, repaired=recovered, diagnostic=first_diagnostic)

    def _accepted(
        self, role: AgentRole, opinion: Any, usage: LLMUsage, calls: int, notes: list[str], repaired: bool = False, errors: list[str] | None = None,
        diagnostic: str | None = None,
    ) -> AgentResult:
        for check in self.filters:
            opinion, issues = check.clean(opinion, role.value)
            self.issues.extend(issues)
        return AgentResult(role, AgentStatus.OK, opinion, None, usage, calls, repaired, tuple(errors or ()), tuple(notes), diagnostic)

    def _complete(self, role: AgentRole, messages: list) -> tuple[Any, AgentResult | None, LLMError | None]:  # type: ignore[type-arg]
        """One request to the provider, with its internal retries capped so
        the per-analysis call and token budgets still hold. Every attempt
        counts as a call and every billed token counts - failed ones too."""
        try:
            response = self.llm.complete_json(messages, max_attempts=self._attempt_allowance(messages), tag=role.value)
        except LLMError as exc:
            self.calls += max(1, exc.attempts)
            self.usage = self.usage + exc.usage
            diagnostic = _diagnostic(exc)
            _logger.warning("ai_committee_agent_failed agent=%s %s", role.value, diagnostic)
            text = _error_text(exc)
            if isinstance(exc, _FATAL):
                self.fatal = text
            return None, AgentResult(role, AgentStatus.FAILED, None, text, exc.usage, max(1, exc.attempts), provider_diagnostic=diagnostic), exc
        self.calls += response.attempts
        self.usage = self.usage + response.usage
        return response, None, None

    def _attempt_allowance(self, messages: list) -> int:  # type: ignore[type-arg]
        """Requests this call may spend (retries included) without starving
        the agents still to run of calls or tokens. Always at least one:
        _check_budget already admitted the first."""
        remaining = self.planned - self.started
        call_room = self.budget.max_calls - self.calls - remaining
        per_attempt = math.ceil(sum(len(m.content) for m in messages) / 3) + self.llm.max_output_tokens
        per_agent = math.ceil(len(self.evidence_text) / 3) + self.llm.max_output_tokens
        token_room = (self.budget.max_total_tokens - self.usage.total_tokens - remaining * per_agent) // per_attempt
        return max(1, min(call_room, token_room))

    def _may_repair(self, messages: list) -> bool:  # type: ignore[type-arg]
        """A repair is allowed only within max_repairs and only if the calls
        still planned for other agents remain affordable afterwards."""
        if self.repairs >= self.budget.max_repairs:
            return False
        if self.calls + 1 + (self.planned - self.started) > self.budget.max_calls:
            return False
        try:
            self._check_budget(messages)
        except LLMBudgetExceededError:
            return False
        # Reserve tokens for the agents still to run (each at least the size
        # of this conversation's evidence plus a full reply).
        remaining = self.planned - self.started
        per_agent = math.ceil(len(self.evidence_text) / 3) + self.llm.max_output_tokens
        repair_tokens = math.ceil(sum(len(m.content) for m in messages) / 3) + self.llm.max_output_tokens
        return self.usage.total_tokens + repair_tokens + remaining * per_agent <= self.budget.max_total_tokens

    def _check_budget(self, messages: list) -> None:  # type: ignore[type-arg]
        prompt_tokens = math.ceil(sum(len(m.content) for m in messages) / 3)
        worst_tokens = prompt_tokens + self.llm.max_output_tokens
        if self.calls + 1 > self.budget.max_calls:
            raise LLMBudgetExceededError(f"se alcanzó el máximo de {self.budget.max_calls} llamadas al modelo")
        if prompt_tokens > self.budget.max_prompt_tokens:
            raise LLMBudgetExceededError("la evidencia es demasiado larga para el límite de tokens por llamada")
        if self.usage.total_tokens + worst_tokens > self.budget.max_total_tokens:
            raise LLMBudgetExceededError("se alcanzaría el límite de tokens del análisis")
        worst_cost = self.llm.estimate_cost_usd(prompt_tokens, self.llm.max_output_tokens)
        spent = self.usage.estimated_cost_usd or Decimal("0")
        if worst_cost is not None and spent + worst_cost > self.budget.max_cost_usd:
            raise LLMBudgetExceededError("se superaría el presupuesto de costo del análisis")


def _error_text(exc: LLMError) -> str:
    if isinstance(exc, LLMTimeoutError):
        return "el proveedor de IA no respondió a tiempo"
    if isinstance(exc, LLMResponseError):
        return f"la respuesta del proveedor de IA no se pudo usar: {_REASONS.get(exc.reason, 'formato inválido')}"
    if isinstance(exc, LLMAuthenticationError):
        return "la clave del proveedor de IA fue rechazada"
    if isinstance(exc, LLMQuotaError):
        return "la cuenta del proveedor de IA no tiene saldo"
    if isinstance(exc, LLMNotConfiguredError):
        return "el proveedor de IA no está configurado"
    return "el proveedor de IA no estuvo disponible"


def _diagnostic(exc: LLMError) -> str:
    """Safe, structured description of a provider failure for the logs and
    the audit ledger: error type, reason, HTTP status, finish_reason,
    request id, attempts and billed tokens - never prompts or content."""
    info = exc.info
    parts = [f"error={type(exc).__name__}"]
    if isinstance(exc, LLMResponseError):
        parts.append(f"reason={exc.reason}")
    parts += [
        f"http_status={info.http_status if info.http_status is not None else '-'}",
        f"finish_reason={info.finish_reason or '-'}",
        f"model={info.model or '-'}",
        f"request_id={info.request_id or '-'}",
        f"attempts={exc.attempts}",
        f"prompt_tokens={exc.usage.prompt_tokens}",
        f"completion_tokens={exc.usage.completion_tokens}",
    ]
    return "proveedor: " + " ".join(parts)


def _macro_not_available(packet: EvidencePacket) -> AgentResult:
    opinion = MacroOpinion(
        status="NO_DISPONIBLE",
        summary=(
            "No hay un proveedor de noticias ni de calendario económico integrado, así que no se evaluaron tasas de "
            "interés, inflación, empleo, política monetaria ni noticias. El comité no usa conocimiento general del "
            "modelo como si fuera información actual."
        ),
        factors=(),
        missing_information=("tasas de interés", "inflación", "empleo", "política monetaria", "calendario económico", "noticias"),
        confidence="BAJA",
    )
    return AgentResult(AgentRole.MACRO_ANALYST, AgentStatus.NOT_AVAILABLE, opinion, f"fuente: {packet.macro_source}")


def _as_data(result: AgentResult) -> dict[str, Any]:
    if result.opinion is None:
        return {"estado": result.status.value, "detalle": result.error}
    return {"estado": result.status.value, **dataclasses.asdict(result.opinion)}


# --- deterministic controls ---


def apply_controls(
    packet: EvidencePacket, results: list[AgentResult], issues: list[ValidationIssue]
) -> tuple[FinalDecision, list[str], ValidationStatus]:
    by_role = {result.role: result for result in results}
    technical = _opinion(by_role, AgentRole.TECHNICAL_ANALYST, TechnicalOpinion)
    risk = _opinion(by_role, AgentRole.RISK_MANAGER, RiskOpinion)
    devil = _opinion(by_role, AgentRole.DEVILS_ADVOCATE, DevilsAdvocateOpinion)
    synthesis = _opinion(by_role, AgentRole.COORDINATOR, Synthesis)
    controls: list[str] = []

    failed = [r for r in results if r.status in (AgentStatus.FAILED, AgentStatus.SKIPPED, AgentStatus.SKIPPED_BUDGET)]
    if synthesis is None or technical is None:
        status = ValidationStatus.FAILED
    elif failed:
        status = ValidationStatus.PARTIAL
    else:
        status = ValidationStatus.COMPLETE

    conclusion = synthesis.conclusion if synthesis else "NO_CONCLUYENTE"
    model_conclusion = synthesis.conclusion if synthesis else None
    if packet.critical_missing:
        conclusion = _force(conclusion, "NO_CONCLUYENTE", controls, "faltan datos esenciales del instrumento")
    elif packet.stale:
        conclusion = _force(conclusion, "NO_CONCLUYENTE", controls, "los datos de mercado están desactualizados")
    elif technical is None:
        conclusion = _force(conclusion, "NO_CONCLUYENTE", controls, "no hubo una opinión técnica válida")
    elif synthesis is None:
        controls.append("no hubo una síntesis válida del coordinador: el resultado es NO_CONCLUYENTE")
    else:
        if conclusion == "FAVORABLE" and not (
            technical.stance == "ALCISTA" and (risk is None or risk.risk_level != "ALTO") and not (devil and devil.material_objection)
        ):
            conclusion = _force(conclusion, "NO_CONCLUYENTE", controls, "una conclusión FAVORABLE no es coherente con las opiniones técnica, de riesgo o la revisión crítica")
        elif conclusion == "DESFAVORABLE" and not (technical.stance == "BAJISTA" or (risk is not None and risk.risk_level == "ALTO")):
            conclusion = _force(conclusion, "NO_CONCLUYENTE", controls, "una conclusión DESFAVORABLE no es coherente con las opiniones técnica y de riesgo")

    trading = _trading_stance(packet, synthesis, controls)

    confidence = synthesis.confidence if synthesis else "BAJA"
    if conclusion == "NO_CONCLUYENTE":
        confidence = "BAJA"
    elif confidence == "ALTA" and (failed or issues or packet.missing or not packet.macro_available):
        confidence = "MEDIA"
        controls.append("la confianza se limitó a MEDIA porque falta información o se eliminaron afirmaciones")

    evidence_for = [a.text for a in synthesis.evidence_for] if synthesis else []
    evidence_against = [a.text for a in synthesis.evidence_against] if synthesis else []
    risks = [*(a.text for a in (risk.risks if risk else ())), *(technical.risks if technical else ())]
    disagreements = [*_detected_disagreements(packet, technical, risk, devil), *(synthesis.disagreements if synthesis else ())]
    # Restatements of the deterministic committee's votes are not AI
    # positions: they leave the AI lists, and the renderer shows the votes in
    # their own labelled section.
    lists = [evidence_for, evidence_against, risks, disagreements]
    moved = sum(1 for items in lists for item in items if is_vote_restatement(item))
    evidence_for, evidence_against, risks, disagreements = ([i for i in items if not is_vote_restatement(i)] for items in lists)
    if moved:
        controls.append(
            f"se separaron {moved} afirmación(es) que repetían votos del comité determinístico: se muestran aparte, no como opiniones de la IA"
        )
    if controls or issues:
        status = ValidationStatus.ADJUSTED if status is ValidationStatus.COMPLETE else status

    kept_for = dedupe_similar(evidence_for)
    kept_against = dedupe_similar(evidence_against, exclude=kept_for)

    decision = FinalDecision(
        conclusion=conclusion,
        trading_stance=trading,
        confidence=confidence,
        executive_summary=synthesis.executive_summary if synthesis else _fallback_summary(packet, results, conclusion, trading),
        beginner_explanation=synthesis.beginner_explanation if synthesis else _FALLBACK_BEGINNER,
        evidence_for=kept_for,
        evidence_against=kept_against,
        risks=dedupe_similar(risks, exclude=kept_against),
        missing_information=dedupe_missing(
            [
                *packet.missing,
                *(technical.missing_information if technical else ()),
                *(risk.missing_information if risk else ()),
                *((devil.insufficient_information) if devil else ()),
            ]
        ),
        disagreements=dedupe_similar(disagreements),
        change_conditions=dedupe_similar([*(synthesis.change_conditions if synthesis else ()), *(devil.invalidation if devil else ())]),
        model_conclusion=model_conclusion,
        summary_source="IA" if synthesis else "CONTINGENCIA_PYTHON",
    )
    return decision, controls, status


def _opinion(by_role: dict[AgentRole, AgentResult], role: AgentRole, kind: type) -> Any:
    result = by_role.get(role)
    return result.opinion if result is not None and isinstance(result.opinion, kind) else None


def _force(current: str, forced: str, controls: list[str], reason: str) -> str:
    if current != forced:
        controls.append(f"la conclusión {current} se cambió a {forced}: {reason}")
    else:
        controls.append(f"{forced}: {reason}")
    return forced


def _trading_stance(packet: EvidencePacket, synthesis: Synthesis | None, controls: list[str]) -> str:
    signal = packet.trading_signal
    if signal is None:
        allowed, default = {"NO_APLICA"}, "NO_APLICA"
    elif signal.direction == "LONG":
        allowed, default = {"COMPRA", "NO_OPERAR"}, "NO_OPERAR"
    elif signal.direction == "SHORT":
        allowed, default = {"VENTA", "NO_OPERAR"}, "NO_OPERAR"
    else:
        allowed, default = {"NO_OPERAR"}, "NO_OPERAR"
    proposed = synthesis.trading_stance if synthesis else default
    if proposed not in allowed:
        controls.append(
            f"la postura de trading {proposed} se cambió a {default}: el motor de riesgo de Python "
            f"{'no generó señal de trading para este instrumento' if signal is None else 'no permite esa operación'}"
        )
        return default
    if packet.stale and proposed in ("COMPRA", "VENTA"):
        controls.append("la postura de trading se cambió a NO_OPERAR: los datos están desactualizados")
        return "NO_OPERAR"
    return proposed


def _detected_disagreements(
    packet: EvidencePacket, technical: TechnicalOpinion | None, risk: RiskOpinion | None, devil: DevilsAdvocateOpinion | None
) -> list[str]:
    found = []
    if technical and risk and technical.stance == "ALCISTA" and risk.risk_level == "ALTO":
        found.append("El analista técnico ve un escenario alcista, pero el gestor de riesgo califica el riesgo como ALTO.")
    signal = packet.trading_signal
    if technical and signal:
        if (technical.stance == "ALCISTA" and signal.direction == "SHORT") or (technical.stance == "BAJISTA" and signal.direction == "LONG"):
            found.append("La postura del analista técnico contradice la señal de la estrategia Python.")
    if devil and devil.material_objection:
        detail = next(iter([*devil.contradictions, *devil.challenged_assumptions]), "sin detalle")
        found.append(f"El abogado del diablo planteó una objeción material: {detail}")
    return found


_FALLBACK_BEGINNER = (
    "El comité de IA no pudo completar su análisis, así que no hay una conclusión. Este resumen lo armó Python con "
    "las respuestas que sí llegaron; no es una opinión de la IA. Cuando falta información, lo prudente es no sacar "
    "conclusiones ni operar."
)
_STANCE_TEXT = {"NO_OPERAR": "NO OPERAR", "NO_APLICA": "no aplica", "COMPRA": "COMPRA", "VENTA": "VENTA"}
_ROLE_NAMES = {
    AgentRole.TECHNICAL_ANALYST: "analista técnico",
    AgentRole.MACRO_ANALYST: "analista macroeconómico",
    AgentRole.RISK_MANAGER: "gestor de riesgo",
    AgentRole.DEVILS_ADVOCATE: "abogado del diablo",
    AgentRole.COORDINATOR: "coordinador",
}


def _fallback_summary(packet: EvidencePacket, results: list[AgentResult], conclusion: str, trading: str) -> str:
    """Deterministic contingency summary when there is no AI synthesis:
    assembled by Python from the structured fields that did arrive, and
    never worded as the committee's own conclusion."""
    if packet.critical_missing:
        return "No hay datos de mercado suficientes para que el comité de IA analice este instrumento."
    answered, failed = [], []
    for result in results:
        name = _ROLE_NAMES[result.role]
        opinion = result.opinion
        if result.status is AgentStatus.OK and isinstance(opinion, TechnicalOpinion):
            answered.append(f"{name}: postura {opinion.stance}")
        elif result.status is AgentStatus.OK and isinstance(opinion, RiskOpinion):
            answered.append(f"{name}: riesgo {opinion.risk_level}")
        elif result.status is AgentStatus.OK and isinstance(opinion, DevilsAdvocateOpinion):
            answered.append(f"{name}: {'planteó una objeción material' if opinion.material_objection else 'sin objeción material'}")
        elif result.status in (AgentStatus.FAILED, AgentStatus.SKIPPED, AgentStatus.SKIPPED_BUDGET):
            failed.append(f"{name} ({result.error or 'sin detalle'})")
    parts = ["El coordinador de IA no produjo una síntesis válida, así que no hay conclusión de la IA."]
    if answered:
        parts.append("Respuestas que sí llegaron: " + "; ".join(answered) + ".")
    if failed:
        parts.append("Sin respuesta válida: " + "; ".join(failed) + ".")
    if packet.trading_signal is not None:
        parts.append(f"Señal de la estrategia Python: {packet.trading_signal.direction}.")
    parts.append(
        f"Por regla, sin síntesis el resultado es {conclusion.replace('_', ' ')} y la postura de trading {_STANCE_TEXT.get(trading, trading)}."
    )
    return " ".join(parts)


_VOTE_WORDS = ("vota", "voto", "votó", "decidió", "decide ", "a favor", "en contra", "mantener")


def is_vote_restatement(text: str) -> bool:
    """A sentence about the deterministic (rules) committee's votes or
    decision: Python facts the report already shows in their own section."""
    lowered = text.lower()
    return ("determinístic" in lowered or "deterministic" in lowered) and any(word in lowered for word in _VOTE_WORDS)


_CITATION = re.compile(r"^e\d+$")  # evidence ids (E11) do not make two sentences different
_STOP = frozenset(
    "de del la las el los y o en no un una para por con sin se su sus que es son al lo este esta como mas más hay ni "
    "fue esta este análisis analisis datos dato información informacion disponible disponibles pero tambien mientras "
    "entre puede pueden".split()
)
# Words that name one kind of missing information. Two entries naming the
# same topic say the same thing ("volumen: Yahoo no lo publica" / "Volumen
# de negociación no publicado").
_TOPICS = {
    "volume": ("volumen", "volume"),
    "macro": ("noticia", "macro", "tasa", "interes", "inflaci", "empleo", "calendario", "monetari", "document", "bancos centrales"),
    "intraday": ("1 hora", "intradia", "intradía", "intradiario"),
}


def _plain_words(text: str) -> tuple[str, frozenset[str]]:
    import unicodedata

    lowered = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    words = frozenset(w[:5] for w in re.findall(r"[a-z0-9]+", lowered) if w not in _STOP and len(w) > 2 and not _CITATION.match(w))
    return lowered, words


def dedupe_missing(items: list[str]) -> tuple[str, ...]:
    """Missing-information entries without repeats: exact, by topic (volume,
    macro/news, intraday data), or by heavy word overlap. The first wording
    wins, so the evidence packet's own entries are kept over agents'
    paraphrases of them."""
    kept: list[str] = []
    seen_topics: set[str] = set()
    seen_words: list[frozenset[str]] = []
    for item in items:
        if not item:
            continue
        lowered, words = _plain_words(item)
        topics = {name for name, keys in _TOPICS.items() if any(key in lowered for key in keys)}
        if topics and topics <= seen_topics:
            continue
        if any(words and other and len(words & other) / min(len(words), len(other)) >= 0.7 for other in seen_words):
            continue
        kept.append(item)
        seen_topics |= topics
        seen_words.append(words)
    return tuple(kept)[:_MAX_LISTED]


def _similar(a: frozenset[str], b: frozenset[str]) -> bool:
    if min(len(a), len(b)) < 3:
        return a == b
    return len(a & b) / min(len(a), len(b)) >= 0.7


def dedupe_similar(items: Sequence[str], exclude: Sequence[str] = ()) -> tuple[str, ...]:
    """Items without repeats: the same idea restated by another agent (the
    same content words once accents, evidence ids such as (E11) and filler
    words are ignored) is kept once, in its first wording. Items similar to
    one in `exclude` (another section of the report) are dropped too."""
    seen = [_plain_words(item)[1] for item in exclude if item]
    kept: list[str] = []
    for item in items:
        if not item:
            continue
        words = _plain_words(item)[1]
        if any(_similar(words, other) for other in seen):
            continue
        kept.append(item)
        seen.append(words)
    return tuple(kept)[:_MAX_LISTED]
