"""AnalyzeAssetTool. See spec-006 S15.1."""

from __future__ import annotations

from typing import TYPE_CHECKING

from investment_committee.memory import DecisionRecord

from ..asset_policy import AssetClassDecision, AssetClassPolicy
from ..models import ConversationSession, Intent
from ..symbol_resolver import canonical_symbol
from ._messages import unsupported_asset
from .base import HelpContent, PolicyDeclined, Tool

if TYPE_CHECKING:
    from investment_committee.application import RecordDecisionUseCase


class AnalyzeAssetTool(Tool):
    """Gates on AssetClassPolicy, then analyzes and records. An unsupported
    symbol never reaches RecordDecisionUseCase, application/, or the
    Decision Ledger (S13). Conversational analysis always records (S15.1).
    """

    def __init__(
        self,
        record_decision_use_case: RecordDecisionUseCase,
        asset_class_policy: AssetClassPolicy,
    ) -> None:
        self._record_decision_use_case = record_decision_use_case
        self._asset_class_policy = asset_class_policy

    def execute(self, intent: Intent, session: ConversationSession) -> DecisionRecord | PolicyDeclined | HelpContent:
        symbol = canonical_symbol(intent.parameters.get("symbol", ""))
        if not symbol:
            return HelpContent(
                available_intents=(),
                description="¿Qué activo quieres analizar? Envía un símbolo, por ejemplo /analyze AAPL o /analyze EUR/USD.",
            )
        decision = self._asset_class_policy.classify(symbol)
        if decision is not AssetClassDecision.SUPPORTED:
            return unsupported_asset(symbol, decision)
        result = self._record_decision_use_case.analyze_and_record(symbol)
        return result.record
