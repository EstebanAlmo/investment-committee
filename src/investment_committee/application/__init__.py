"""Application Service: the single orchestration entry point for one analysis pass.

ApplicationService composes market_intelligence -> indicators -> committee
into one AnalysisResult for a single ticker. Every interface (the dev CLI,
Telegram, a future web interface) calls this layer and never orchestrates
market_intelligence, indicators, or committee directly - see
docs/architecture.md, product principle 4, and
docs/specs/spec-004-application-service.md.
"""

from __future__ import annotations

from .errors import (
    ApplicationServiceError,
    CommitteeDecisionFailedError,
    IndicatorCalculationFailedError,
    InvalidTickerError,
    MarketDataUnavailableError,
)
from .ai_committee import AICommitteeRecordingError, AICommitteeService, AICommitteeUnavailableError
from .decision_query import DecisionNotFoundInApplicationError, DecisionQueryFailedError, DecisionQueryService
from .factory import (
    build_default_ai_committee_service,
    build_default_application_service,
    build_default_decision_query_service,
    build_default_learning_service,
    build_default_ledger,
    build_default_opportunity_scanner,
    build_default_record_decision_use_case,
    build_default_replay_service,
    build_default_tracker_service,
    build_default_portfolio_guardian,
    build_default_trading_service,
)
from .learning import LearningService, LessonCatalog, TradeExplanation, TradeSignalNotFoundError
from investment_committee.committee.ai import AICommitteeReport
from investment_committee.education import Lesson
from investment_committee.simulation import BacktestResult, SegmentedBacktest, SimulatedTrade, TradeSignal

from .trading import (
    BacktestUnavailableError,
    TradeJournalFailedError,
    TradeJournalView,
    TradingConfig,
    TradingNotAvailableError,
    TradingService,
)
from .models import AnalysisResult
from .opportunities import OpportunityScannerService, OpportunityScanRecordingError, ScannerConfig, account_state_from_journal
from investment_committee.simulation import InstrumentScan, OpportunityScan
from investment_committee.simulation.replay import PolicyResult, ReplayReport
from .replay import HistoricalReplayService, ReplayUnavailableError, previous_month
from .tracker import OpportunityTrackerService, TrackerUnavailableError, TrackerView
from .portfolio import AlertCheck, GuardianSettings, PortfolioGuardianService, PortfolioUnavailableError, PortfolioView
from .record_decision import DecisionRecordingError, RecordDecisionResult, RecordDecisionUseCase
from .service import ApplicationService

__all__ = [
    "ApplicationService",
    "AnalysisResult",
    "ApplicationServiceError",
    "InvalidTickerError",
    "MarketDataUnavailableError",
    "IndicatorCalculationFailedError",
    "CommitteeDecisionFailedError",
    "build_default_application_service",
    "analyze_asset",
    "RecordDecisionUseCase",
    "RecordDecisionResult",
    "DecisionRecordingError",
    "build_default_record_decision_use_case",
    "DecisionQueryService",
    "DecisionNotFoundInApplicationError",
    "DecisionQueryFailedError",
    "build_default_decision_query_service",
    "build_default_ledger",
    # simulated trading and lessons
    "TradingService",
    "TradingConfig",
    "TradeJournalView",
    "TradingNotAvailableError",
    "TradeJournalFailedError",
    "BacktestUnavailableError",
    "build_default_trading_service",
    "LearningService",
    "LessonCatalog",
    "TradeExplanation",
    "TradeSignalNotFoundError",
    "build_default_learning_service",
    # result types callers (conversation/) render, re-exported so they never
    # import simulation/ or education/ directly
    "TradeSignal",
    "SimulatedTrade",
    "BacktestResult",
    "SegmentedBacktest",
    "Lesson",
    # AI committee
    "AICommitteeService",
    "AICommitteeUnavailableError",
    "AICommitteeRecordingError",
    "AICommitteeReport",
    "build_default_ai_committee_service",
    # opportunity scanner (/oportunidades)
    "OpportunityScannerService",
    "OpportunityScanRecordingError",
    "ScannerConfig",
    "OpportunityScan",
    "InstrumentScan",
    "account_state_from_journal",
    "build_default_opportunity_scanner",
    # historical replay (/simular_mes)
    "HistoricalReplayService",
    "ReplayUnavailableError",
    "ReplayReport",
    "PolicyResult",
    "previous_month",
    "build_default_replay_service",
    # opportunity tracker (/seguimiento)
    "OpportunityTrackerService",
    "TrackerUnavailableError",
    "TrackerView",
    "build_default_tracker_service",
    # portfolio guardian (/portafolio, /alertas)
    "PortfolioGuardianService",
    "PortfolioUnavailableError",
    "PortfolioView",
    "AlertCheck",
    "GuardianSettings",
    "build_default_portfolio_guardian",
]


def analyze_asset(symbol: str) -> AnalysisResult:
    """Convenience wrapper: analyze_asset via a freshly built default ApplicationService.

    Builds a new ApplicationService (and therefore a new default
    YFinanceProvider) on every call. Fine for a single ad hoc analysis or
    the dev CLI; callers making repeated calls should build one
    ApplicationService via build_default_application_service() and reuse it.
    """
    return build_default_application_service().analyze_asset(symbol)
