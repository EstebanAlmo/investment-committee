"""Tests for investment_committee.indicators.models."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_committee.domain import Asset, AssetType, MarketSnapshot, PriceBar
from investment_committee.indicators.models import IndicatorSnapshot

UTC_NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


def make_snapshot() -> MarketSnapshot:
    asset = Asset(
        ticker="AAPL",
        name="Apple Inc.",
        asset_type=AssetType.STOCK,
        exchange="NASDAQ",
        currency="USD",
    )
    bar = PriceBar(
        timestamp=UTC_NOW,
        open=Decimal("100"),
        high=Decimal("100"),
        low=Decimal("100"),
        close=Decimal("100"),
        volume=1_000,
    )
    return MarketSnapshot(asset=asset, generated_at=UTC_NOW, price_bars=[bar])


def make_indicator_snapshot(generated_at: datetime = UTC_NOW) -> IndicatorSnapshot:
    return IndicatorSnapshot(
        market_snapshot=make_snapshot(),
        generated_at=generated_at,
        latest_close=Decimal("100"),
        sma_20=None,
        sma_50=None,
        sma_200=None,
        return_1d=None,
        return_30d=None,
        return_90d=None,
        return_ytd=None,
        annualized_volatility=None,
        maximum_drawdown=None,
        rsi_14=None,
    )


def test_indicator_snapshot_exposes_asset_from_market_snapshot() -> None:
    snapshot = make_indicator_snapshot()
    assert snapshot.asset is snapshot.market_snapshot.asset
    assert snapshot.asset.ticker == "AAPL"


def test_indicator_snapshot_rejects_timezone_naive_generated_at() -> None:
    with pytest.raises(ValueError):
        make_indicator_snapshot(generated_at=datetime(2026, 7, 21, 12, 0))
