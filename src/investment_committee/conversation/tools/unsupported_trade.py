"""UnsupportedTradeRequestTool. See spec-006 S15.8."""

from __future__ import annotations

from ..models import ConversationSession, Intent
from .base import PolicyDeclined, Tool


class UnsupportedTradeRequestTool(Tool):
    """Refuses outright - no broker integration, no real-money execution
    (CLAUDE.md). Never a confirmation step (S19).
    """

    def execute(self, intent: Intent, session: ConversationSession) -> PolicyDeclined:
        symbol = intent.parameters.get("symbol")
        if symbol:
            offer = f"En cambio, puedo analizar {symbol}: prueba con /analyze {symbol}."
        else:
            offer = "En cambio, puedo analizar un activo: prueba con /analyze AAPL."
        return PolicyDeclined(
            reason="no_broker_integration",
            message="No ejecuto operaciones: este sistema solo analiza y registra recomendaciones, "
            f"nunca opera con dinero real ni se conecta a un bróker. {offer}",
        )
