"""PerformanceSummaryTool. See spec-006 S15.6."""

from __future__ import annotations

from ..models import ConversationSession, Intent
from .base import NotYetAvailable, Tool


class PerformanceSummaryTool(Tool):
    def execute(self, intent: Intent, session: ConversationSession) -> NotYetAvailable:
        return NotYetAvailable(
            feature_name="La evaluación de rendimiento",
            message="La evaluación de rendimiento todavía no está construida; "
            "está planeada para una etapa posterior del proyecto.",
        )
