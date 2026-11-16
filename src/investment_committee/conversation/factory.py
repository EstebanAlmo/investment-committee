"""Composition root: assembles the production, deterministic ConversationService.

See docs/specs/spec-006-conversational-interface.md S25. The deterministic
Telegram MVP wires RuleBasedIntentRouter and TemplateResponseSummarizer
only - no LLM client, no Speech-to-Text adapter.

One DecisionLedger instance is shared by the recording use case and the
query service: JsonLinesDecisionLedger indexes its file once at
construction, so separate instances would not see each other's appends
(a decision recorded by /analyze would be missing from /history until a
restart).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from investment_committee.application import (
    build_default_ai_committee_service,
    build_default_decision_query_service,
    build_default_learning_service,
    build_default_ledger,
    build_default_opportunity_scanner,
    build_default_portfolio_guardian,
    build_default_replay_service,
    build_default_tracker_service,
    build_default_record_decision_use_case,
    build_default_trading_service,
)

from .access import AllowlistAccessPolicy
from .asset_policy import TickerPatternAssetClassPolicy
from .command_parser import CommandParser
from .intent_router import RuleBasedIntentRouter
from .models import IntentType
from .service import ConversationService
from .session_store import InMemorySessionStore
from .speech import SpeechToTranscriber
from .summarizer import TemplateResponseSummarizer
from .symbol_resolver import StaticSymbolResolver
from .tools.ai_committee import AICommitteeTool
from .tools.analyze_asset import AnalyzeAssetTool
from .tools.compare_assets import CompareAssetsTool
from .tools.decision_history import DecisionHistoryTool
from .tools.explain_decision import ExplainDecisionTool
from .tools.help import HelpTool
from .tools.monitoring import AlertsTool, PortfolioGuardianTool, ReplayMonthTool, TrackerTool
from .tools.opportunities import OpportunitiesTool
from .tools.performance_summary import PerformanceSummaryTool
from .tools.portfolio_summary import PortfolioSummaryTool
from .tools.router import ToolRouter
from .tools.trading import BacktestTool, ExplainTradeTool, LearnConceptTool, SimulatedTradesTool, TradeSignalTool
from .tools.unsupported_trade import UnsupportedTradeRequestTool

if TYPE_CHECKING:
    from investment_committee.application import (
        AICommitteeService,
        DecisionQueryService,
        HistoricalReplayService,
        OpportunityScannerService,
        OpportunityTrackerService,
        PortfolioGuardianService,
        RecordDecisionUseCase,
        TradingService,
    )

DEFAULT_NAME_TO_TICKER: dict[str, str] = {
    # Hand-maintained convenience table (S13) - supported asset classes only;
    # deliberately no crypto or commodity names.
    "apple": "AAPL",
    "microsoft": "MSFT",
    "nvidia": "NVDA",
    "tesla": "TSLA",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "amazon": "AMZN",
    "meta": "META",
    "facebook": "META",
    "netflix": "NFLX",
    "sp500": "SPY",
    "nasdaq": "QQQ",
    "euro dollar": "EURUSD=X",
    "eur usd": "EURUSD=X",
    "eurusd": "EURUSD=X",
    "pound dollar": "GBPUSD=X",
    "gbp usd": "GBPUSD=X",
    "dollar yen": "USDJPY=X",
    "usd jpy": "USDJPY=X",
}


def build_default_conversation_service(
    allowed_user_ids: frozenset[str],
    record_decision_use_case: RecordDecisionUseCase | None = None,
    decision_query_service: DecisionQueryService | None = None,
    transcriber: SpeechToTranscriber | None = None,
    trading_service: TradingService | None = None,
    ai_committee_service: AICommitteeService | None = None,
    opportunity_scanner: OpportunityScannerService | None = None,
    replay_service: HistoricalReplayService | None = None,
    tracker_service: OpportunityTrackerService | None = None,
    portfolio_guardian: PortfolioGuardianService | None = None,
) -> ConversationService:
    """Assemble the production ConversationService.

    `allowed_user_ids` is required - there is no safe default allowlist
    (S25). The two application collaborators default to production
    instances sharing one ledger; pass both (never just one) to override
    them, e.g. in tests. `transcriber` is optional in this MVP (see
    conversation/service.py). `trading_service` defaults to the production
    simulated-trading service (Yahoo 1-hour bars, JSONL trade journal);
    lessons read the same trading service and decision ledger.
    `ai_committee_service` defaults to the DeepSeek-backed /committee,
    configured from the environment (unavailable without DEEPSEEK_API_KEY).
    `opportunity_scanner` defaults to /oportunidades over EUR/USD, GBP/USD
    and USD/JPY, reading the trading service's journal for open risk; it
    never calls the AI committee from Telegram.
    """
    if (record_decision_use_case is None) != (decision_query_service is None):
        raise ValueError(
            "pass both record_decision_use_case and decision_query_service, or neither - "
            "they must share one ledger"
        )
    if record_decision_use_case is None or decision_query_service is None:
        ledger = build_default_ledger()
        record_decision_use_case = build_default_record_decision_use_case(ledger=ledger)
        decision_query_service = build_default_decision_query_service(ledger=ledger)

    if trading_service is None:
        trading_service = build_default_trading_service()
    learning_service = build_default_learning_service(trading_service, decision_query_service)
    if ai_committee_service is None:
        ai_committee_service = build_default_ai_committee_service(trading_service=trading_service)
    if opportunity_scanner is None:
        opportunity_scanner = build_default_opportunity_scanner(trading_journal=trading_service.journal)
    replay_service = replay_service or build_default_replay_service()
    tracker_service = tracker_service or build_default_tracker_service()
    portfolio_guardian = portfolio_guardian or build_default_portfolio_guardian(trading_journal=trading_service.journal)

    asset_class_policy = TickerPatternAssetClassPolicy()
    help_tool = HelpTool()
    tools_by_intent = {
        IntentType.ANALYZE_ASSET: AnalyzeAssetTool(record_decision_use_case, asset_class_policy),
        IntentType.EXPLAIN_DECISION: ExplainDecisionTool(decision_query_service),
        IntentType.DECISION_HISTORY: DecisionHistoryTool(decision_query_service),
        IntentType.COMPARE_ASSETS: CompareAssetsTool(record_decision_use_case, asset_class_policy),
        IntentType.PORTFOLIO_SUMMARY: PortfolioSummaryTool(),
        IntentType.PERFORMANCE_SUMMARY: PerformanceSummaryTool(),
        IntentType.HELP: help_tool,
        IntentType.UNKNOWN: help_tool,
        IntentType.UNSUPPORTED_TRADE_REQUEST: UnsupportedTradeRequestTool(),
        IntentType.TRADE_SIGNAL: TradeSignalTool(trading_service),
        IntentType.EXPLAIN_TRADE: ExplainTradeTool(learning_service),
        IntentType.BACKTEST: BacktestTool(trading_service),
        IntentType.SIMULATED_TRADES: SimulatedTradesTool(trading_service),
        IntentType.LEARN_CONCEPT: LearnConceptTool(learning_service),
        IntentType.AI_COMMITTEE: AICommitteeTool(ai_committee_service, asset_class_policy),
        IntentType.OPPORTUNITIES: OpportunitiesTool(opportunity_scanner),
        IntentType.REPLAY_MONTH: ReplayMonthTool(replay_service),
        IntentType.OPPORTUNITY_TRACKER: TrackerTool(tracker_service),
        IntentType.PORTFOLIO_GUARDIAN: PortfolioGuardianTool(portfolio_guardian),
        IntentType.PORTFOLIO_ALERTS: AlertsTool(portfolio_guardian),
    }

    return ConversationService(
        access_policy=AllowlistAccessPolicy(allowed_user_ids),
        command_parser=CommandParser(),
        intent_router=RuleBasedIntentRouter(symbol_resolver=StaticSymbolResolver(DEFAULT_NAME_TO_TICKER)),
        tool_router=ToolRouter(tools_by_intent),
        response_summarizer=TemplateResponseSummarizer(),
        session_store=InMemorySessionStore(),
        transcriber=transcriber,
    )
