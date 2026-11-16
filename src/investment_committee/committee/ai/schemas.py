"""Strict schemas for the AI agents' JSON replies.

Each parser checks required keys, types, enum values, list sizes and text
lengths, and raises AgentOutputError naming the field and the exact
violation. Keys the schema does not define are ignored and never used.
Arguments must cite evidence ids that exist in the packet; citations of
unknown ids are dropped (and the argument with them, if nothing else
supports it).

A few shapes are normalised because their meaning is unambiguous, and
every normalisation is reported in `notes` (shown to the user and kept in
the audit trail) - nothing is invented, reordered, or truncated:
- a text list given as one string -> a one-element list;
- a text list given as null -> an empty list;
- a text-list item given as {"texto": ..., "evidencia": [...]} (the
  argument shape) -> its text, with the cited ids that exist appended,
  e.g. "... (E5, E7)";
- an argument given as a bare string -> discarded, since it cites no
  evidence (the same rule as an argument citing unknown ids).
Anything else - too many items, numbers, nested lists, objects with other
keys - is an error, never guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

MAX_TEXT = 600
MAX_ITEMS = 6
CONFIDENCE = ("BAJA", "MEDIA", "ALTA")


class AgentOutputError(ValueError):
    """The model's JSON does not match the agent's schema. The message names
    the field and the violation only - never the model's content - so it
    is safe to log, store, and send back to the model in a repair request."""


Notes = list[str] | None


@dataclass(frozen=True)
class Argument:
    text: str
    evidence: Sequence[str]  # evidence ids, e.g. ("E3", "E7")


@dataclass(frozen=True)
class TechnicalOpinion:
    stance: str  # ALCISTA | BAJISTA | NEUTRAL
    bullish_scenario: str
    bearish_scenario: str
    neutral_scenario: str
    arguments: Sequence[Argument]
    risks: Sequence[str]
    missing_information: Sequence[str]
    confidence: str


@dataclass(frozen=True)
class MacroOpinion:
    status: str  # DISPONIBLE | NO_DISPONIBLE
    summary: str
    factors: Sequence[Argument]
    missing_information: Sequence[str]
    confidence: str


@dataclass(frozen=True)
class RiskOpinion:
    risk_level: str  # BAJO | MEDIO | ALTO
    risks: Sequence[Argument]
    engine_comment: str
    missing_information: Sequence[str]
    confidence: str


@dataclass(frozen=True)
class DevilsAdvocateOpinion:
    contradictions: Sequence[str]
    challenged_assumptions: Sequence[str]
    insufficient_information: Sequence[str]
    adverse_scenarios: Sequence[str]
    invalidation: Sequence[str]
    material_objection: bool
    confidence: str


@dataclass(frozen=True)
class Synthesis:
    conclusion: str  # FAVORABLE | DESFAVORABLE | NEUTRAL | NO_CONCLUYENTE
    trading_stance: str  # COMPRA | VENTA | NO_OPERAR | NO_APLICA
    executive_summary: str
    evidence_for: Sequence[Argument]
    evidence_against: Sequence[Argument]
    disagreements: Sequence[str]
    change_conditions: Sequence[str]
    beginner_explanation: str
    confidence: str


Opinion = TechnicalOpinion | MacroOpinion | RiskOpinion | DevilsAdvocateOpinion | Synthesis


# --- primitives ---


def _text(data: Mapping[str, Any], key: str, *, required: bool = True) -> str:
    value = data.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise AgentOutputError(f"'{key}' must be a non-empty string")
    if len(value) > MAX_TEXT:
        raise AgentOutputError(f"'{key}' is longer than {MAX_TEXT} characters")
    return value.strip()


def _enum(data: Mapping[str, Any], key: str, allowed: Sequence[str]) -> str:
    value = data.get(key)
    if not isinstance(value, str) or value.strip().upper() not in allowed:
        raise AgentOutputError(f"'{key}' must be one of {', '.join(allowed)}")
    return value.strip().upper()


def _note(notes: Notes, text: str) -> None:
    if notes is not None:
        notes.append(text)


def _texts(data: Mapping[str, Any], key: str, evidence_ids: frozenset[str] = frozenset(), notes: Notes = None) -> tuple[str, ...]:
    value = data.get(key, [])
    if value is None:
        _note(notes, f"'{key}': llegó vacío (null) y se tomó como lista vacía")
        return ()
    if isinstance(value, str):
        if len(value) > MAX_TEXT:
            raise AgentOutputError(f"'{key}' is a single string longer than {MAX_TEXT} characters; expected a list of strings")
        _note(notes, f"'{key}': llegó como un texto suelto y se tomó como lista de un elemento")
        value = [value]
    if not isinstance(value, list):
        raise AgentOutputError(f"'{key}' is a {_kind(value)}; expected a list of at most {MAX_ITEMS} strings")
    if len(value) > MAX_ITEMS:
        raise AgentOutputError(f"'{key}' has {len(value)} items; at most {MAX_ITEMS} are allowed")
    out = []
    converted = 0
    for index, item in enumerate(value, start=1):
        if isinstance(item, Mapping) and set(item) <= {"texto", "evidencia"} and isinstance(item.get("texto"), str):
            cited = item.get("evidencia", [])
            if not isinstance(cited, list) or not all(isinstance(c, str) for c in cited):
                raise AgentOutputError(f"'{key}' item {index} has an 'evidencia' that is not a list of ids")
            known = [c.strip().upper() for c in cited if c.strip().upper() in evidence_ids]
            item = item["texto"].strip() + (f" ({', '.join(dict.fromkeys(known))})" if known else "")
            converted += 1
        if not isinstance(item, str):
            raise AgentOutputError(f"'{key}' item {index} is a {_kind(item)}; expected a string")
        if len(item) > MAX_TEXT:
            raise AgentOutputError(f"'{key}' item {index} is longer than {MAX_TEXT} characters")
        if item.strip():
            out.append(item.strip())
    if converted:
        _note(notes, f"'{key}': {converted} elemento(s) con formato {{texto, evidencia}} se convirtieron a texto")
    return tuple(out)


def _arguments(data: Mapping[str, Any], key: str, evidence_ids: frozenset[str], notes: Notes = None) -> tuple[Argument, ...]:
    value = data.get(key, [])
    if value is None:
        _note(notes, f"'{key}': llegó vacío (null) y se tomó como lista vacía")
        return ()
    if not isinstance(value, list):
        raise AgentOutputError(f"'{key}' is a {_kind(value)}; expected a list of at most {MAX_ITEMS} arguments")
    if len(value) > MAX_ITEMS:
        raise AgentOutputError(f"'{key}' has {len(value)} items; at most {MAX_ITEMS} are allowed")
    arguments = []
    for index, item in enumerate(value, start=1):
        if isinstance(item, str):
            _note(notes, f"'{key}': se descartó un argumento sin evidencia citada")
            continue
        if not isinstance(item, Mapping):
            raise AgentOutputError(f"'{key}' item {index} is a {_kind(item)}; expected an object with 'texto' and 'evidencia'")
        text = _text(item, "texto")
        cited = item.get("evidencia", [])
        if not isinstance(cited, list) or not all(isinstance(c, str) for c in cited):
            raise AgentOutputError(f"'{key}' item {index} has an 'evidencia' that is not a list of ids")
        known = tuple(dict.fromkeys(c.strip().upper() for c in cited if c.strip().upper() in evidence_ids))
        if known:  # an argument citing no real evidence is not kept
            arguments.append(Argument(text=text, evidence=known))
    return tuple(arguments)


def _kind(value: Any) -> str:
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "list"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def _bool(data: Mapping[str, Any], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise AgentOutputError(f"'{key}' must be true or false")
    return value


def _object(data: Any) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise AgentOutputError("the reply must be a JSON object")
    return data


# --- parsers ---


def parse_technical(data: Any, evidence_ids: frozenset[str], notes: Notes = None) -> TechnicalOpinion:
    data = _object(data)
    return TechnicalOpinion(
        stance=_enum(data, "postura", ("ALCISTA", "BAJISTA", "NEUTRAL")),
        bullish_scenario=_text(data, "escenario_alcista"),
        bearish_scenario=_text(data, "escenario_bajista"),
        neutral_scenario=_text(data, "escenario_neutral"),
        arguments=_arguments(data, "argumentos", evidence_ids, notes),
        risks=_texts(data, "riesgos", evidence_ids, notes),
        missing_information=_texts(data, "informacion_faltante", evidence_ids, notes),
        confidence=_enum(data, "confianza", CONFIDENCE),
    )


def parse_macro(data: Any, evidence_ids: frozenset[str], notes: Notes = None) -> MacroOpinion:
    data = _object(data)
    return MacroOpinion(
        status=_enum(data, "estado", ("DISPONIBLE", "NO_DISPONIBLE")),
        summary=_text(data, "resumen"),
        factors=_arguments(data, "factores", evidence_ids, notes),
        missing_information=_texts(data, "informacion_faltante", evidence_ids, notes),
        confidence=_enum(data, "confianza", CONFIDENCE),
    )


def parse_risk(data: Any, evidence_ids: frozenset[str], notes: Notes = None) -> RiskOpinion:
    data = _object(data)
    return RiskOpinion(
        risk_level=_enum(data, "nivel_riesgo", ("BAJO", "MEDIO", "ALTO")),
        risks=_arguments(data, "riesgos", evidence_ids, notes),
        engine_comment=_text(data, "comentario_motor"),
        missing_information=_texts(data, "informacion_faltante", evidence_ids, notes),
        confidence=_enum(data, "confianza", CONFIDENCE),
    )


def parse_devils_advocate(data: Any, evidence_ids: frozenset[str], notes: Notes = None) -> DevilsAdvocateOpinion:
    data = _object(data)
    return DevilsAdvocateOpinion(
        contradictions=_texts(data, "contradicciones", evidence_ids, notes),
        challenged_assumptions=_texts(data, "supuestos_cuestionados", evidence_ids, notes),
        insufficient_information=_texts(data, "informacion_insuficiente", evidence_ids, notes),
        adverse_scenarios=_texts(data, "escenarios_adversos", evidence_ids, notes),
        invalidation=_texts(data, "invalidacion", evidence_ids, notes),
        material_objection=_bool(data, "objecion_material"),
        confidence=_enum(data, "confianza", CONFIDENCE),
    )


def parse_synthesis(data: Any, evidence_ids: frozenset[str], notes: Notes = None) -> Synthesis:
    data = _object(data)
    return Synthesis(
        conclusion=_enum(data, "conclusion", ("FAVORABLE", "DESFAVORABLE", "NEUTRAL", "NO_CONCLUYENTE")),
        trading_stance=_enum(data, "postura_trading", ("COMPRA", "VENTA", "NO_OPERAR", "NO_APLICA")),
        executive_summary=_text(data, "resumen_ejecutivo"),
        evidence_for=_arguments(data, "evidencia_a_favor", evidence_ids, notes),
        evidence_against=_arguments(data, "evidencia_en_contra", evidence_ids, notes),
        disagreements=_texts(data, "desacuerdos", evidence_ids, notes),
        change_conditions=_texts(data, "condiciones_de_cambio", evidence_ids, notes),
        beginner_explanation=_text(data, "explicacion_principiante"),
        confidence=_enum(data, "confianza", CONFIDENCE),
    )
