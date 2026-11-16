"""Fakes for AI committee tests: a scripted LLM and evidence packets. No
network, no paid calls. Not collected by pytest."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from investment_committee.committee.ai import EvidenceItem, EvidencePacket, TradingSignalSummary
from investment_committee.integrations.llm import LLMClient, LLMError, LLMMessage, LLMResponse, LLMUsage
from investment_committee.market_intelligence.macro import ExternalDocument

NOW = datetime(2026, 10, 13, 17, 0, tzinfo=timezone.utc)
ROLE_MARKERS = {
    "ROL: ANALISTA_TECNICO": "technical",
    "ROL: ANALISTA_MACROECONOMICO": "macro",
    "ROL: GESTOR_DE_RIESGO": "risk",
    "ROL: ABOGADO_DEL_DIABLO": "devils_advocate",
    "ROL: COORDINADOR_DEL_COMITE": "synthesis",
}

GOOD_REPLIES: dict[str, dict[str, Any]] = {
    "technical": {
        "postura": "ALCISTA",
        "escenario_alcista": "Si el precio se mantiene sobre la SMA50 la tendencia continúa.",
        "escenario_bajista": "Una caída bajo la SMA50 debilitaría la tendencia.",
        "escenario_neutral": "Un RSI cerca de 50 indicaría pausa.",
        "argumentos": [
            {"texto": "El precio de 1,11957 está sobre la SMA50 de 1,1050.", "evidencia": ["E1", "E3"]},
            {"texto": "El RSI14 de 55,62 muestra impulso comprador sin exceso.", "evidencia": ["E4"]},
        ],
        "riesgos": ["La volatilidad puede cambiar con noticias."],
        "informacion_faltante": ["No hay datos de volumen."],
        "confianza": "MEDIA",
    },
    "macro": {
        "estado": "DISPONIBLE",
        "resumen": "El documento recuperado menciona una decisión de tasas.",
        "factores": [{"texto": "Hay un documento sobre tasas.", "evidencia": ["E1"]}],
        "informacion_faltante": ["inflación"],
        "confianza": "BAJA",
    },
    "risk": {
        "nivel_riesgo": "MEDIO",
        "riesgos": [{"texto": "El ATR de 9,5 pips implica movimientos moderados.", "evidencia": ["E5"]}],
        "comentario_motor": "El motor de Python permite una compra con stop y objetivo definidos.",
        "informacion_faltante": [],
        "confianza": "MEDIA",
    },
    "devils_advocate": {
        "contradicciones": [],
        "supuestos_cuestionados": ["La tendencia podría ser solo un rebote."],
        "informacion_insuficiente": ["Sin datos macroeconómicos."],
        "escenarios_adversos": ["Un dato de inflación inesperado."],
        "invalidacion": ["Un cierre por debajo de la SMA50 invalidaría la hipótesis."],
        "objecion_material": False,
        "confianza": "MEDIA",
    },
    "synthesis": {
        "conclusion": "FAVORABLE",
        "postura_trading": "COMPRA",
        "resumen_ejecutivo": "La tendencia es alcista y el riesgo es moderado.",
        "evidencia_a_favor": [{"texto": "Precio sobre la SMA50.", "evidencia": ["E1", "E3"]}],
        "evidencia_en_contra": [{"texto": "Sin información macro.", "evidencia": ["E1"]}],
        "desacuerdos": [],
        "condiciones_de_cambio": ["Una caída bajo la SMA50."],
        "explicacion_principiante": "El precio viene subiendo, pero nada garantiza que siga.",
        "confianza": "MEDIA",
    },
}


def replies(**overrides: Any) -> dict[str, Any]:
    scripted = copy.deepcopy(GOOD_REPLIES)
    for role, value in overrides.items():
        scripted[role] = value if callable(value) or isinstance(value, Exception) or not isinstance(value, dict) else {**scripted[role], **value}
    return scripted


class FakeLLM(LLMClient):
    provider_name = "fake"

    def __init__(self, scripted: dict[str, Any] | None = None, cost_per_call: Decimal | None = Decimal("0.001"), max_output: int = 900) -> None:
        self.scripted = scripted if scripted is not None else replies()
        self.calls: list[tuple[str, list[LLMMessage]]] = []
        self.max_attempts: list[int | None] = []
        self._cost = cost_per_call
        self._max_output = max_output

    @property
    def model(self) -> str:
        return "fake-model"

    @property
    def max_output_tokens(self) -> int:
        return self._max_output

    def estimate_cost_usd(self, prompt_tokens: int, completion_tokens: int) -> Decimal | None:
        return self._cost

    def complete_json(
        self, messages: list[LLMMessage], max_output_tokens: int | None = None, *, max_attempts: int | None = None, tag: str | None = None
    ) -> LLMResponse:
        role = next(key for marker, key in ROLE_MARKERS.items() if marker in messages[0].content)
        self.calls.append((role, messages))
        self.max_attempts.append(max_attempts)
        reply = self.scripted[role]
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            reply = reply(messages)
        return LLMResponse(data=reply, usage=LLMUsage(1000, 200, 0, 1000, self._cost), model="fake-model", attempts=1)


def packet(
    *,
    stale: bool = False,
    critical_missing: bool = False,
    direction: str | None = "LONG",
    documents: tuple[ExternalDocument, ...] = (),
    missing: tuple[str, ...] = ("volumen (Yahoo no lo publica para Forex)",),
) -> EvidencePacket:
    def item(number: int, label: str, value: str, unit: str = "", places: int = 4) -> EvidenceItem:
        return EvidenceItem(f"E{number}", "técnico diario", label, Decimal(value), unit, "Yahoo Finance", NOW, places)

    return EvidencePacket(
        instrument="EURUSD=X",
        display_name="EUR/USD",
        asset_class="forex",
        generated_at=NOW,
        data_as_of=NOW,
        stale=stale,
        freshness_note="datos de hace 0 min",
        items=(
            item(1, "precio de cierre", "1.11957", places=5),
            item(2, "SMA20", "1.1100", places=5),
            item(3, "SMA50", "1.1050", places=5),
            item(4, "RSI14", "55.62", places=2),
            item(5, "ATR14", "9.5", "pips", places=1),
        ),
        missing=missing,
        critical_missing=critical_missing,
        trading_signal=None if direction is None else TradingSignalSummary(direction, ("Tendencia alcista.",), "tendencia-momentum-volatilidad v1.0.0"),
        macro_source="no integrado",
        documents=documents,
    )


def failing(error: LLMError) -> Callable[[list[LLMMessage]], Any]:
    def raise_it(messages: list[LLMMessage]) -> Any:
        raise error

    return raise_it
