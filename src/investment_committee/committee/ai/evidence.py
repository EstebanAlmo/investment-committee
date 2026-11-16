"""EvidencePacket: the shared, Python-computed facts every AI agent sees.

Every number an agent may cite is an EvidenceItem with an id (E1, E2, ...),
a source, and a timestamp. External documents (news, macro releases) are
kept apart, sanitised, and rendered inside an explicitly untrusted block.
The packet is built by application/ - this module only holds and renders it.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

from investment_committee.market_intelligence.macro import ExternalDocument

MAX_DOCUMENT_CHARS = 1200
MAX_DOCUMENTS = 8


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    category: str  # e.g. "técnico diario", "técnico 1 hora", "riesgo", "estrategia"
    label: str
    value: Decimal | str
    unit: str  # "", "%", "pips", "USD", "velas"
    source: str
    as_of: datetime | None
    places: int = 4

    def display_value(self) -> str:
        if isinstance(self.value, Decimal):
            text = f"{self.value:.{self.places}f}".replace(".", ",")
            return f"{text} {self.unit}".strip()
        return self.value


@dataclass(frozen=True)
class TradingSignalSummary:
    """The existing Python strategy's live reading - shown to the agents as
    evidence; the AI can never widen what it allows."""

    direction: str  # LONG | SHORT | NO_TRADE
    reasons: Sequence[str]  # Spanish explanations
    strategy: str


@dataclass(frozen=True)
class EvidencePacket:
    instrument: str  # canonical symbol, e.g. "EURUSD=X"
    display_name: str  # e.g. "EUR/USD"
    asset_class: str  # "forex" | "acciones/ETF"
    generated_at: datetime
    data_as_of: datetime | None  # latest market data used
    stale: bool
    freshness_note: str
    items: Sequence[EvidenceItem]
    missing: Sequence[str]  # information the system could not provide
    critical_missing: bool  # not enough data for any conclusion
    trading_signal: TradingSignalSummary | None
    macro_source: str
    documents: Sequence[ExternalDocument] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "missing", tuple(self.missing))
        object.__setattr__(self, "documents", tuple(self.documents[:MAX_DOCUMENTS]))

    @property
    def macro_available(self) -> bool:
        return bool(self.documents)

    def item_ids(self) -> frozenset[str]:
        return frozenset(item.id for item in self.items)

    def render(self) -> str:
        """The packet as the agents read it. Python values only; external
        text appears solely inside the untrusted block."""
        lines = [
            f"Instrumento: {self.display_name} ({self.instrument}) · clase: {self.asset_class}",
            f"Generado: {_when(self.generated_at)} · datos hasta: {_when(self.data_as_of) if self.data_as_of else 'sin datos'}",
            f"Frescura: {self.freshness_note}",
            "",
            "<evidencia_python>",
        ]
        for item in self.items:
            as_of = f" · {_when(item.as_of)}" if item.as_of else ""
            lines.append(f"{item.id} | {item.category} | {item.label} = {item.display_value()} | fuente: {item.source}{as_of}")
        lines.append("</evidencia_python>")
        if self.trading_signal is not None:
            lines += ["", f"Señal de la estrategia Python ({self.trading_signal.strategy}): {self.trading_signal.direction}"]
            lines += [f"- {reason}" for reason in self.trading_signal.reasons]
        lines += ["", "Información NO disponible en este análisis:"]
        lines += [f"- {gap}" for gap in self.missing] or ["- (ninguna)"]
        lines += ["", f"Fuente macroeconómica y de noticias: {self.macro_source}"]
        if self.documents:
            lines.append("<documentos_externos_no_confiables>")
            for number, document in enumerate(self.documents, start=1):
                lines.append(
                    f"[D{number}] fuente: {sanitize_external_text(document.source, 80)} · recuperado: {_when(document.retrieved_at)} · "
                    f"título: {sanitize_external_text(document.title, 200)}"
                )
                lines.append(f"«{sanitize_external_text(document.text, MAX_DOCUMENT_CHARS)}»")
            lines.append("</documentos_externos_no_confiables>")
        else:
            lines.append("(no se recuperó ningún documento macroeconómico ni noticia)")
        return "\n".join(lines)


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def sanitize_external_text(text: str, limit: int) -> str:
    """Untrusted text, made inert for the prompt: normalised, control
    characters removed, angle brackets and guillemets replaced so it cannot
    close or forge the evidence blocks, whitespace collapsed, truncated."""
    normalised = unicodedata.normalize("NFKC", text)
    cleaned = _CONTROL.sub(" ", normalised)
    for char, replacement in (("<", "‹"), (">", "›"), ("«", '"'), ("»", '"')):
        cleaned = cleaned.replace(char, replacement)
    collapsed = " ".join(cleaned.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def _when(moment: datetime) -> str:
    return f"{moment.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC"
