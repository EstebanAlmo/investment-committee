"""CompareAssetsTool. See spec-006 S15.4."""

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


class CompareAssetsTool(Tool):
    """Both symbols are checked before either is analyzed - if either is
    unsupported, neither is analyzed or recorded. No comparison computation
    of its own: two independent DecisionRecords (S15.4).
    """

    def __init__(
        self,
        record_decision_use_case: RecordDecisionUseCase,
        asset_class_policy: AssetClassPolicy,
    ) -> None:
        self._record_decision_use_case = record_decision_use_case
        self._asset_class_policy = asset_class_policy

    def execute(
        self, intent: Intent, session: ConversationSession
    ) -> tuple[DecisionRecord, DecisionRecord] | PolicyDeclined | HelpContent:
        symbols = [canonical_symbol(s) for s in intent.parameters.get("symbols", "").split(",") if s.strip()]
        if len(symbols) != 2:
            return HelpContent(
                available_intents=(),
                description="Envía exactamente dos símbolos para comparar, por ejemplo /compare MSFT AAPL.",
            )
        for symbol in symbols:
            decision = self._asset_class_policy.classify(symbol)
            if decision is not AssetClassDecision.SUPPORTED:
                return unsupported_asset(symbol, decision)
        result_a = self._record_decision_use_case.analyze_and_record(symbols[0])
        result_b = self._record_decision_use_case.analyze_and_record(symbols[1])
        return (result_a.record, result_b.record)
