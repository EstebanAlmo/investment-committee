"""ApplicationService: composes market_intelligence -> indicators -> committee.

The single orchestration entry point for one analysis pass. See
docs/architecture.md, layer 6, and
docs/specs/spec-004-application-service.md.
"""

from __future__ import annotations

from investment_committee.committee import CommitteeEngineError, DeterministicCommitteeEngine
from investment_committee.indicators import IndicatorCalculationError, IndicatorEngine
from investment_committee.market_intelligence import MarketDataError, MarketIntelligenceService

from .errors import (
    CommitteeDecisionFailedError,
    IndicatorCalculationFailedError,
    InvalidTickerError,
    MarketDataUnavailableError,
)
from .models import AnalysisResult


class ApplicationService:
    """Single entry point that drives one full analysis pass for one ticker."""

    def __init__(
        self,
        market_intelligence_service: MarketIntelligenceService,
        indicator_engine: IndicatorEngine,
        committee_engine: DeterministicCommitteeEngine,
    ) -> None:
        self._market_intelligence_service = market_intelligence_service
        self._indicator_engine = indicator_engine
        self._committee_engine = committee_engine

    def analyze_asset(self, symbol: str) -> AnalysisResult:
        """Run one full analysis pass for `symbol` and return its AnalysisResult.

        Fetches market data exactly once, computes indicators exactly once,
        and runs the committee exactly once. Raises a subclass of
        ApplicationServiceError (see errors.py) on any failure.
        """
        ticker = _normalize_ticker(symbol)

        try:
            market_snapshot = self._market_intelligence_service.get_daily_history(ticker)
        except MarketDataError as exc:
            raise MarketDataUnavailableError(str(exc)) from exc

        try:
            indicator_snapshot = self._indicator_engine.calculate(market_snapshot)
        except IndicatorCalculationError as exc:
            raise IndicatorCalculationFailedError(str(exc)) from exc

        try:
            decision = self._committee_engine.run(indicator_snapshot)
        except CommitteeEngineError as exc:
            raise CommitteeDecisionFailedError(str(exc)) from exc

        return AnalysisResult(
            market_snapshot=market_snapshot,
            indicator_snapshot=indicator_snapshot,
            decision=decision,
        )


def _normalize_ticker(symbol: str) -> str:
    """Strip, validate, and uppercase a raw ticker symbol.

    Raises InvalidTickerError before any collaborator is called if `symbol`
    is blank (after stripping) or contains internal whitespace. No other
    character restriction is applied - symbols such as BRK.B, BTC-USD, or
    ^GSPC pass through unchanged.
    """
    stripped = symbol.strip()
    if not stripped:
        raise InvalidTickerError("ticker symbol must not be blank")

    ticker = stripped.upper()
    if any(character.isspace() for character in ticker):
        raise InvalidTickerError(
            f"ticker symbol must not contain internal whitespace: {symbol!r}"
        )

    return ticker
