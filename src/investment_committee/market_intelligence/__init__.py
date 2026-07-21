"""Market Intelligence: fetches and validates market data for downstream layers.

Callers should depend on MarketDataProvider and MarketIntelligenceService, not
on any concrete provider implementation. See docs/architecture.md.
"""

from .providers.base import MarketDataError, MarketDataProvider
from .service import MarketIntelligenceService

__all__ = [
    "MarketDataError",
    "MarketDataProvider",
    "MarketIntelligenceService",
]
