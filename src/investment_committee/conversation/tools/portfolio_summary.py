"""PortfolioSummaryTool. See spec-006 S15.5."""

from __future__ import annotations

from ..models import ConversationSession, Intent
from .base import NotYetAvailable, Tool


class PortfolioSummaryTool(Tool):
    def execute(self, intent: Intent, session: ConversationSession) -> NotYetAvailable:
        return NotYetAvailable(
            feature_name="El portafolio",
            message="El seguimiento de un portafolio simulado todavía no está construido; "
            "está planeado para una etapa posterior del proyecto.",
        )
