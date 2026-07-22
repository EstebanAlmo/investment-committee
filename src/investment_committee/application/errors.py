"""ApplicationServiceError hierarchy: translates lower-layer failures.

See docs/specs/spec-004-application-service.md, "Error model". Only the
three named typed exceptions from market_intelligence, indicators, and
committee are translated here; anything else propagates unmodified.
"""

from __future__ import annotations


class ApplicationServiceError(Exception):
    """Base class for every error ApplicationService.analyze_asset can raise."""


class InvalidTickerError(ApplicationServiceError):
    """Raised when the input symbol fails Application Service input validation.

    Raised before any collaborator is called - no network access has
    happened yet when this is raised.
    """


class MarketDataUnavailableError(ApplicationServiceError):
    """Wraps a market_intelligence.MarketDataError from get_daily_history."""


class IndicatorCalculationFailedError(ApplicationServiceError):
    """Wraps an indicators.IndicatorCalculationError from IndicatorEngine.calculate."""


class CommitteeDecisionFailedError(ApplicationServiceError):
    """Wraps a committee.CommitteeEngineError from DeterministicCommitteeEngine.run."""
