"""Entry point the rest of the application uses to fetch market data.

Callers depend on MarketIntelligenceService and MarketDataProvider only; the
concrete provider is an implementation detail selected here.
"""

from __future__ import annotations

from investment_committee.domain import MarketSnapshot

from .providers.base import MarketDataProvider
from .providers.yfinance_provider import YFinanceProvider


class MarketIntelligenceService:
    """Fetches market data through a MarketDataProvider."""

    def __init__(self, provider: MarketDataProvider | None = None) -> None:
        self.provider: MarketDataProvider = provider if provider is not None else YFinanceProvider()

    def get_daily_history(self, ticker: str) -> MarketSnapshot:
        """Return one year of daily price history for `ticker`."""
        return self.provider.get_daily_history(ticker)
