"""MarketDataProvider: the interface every market data source implements.

The rest of the application depends on this interface, never on a concrete
provider, so a data source can be swapped without touching downstream code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from investment_committee.domain import MarketSnapshot


class MarketDataError(Exception):
    """Raised when a provider cannot produce a MarketSnapshot for a ticker."""


class MarketDataProvider(ABC):
    """Source of historical price data for a single asset."""

    @abstractmethod
    def get_daily_history(self, ticker: str) -> MarketSnapshot:
        """Return one year of daily OHLCV history for `ticker`.

        Raises MarketDataError if the ticker is invalid, the response is
        empty, or the underlying provider fails.
        """
        raise NotImplementedError
