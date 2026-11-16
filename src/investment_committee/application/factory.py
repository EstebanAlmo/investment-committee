"""Composition root: assembles the production ApplicationService.

Object construction is kept separate from orchestration logic (service.py).
See docs/specs/spec-004-application-service.md, "Composition root".
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal, InvalidOperation
from typing import Mapping

from investment_committee.committee import (
    DeterministicCommitteeChair,
    DeterministicCommitteeEngine,
    DeterministicDevilsAdvocate,
    DeterministicRiskManager,
    DeterministicTechnicalAnalyst,
)
from investment_committee.indicators import IndicatorEngine
from investment_committee.market_intelligence import MarketIntelligenceService
from investment_committee.memory import DETERMINISTIC_STRATEGY_METADATA, DecisionLedger, JsonLinesDecisionLedger
from investment_committee.simulation import (
    JsonLinesTradeJournal,
    OpportunityScanLedger,
    TradeJournal,
)
from investment_committee.committee.ai import AICommitteeCoordinator, AnalysisBudget
from investment_committee.integrations.llm import DeepSeekClient, DeepSeekConfig, LLMNotConfiguredError
from investment_committee.memory.ai_ledger import AICommitteeLedger, JsonLinesAICommitteeLedger

from .ai_committee import AICommitteeService
from .decision_query import DecisionQueryService
from .learning import LearningService
from .opportunities import OpportunityScannerService, ScannerConfig, account_state_from_journal
from .replay import HistoricalReplayService
from .tracker import OpportunityTrackerService
from .portfolio import GuardianSettings, PortfolioGuardianService
from investment_committee.portfolio.sqlite_store import SqliteAlertStore
from investment_committee.portfolio.store import JsonLinesPositionStore
from investment_committee.simulation import ScanStrategy
from investment_committee.simulation.sqlite_store import SqliteOpportunityScanLedger, SqliteTrackerLedger
from investment_committee.simulation.tracker import TrackerLedger
from .record_decision import RecordDecisionUseCase
from .service import ApplicationService
from .trading import TradingConfig, TradingService


def build_default_application_service() -> ApplicationService:
    """Assemble the production ApplicationService with its default collaborators.

    Constructs MarketIntelligenceService with its default provider
    (YFinanceProvider), a plain IndicatorEngine, and a
    DeterministicCommitteeEngine wired with the three deterministic
    analysts and chair. No network access or other I/O happens during
    construction - only during a subsequent analyze_asset() call.
    """
    return ApplicationService(
        market_intelligence_service=MarketIntelligenceService(),
        indicator_engine=IndicatorEngine(),
        committee_engine=DeterministicCommitteeEngine(
            technical_analyst=DeterministicTechnicalAnalyst(),
            risk_manager=DeterministicRiskManager(),
            devils_advocate=DeterministicDevilsAdvocate(),
            chair=DeterministicCommitteeChair(),
        ),
    )


def build_default_record_decision_use_case(
    ledger: DecisionLedger | None = None,
) -> RecordDecisionUseCase:
    """Assemble the production RecordDecisionUseCase for the deterministic strategy.

    Binds together the deterministic ApplicationService (via
    build_default_application_service(), unchanged), the matching
    DETERMINISTIC_STRATEGY_METADATA, and a durable ledger (a
    JsonLinesDecisionLedger at the default path unless `ledger` overrides
    it) - see spec-005 S13, "Production binding guarantee." No network
    access happens during construction - only during a subsequent record()
    or analyze_and_record() call.
    """
    return RecordDecisionUseCase(
        application_service=build_default_application_service(),
        ledger=ledger if ledger is not None else JsonLinesDecisionLedger(),
        strategy_metadata=DETERMINISTIC_STRATEGY_METADATA,
    )


def build_default_ledger() -> DecisionLedger:
    """Build the production DecisionLedger (a JsonLinesDecisionLedger at the
    default path). No I/O beyond reading an existing ledger file.

    A long-running process that both records and queries (the Telegram bot)
    must build this once and pass the same instance to both
    build_default_record_decision_use_case() and
    build_default_decision_query_service(): JsonLinesDecisionLedger indexes
    its file once at construction, so two separate instances would not see
    each other's appends.
    """
    return JsonLinesDecisionLedger()


def build_default_decision_query_service(
    ledger: DecisionLedger | None = None,
) -> DecisionQueryService:
    """Assemble the production DecisionQueryService (spec-006 S16)."""
    return DecisionQueryService(ledger=ledger if ledger is not None else JsonLinesDecisionLedger())


def build_default_trading_service(
    journal: TradeJournal | None = None,
    market_intelligence_service: MarketIntelligenceService | None = None,
    config: TradingConfig | None = None,
) -> TradingService:
    """Assemble the production TradingService: Yahoo intraday bars (via the
    default MarketIntelligenceService) and a JsonLinesTradeJournal at
    data/ledger/simulated_trades.jsonl. No network access during
    construction. Simulation only - there is no broker adapter to wire."""
    return TradingService(
        market_intelligence_service=market_intelligence_service or MarketIntelligenceService(),
        journal=journal if journal is not None else JsonLinesTradeJournal(),
        config=config,
    )


def build_default_learning_service(
    trading_service: TradingService, decision_query_service: DecisionQueryService
) -> LearningService:
    """Lessons grounded in the same trading service and ledger the bot uses."""
    return LearningService(trading_service=trading_service, decision_query_service=decision_query_service)


AI_COMMITTEE_MAX_TOKENS = "AI_COMMITTEE_MAX_TOKENS"
AI_COMMITTEE_MAX_COST_USD = "AI_COMMITTEE_MAX_COST_USD"


def build_default_ai_committee_service(
    env: Mapping[str, str] | None = None,
    trading_service: TradingService | None = None,
    ledger: AICommitteeLedger | None = None,
) -> AICommitteeService:
    """Assemble /committee. Reads DEEPSEEK_* and AI_COMMITTEE_* from `env`
    (default: the process environment, which the Telegram entry point loads
    from .env). Without DEEPSEEK_API_KEY, or with an invalid setting, the
    service is built unavailable and explains why - nothing else is
    affected. No network access during construction."""
    env = os.environ if env is None else env
    coordinator = None
    reason = None
    try:
        config = DeepSeekConfig.from_env(env)
        budget = AnalysisBudget(
            max_total_tokens=int(env.get(AI_COMMITTEE_MAX_TOKENS, "") or 40000),
            max_cost_usd=Decimal(env.get(AI_COMMITTEE_MAX_COST_USD, "") or "0.05"),
        )
        coordinator = AICommitteeCoordinator(DeepSeekClient(config), budget=budget)
    except LLMNotConfiguredError as exc:
        reason = "falta configurar DEEPSEEK_API_KEY" if "DEEPSEEK_API_KEY" in str(exc) else "la configuración de DeepSeek no es válida"
    except (ValueError, InvalidOperation):
        reason = "los límites AI_COMMITTEE_MAX_TOKENS o AI_COMMITTEE_MAX_COST_USD no son válidos"
    if reason is not None:
        logging.getLogger(__name__).info("AI committee disabled: %s", reason)
    return AICommitteeService(
        application_service=build_default_application_service(),
        ledger=ledger if ledger is not None else JsonLinesAICommitteeLedger(),
        coordinator=coordinator,
        trading_service=trading_service,
        unavailable_reason=reason,
    )


def build_default_opportunity_scanner(
    trading_journal: TradeJournal | None = None,
    ai_committee_service: AICommitteeService | None = None,
    ledger: OpportunityScanLedger | None = None,
    market_intelligence_service: MarketIntelligenceService | None = None,
    config: ScannerConfig | None = None,
    tracker: TrackerLedger | None = None,
    strategy: ScanStrategy | None = None,
) -> OpportunityScannerService:
    """Assemble /oportunidades: Yahoo 1-hour bars for EUR/USD, GBP/USD and
    USD/JPY, the account read from the simulated-trade journal (open risk,
    today's realized P&L), and the prospective registry in SQLite
    (data/investment_committee.db: scans, observations, ranked candidates).
    `strategy` defaults to v1.0.0. AI review stays OFF unless `config`
    enables it. No network access during construction."""
    journal = trading_journal if trading_journal is not None else JsonLinesTradeJournal()
    config = config or ScannerConfig()
    return OpportunityScannerService(
        market_intelligence_service=market_intelligence_service or MarketIntelligenceService(),
        ledger=ledger if ledger is not None else SqliteOpportunityScanLedger(),
        strategy=strategy,
        config=config,
        account_provider=lambda now: account_state_from_journal(journal, config.limits.capital, now),
        ai_committee=ai_committee_service,
        tracker=tracker if tracker is not None else SqliteTrackerLedger(),
    )


def build_default_replay_service(
    market_intelligence_service: MarketIntelligenceService | None = None,
    ai_ledger: AICommitteeLedger | None = None,
) -> HistoricalReplayService:
    """Assemble /simular_mes: Yahoo 1-hour bars and, for contemporaneous
    objections only, the AI committee audit ledger (read, never written).
    No validated evidence is registered, so policy C does not trade. No
    network access during construction."""
    ledger = ai_ledger if ai_ledger is not None else JsonLinesAICommitteeLedger()
    return HistoricalReplayService(market_intelligence_service or MarketIntelligenceService(), ai_records=ledger.records)


def build_default_tracker_service(
    market_intelligence_service: MarketIntelligenceService | None = None, ledger: TrackerLedger | None = None
) -> OpportunityTrackerService:
    """Assemble /seguimiento over the SQLite prospective registry (the same
    database the scanner adds its ranked candidates to)."""
    return OpportunityTrackerService(market_intelligence_service or MarketIntelligenceService(), ledger if ledger is not None else SqliteTrackerLedger())


def build_default_portfolio_guardian(
    trading_journal: TradeJournal | None = None,
    market_intelligence_service: MarketIntelligenceService | None = None,
    settings: GuardianSettings | None = None,
) -> PortfolioGuardianService:
    """Assemble /portafolio and /alertas: manual positions in
    data/ledger/portfolio_positions.jsonl, the simulated journal's open
    trades, and alert states and events in SQLite. Monitoring only."""
    return PortfolioGuardianService(
        market_intelligence_service or MarketIntelligenceService(),
        JsonLinesPositionStore(),
        SqliteAlertStore(),
        journal=trading_journal if trading_journal is not None else JsonLinesTradeJournal(),
        settings=settings,
    )


def build_default_monitor(db_path=None, settings=None, guardian=None, market_intelligence_service=None):  # type: ignore[no-untyped-def]
    """Assemble the prospective monitor over the SQLite registry. Builds
    nothing that sends, trades or schedules anything."""
    from investment_committee.memory.database import DEFAULT_DB_PATH
    from investment_committee.simulation.sqlite_store import MonitorScanLedger

    from .monitor import ProspectiveMonitor

    db = db_path or DEFAULT_DB_PATH
    return ProspectiveMonitor(
        market_intelligence_service or MarketIntelligenceService(), db_path=db, settings=settings, guardian=guardian,
        scan_ledger_factory=MonitorScanLedger, tracker_ledger=SqliteTrackerLedger(db),
    )
