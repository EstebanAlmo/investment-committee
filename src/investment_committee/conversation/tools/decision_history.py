"""DecisionHistoryTool. See spec-006 S15.3."""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from investment_committee.memory import DecisionRecord

from ..models import ConversationSession, Intent
from ..symbol_resolver import canonical_symbol
from .base import Tool

if TYPE_CHECKING:
    from investment_committee.application import DecisionQueryService

DEFAULT_HISTORY_LIMIT = 10


class DecisionHistoryTool(Tool):
    """Fetches recorded decisions, most recent first - for one symbol if
    given, otherwise across all assets. Never computes derived statistics
    (S15.3: that is ResponseSummarizer's job).
    """

    def __init__(self, decision_query_service: DecisionQueryService) -> None:
        self._decision_query_service = decision_query_service

    def execute(self, intent: Intent, session: ConversationSession) -> Sequence[DecisionRecord]:
        limit = _parse_limit(intent.parameters.get("limit"))
        symbol = canonical_symbol(intent.parameters.get("symbol", ""))
        if symbol:
            records = self._decision_query_service.list_by_asset(symbol)
            return tuple(sorted(records, key=lambda r: r.recorded_at, reverse=True))[:limit]
        return tuple(self._decision_query_service.list_recent(limit))


def _parse_limit(raw: str | None) -> int:
    if raw is not None and raw.strip().isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_HISTORY_LIMIT
