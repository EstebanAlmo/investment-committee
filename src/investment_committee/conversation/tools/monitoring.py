"""Replay, tracking and portfolio tools: /simular_mes, /seguimiento,
/portafolio, /alertas. Each delegates to application/ (S8); none computes
a figure, opens a trade, or sends an order."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import ConversationSession, Intent
from .base import Tool, ToolOutcome

if TYPE_CHECKING:
    from investment_committee.application import HistoricalReplayService, OpportunityTrackerService, PortfolioGuardianService


class ReplayMonthTool(Tool):
    """The last complete calendar month, policies A, B and C."""

    def __init__(self, replay_service: HistoricalReplayService) -> None:
        self._replay = replay_service

    def execute(self, intent: Intent, session: ConversationSession) -> ToolOutcome:
        return self._replay.replay()


class TrackerTool(Tool):
    def __init__(self, tracker_service: OpportunityTrackerService) -> None:
        self._tracker = tracker_service

    def execute(self, intent: Intent, session: ConversationSession) -> ToolOutcome:
        return self._tracker.update()


class PortfolioGuardianTool(Tool):
    def __init__(self, guardian: PortfolioGuardianService) -> None:
        self._guardian = guardian

    def execute(self, intent: Intent, session: ConversationSession) -> ToolOutcome:
        return self._guardian.view()


class AlertsTool(Tool):
    """A manual alert check, run because the user asked."""

    def __init__(self, guardian: PortfolioGuardianService) -> None:
        self._guardian = guardian

    def execute(self, intent: Intent, session: ConversationSession) -> ToolOutcome:
        return self._guardian.check()
