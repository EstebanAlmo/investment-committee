"""AICommitteeTool (/committee). Same asset-class gate as /analyze; an
unsupported symbol never reaches the AI committee or any model call."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..asset_policy import AssetClassDecision, AssetClassPolicy
from ..models import ConversationSession, Intent
from ..symbol_resolver import canonical_symbol
from ._messages import unsupported_asset
from .base import HelpContent, Tool, ToolOutcome

if TYPE_CHECKING:
    from investment_committee.application import AICommitteeService


class AICommitteeTool(Tool):
    def __init__(self, ai_committee_service: AICommitteeService, asset_class_policy: AssetClassPolicy) -> None:
        self._service = ai_committee_service
        self._asset_class_policy = asset_class_policy

    def execute(self, intent: Intent, session: ConversationSession) -> ToolOutcome:
        symbol = canonical_symbol(intent.parameters.get("symbol", ""))
        if not symbol:
            return HelpContent(
                available_intents=(),
                description="¿Qué activo quieres que analice el comité de IA? Por ejemplo /committee EUR/USD o /committee AAPL.",
            )
        decision = self._asset_class_policy.classify(symbol)
        if decision is not AssetClassDecision.SUPPORTED:
            return unsupported_asset(symbol, decision)
        return self._service.analyze(symbol)
