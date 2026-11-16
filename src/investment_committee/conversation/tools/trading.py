"""Simulated-trading and learning tools: /trade, /explicar_trade, /backtest,
/operaciones, /aprender. Each delegates to application/ (S8) - a Tool never
computes a signal, a level, or a metric itself, and none can place an order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import ConversationSession, Intent
from ..symbol_resolver import canonical_symbol
from .base import Tool, ToolOutcome

if TYPE_CHECKING:
    from investment_committee.application import LearningService, TradingService

DEFAULT_TRADING_SYMBOL = "EURUSD=X"
DEFAULT_TRADE_LIST_LIMIT = 10


def _trading_symbol(intent: Intent) -> str:
    return canonical_symbol(intent.parameters.get("symbol", "")) or DEFAULT_TRADING_SYMBOL


class TradeSignalTool(Tool):
    def __init__(self, trading_service: TradingService) -> None:
        self._trading_service = trading_service

    def execute(self, intent: Intent, session: ConversationSession) -> ToolOutcome:
        return self._trading_service.propose_trade(_trading_symbol(intent))


class ExplainTradeTool(Tool):
    """Explains the session's last /trade signal, else the latest recorded one."""

    def __init__(self, learning_service: LearningService) -> None:
        self._learning_service = learning_service

    def execute(self, intent: Intent, session: ConversationSession) -> ToolOutcome:
        return self._learning_service.explain_trade(session.last_trade_signal_id)


class BacktestTool(Tool):
    def __init__(self, trading_service: TradingService) -> None:
        self._trading_service = trading_service

    def execute(self, intent: Intent, session: ConversationSession) -> ToolOutcome:
        return self._trading_service.backtest(_trading_symbol(intent))


class SimulatedTradesTool(Tool):
    def __init__(self, trading_service: TradingService) -> None:
        self._trading_service = trading_service

    def execute(self, intent: Intent, session: ConversationSession) -> ToolOutcome:
        return self._trading_service.list_trades(DEFAULT_TRADE_LIST_LIMIT)


class LearnConceptTool(Tool):
    """A lesson grounded in the session's latest trade signal or committee
    decision, when those computed the concept's values."""

    def __init__(self, learning_service: LearningService) -> None:
        self._learning_service = learning_service

    def execute(self, intent: Intent, session: ConversationSession) -> ToolOutcome:
        return self._learning_service.lesson(
            intent.parameters.get("concept", ""),
            trade_signal_id=session.last_trade_signal_id,
            record_id=session.last_record_id,
        )
