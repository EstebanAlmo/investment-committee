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
from .factory import build_default_application_service
from .models import AnalysisResult
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
]


def analyze_asset(symbol: str) -> AnalysisResult:
    """Convenience wrapper: analyze_asset via a freshly built default ApplicationService.

    Builds a new ApplicationService (and therefore a new default
    YFinanceProvider) on every call. Fine for a single ad hoc analysis or
    the dev CLI; callers making repeated calls should build one
    ApplicationService via build_default_application_service() and reuse it.
    """
    return build_default_application_service().analyze_asset(symbol)
