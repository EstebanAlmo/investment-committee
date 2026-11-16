"""OpportunitiesTool: /oportunidades - scan the configured universe for
day-trading candidates. Delegates to application/ (S8); the Tool computes
nothing and never opens a trade. AI review stays off here: a Telegram scan
never triggers a paid call."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import ConversationSession, Intent
from .base import Tool, ToolOutcome

if TYPE_CHECKING:
    from investment_committee.application import OpportunityScannerService


class OpportunitiesTool(Tool):
    def __init__(self, scanner: OpportunityScannerService) -> None:
        self._scanner = scanner

    def execute(self, intent: Intent, session: ConversationSession) -> ToolOutcome:
        return self._scanner.scan(ai_reviews=0)
