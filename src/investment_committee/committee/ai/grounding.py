"""Numeric grounding: every number an agent writes must come from the
evidence packet.

A number is grounded when some number shown in the packet, rounded to the
precision the agent used, equals it (1,11957 in the packet grounds "1,12";
it does not ground "1,13"). Dates and times are ignored; small counts
(0-10) are allowed. Text containing an ungrounded number is removed - a
sentence from free text, a whole item from a list, a whole argument - and
recorded as a ValidationIssue. Nothing is ever rewritten to a "corrected"
number.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, TypeVar

from .schemas import Argument

_DATE_OR_TIME = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}(?::\d{2})?")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_THOUSANDS = re.compile(r"^\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?$")
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")
_ALWAYS_ALLOWED = frozenset(Decimal(n) for n in range(0, 11))

T = TypeVar("T")


@dataclass(frozen=True)
class ValidationIssue:
    agent: str
    field: str
    detail: str  # Spanish, safe to show


def numbers_in(text: str) -> list[tuple[str, list[Decimal]]]:
    """Each number token with its possible readings. One separator: a
    decimal point or comma, or - when exactly three digits follow - a
    thousands separator ("1.119" may be either). Several separators: the
    Spanish/English thousands forms "10.000", "10.000,50", "1,234.5"."""
    stripped = _DATE_OR_TIME.sub(" ", text)
    found = []
    for token in _NUMBER.findall(stripped):
        positions = [i for i, c in enumerate(token) if c in ".,"]
        readings: list[Decimal] = []
        if not positions:
            readings.append(Decimal(token))
        elif len(positions) == 1:
            head, tail = token[: positions[0]], token[positions[0] + 1 :]
            readings.append(Decimal(f"{head}.{tail}"))
            if len(tail) == 3:
                readings.append(Decimal(head + tail))
        elif _THOUSANDS.match(token):
            last = positions[-1]
            separators = {token[i] for i in positions}
            tail = token[last + 1 :]
            if len(separators) == 1 and len(tail) == 3:
                readings.append(Decimal(re.sub(r"[.,]", "", token)))
            else:
                readings.append(Decimal(re.sub(r"[.,]", "", token[:last]) + "." + tail))
        found.append((token, readings))
    return found


class _TextFilter:
    """Removes text that fails `problem()`: a sentence from free text, an
    item from a text list, a whole argument. Never rewrites what remains."""

    removed_text = "(contenido eliminado por los controles de validación)"

    def problem(self, text: str) -> str | None:
        raise NotImplementedError

    def clean(self, opinion: T, agent: str) -> tuple[T, list[ValidationIssue]]:
        issues: list[ValidationIssue] = []
        changes = {}
        for field in dataclasses.fields(opinion):  # type: ignore[arg-type]
            value = getattr(opinion, field.name)
            if isinstance(value, str):
                kept = self._clean_text(value, agent, field.name, issues)
            elif isinstance(value, tuple) and value and isinstance(value[0], Argument):
                kept = tuple(self._keep(value, agent, field.name, issues, lambda a: a.text))
            elif isinstance(value, tuple) and value and isinstance(value[0], str):
                kept = tuple(self._keep(value, agent, field.name, issues, lambda s: s))
            else:
                continue
            if kept != value:
                changes[field.name] = kept
        return (dataclasses.replace(opinion, **changes) if changes else opinion), issues  # type: ignore[type-var]

    def _clean_text(self, text: str, agent: str, field: str, issues: list[ValidationIssue]) -> str:
        kept = []
        for sentence in _SENTENCE_END.split(text):
            problem = self.problem(sentence)
            if problem:
                issues.append(ValidationIssue(agent, field, f"se eliminó una afirmación: {problem}"))
            else:
                kept.append(sentence)
        result = " ".join(kept).strip()
        return result if result else self.removed_text

    def _keep(self, items: Iterable[T], agent: str, field: str, issues: list[ValidationIssue], text_of) -> list[T]:  # type: ignore[no-untyped-def]
        kept = []
        for item in items:
            problem = self.problem(text_of(item))
            if problem:
                issues.append(ValidationIssue(agent, field, f"se eliminó un punto: {problem}"))
            else:
                kept.append(item)
        return kept


class GroundingChecker(_TextFilter):
    removed_text = "(contenido eliminado: contenía cifras sin respaldo en la evidencia)"

    def __init__(self, evidence_text: str) -> None:
        self._allowed: set[Decimal] = set(_ALWAYS_ALLOWED)
        for _, readings in numbers_in(evidence_text):
            self._allowed.update(readings)

    def is_grounded(self, reading: Decimal) -> bool:
        exponent = reading.as_tuple().exponent
        places = -exponent if isinstance(exponent, int) and exponent < 0 else 0
        quantum = Decimal(1).scaleb(-places)
        return any(allowed.quantize(quantum) == reading for allowed in self._allowed if abs(allowed) < Decimal("1e12"))

    def ungrounded(self, text: str) -> list[str]:
        return [token for token, readings in numbers_in(text) if readings and not any(self.is_grounded(r) for r in readings)]

    def problem(self, text: str) -> str | None:
        bad = self.ungrounded(text)
        return f"tenía cifras sin respaldo en la evidencia ({', '.join(bad)})" if bad else None


_VOTE_LABEL = re.compile(r"\b(A FAVOR|EN CONTRA)\b")
_DETERMINISTIC_MENTION = re.compile(r"determin[ií]st|\breglas?\b|/analyze", re.IGNORECASE)


class VoteConfusionFilter(_TextFilter):
    """The deterministic committee votes A FAVOR / EN CONTRA (rules met or
    not); the AI agents never use those labels - their stances are
    ALCISTA/BAJISTA/NEUTRAL and risk BAJO/MEDIO/ALTO. A statement using a
    vote label without saying it refers to the deterministic committee (or
    its rules) is mixing the two and is removed rather than shown as an AI
    agent's position."""

    def problem(self, text: str) -> str | None:
        if _VOTE_LABEL.search(text) and not _DETERMINISTIC_MENTION.search(text):
            return "confundía un voto del comité determinístico (A FAVOR / EN CONTRA) con una postura del comité IA"
        return None
