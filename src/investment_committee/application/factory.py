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
