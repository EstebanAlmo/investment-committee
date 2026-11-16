"""Tests for investment_committee.market_intelligence.service."""

from datetime import datetime, timezone
from decimal import Decimal

from investment_committee.domain import Asset, AssetType, MarketSnapshot, PriceBar
from investment_committee.market_intelligence.providers.base import MarketDataProvider
from investment_committee.market_intelligence.providers.yfinance_provider import (
    YFinanceProvider,
)
from investment_committee.market_intelligence.service import MarketIntelligenceService


def make_snapshot() -> MarketSnapshot:
    asset = Asset(
        ticker="AAPL",
        name=None,
        asset_type=AssetType.STOCK,
        exchange=None,
        currency=None,
    )
    now = datetime(2026, 7, 21, tzinfo=timezone.utc)
    bar = PriceBar(
        timestamp=now,
        open=Decimal("1"),
        high=Decimal("1"),
        low=Decimal("1"),
        close=Decimal("1"),
        volume=1,
    )
    return MarketSnapshot(asset=asset, generated_at=now, price_bars=[bar])


class StubProvider(MarketDataProvider):
    """A MarketDataProvider test double that records the ticker it was asked for."""

    def __init__(self, snapshot: MarketSnapshot) -> None:
        self._snapshot = snapshot
        self.requested_ticker: str | None = None

    def get_daily_history(self, ticker: str) -> MarketSnapshot:
        self.requested_ticker = ticker
        return self._snapshot


class FalsyStubProvider(StubProvider):
    """A StubProvider that is falsy, to prove `is not None` checks are used."""

    def __bool__(self) -> bool:
        return False


def test_service_delegates_to_injected_provider() -> None:
    snapshot = make_snapshot()
    provider = StubProvider(snapshot)
    service = MarketIntelligenceService(provider=provider)

    result = service.get_daily_history("aapl")

    assert result is snapshot
    assert provider.requested_ticker == "aapl"


def test_service_defaults_to_yfinance_provider() -> None:
    service = MarketIntelligenceService()

    assert isinstance(service.provider, YFinanceProvider)


def test_service_uses_falsy_injected_provider_instead_of_default() -> None:
    snapshot = make_snapshot()
    provider = FalsyStubProvider(snapshot)
    service = MarketIntelligenceService(provider=provider)

    assert service.provider is provider

    result = service.get_daily_history("aapl")

    assert result is snapshot
    assert provider.requested_ticker == "aapl"
