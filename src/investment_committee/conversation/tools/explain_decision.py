"""ExplainDecisionTool. See spec-006 S15.2."""

from __future__ import annotations

from typing import TYPE_CHECKING

from investment_committee.memory import DecisionRecord

from ..models import ConversationSession, Intent
from ..symbol_resolver import canonical_symbol
from .base import HelpContent, NotYetAvailable, Tool

if TYPE_CHECKING:
    from investment_committee.application import DecisionQueryService


class ExplainDecisionTool(Tool):
    """Resolves which decision to explain: an explicit record_id, else the
    most recent record for `symbol`, else the session's last record, else
    asks for clarification rather than guessing (S15.2).
    """

    def __init__(self, decision_query_service: DecisionQueryService) -> None:
        self._decision_query_service = decision_query_service

    def execute(self, intent: Intent, session: ConversationSession) -> DecisionRecord | NotYetAvailable | HelpContent:
        record_id = intent.parameters.get("record_id", "").strip()
        if record_id:
            return self._decision_query_service.get(record_id)  # type: ignore[arg-type]

        symbol = canonical_symbol(intent.parameters.get("symbol", ""))
        if symbol:
            records = self._decision_query_service.list_by_asset(symbol)
            if not records:
                return NotYetAvailable(
                    feature_name=f"Una explicación para {symbol}",
                    message=f"Todavía no hay decisiones registradas para {symbol}. Prueba primero con /analyze {symbol}.",
                )
            return max(records, key=lambda r: r.recorded_at)

        if session.last_record_id is not None:
            return self._decision_query_service.get(session.last_record_id)

        return HelpContent(
            available_intents=(),
            description="¿Qué decisión quieres que te explique? Prueba con /explain AAPL o analiza primero un activo.",
        )
