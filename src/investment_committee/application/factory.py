"""Composition root: assembles the production ApplicationService.

Object construction is kept separate from orchestration logic (service.py).
See docs/specs/spec-004-application-service.md, "Composition root".
"""

from __future__ import annotations

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

from .record_decision import RecordDecisionUseCase
from .service import ApplicationService


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
