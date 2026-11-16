"""Tests for investment_committee.domain.market."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from investment_committee.domain.assets import Asset, AssetType
from investment_committee.domain.market import MarketSnapshot, PriceBar

UTC_NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


def make_asset() -> Asset:
    return Asset(
        ticker="AAPL",
        name="Apple Inc.",
        asset_type=AssetType.STOCK,
        exchange="NASDAQ",
        currency="USD",
    )


def make_price_bar(
    timestamp: datetime = UTC_NOW,
    open_: str = "100",
    high: str = "110",
    low: str = "95",
    close: str = "105",
    volume: int = 1_000,
) -> PriceBar:
    return PriceBar(
        timestamp=timestamp,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
    )


def test_valid_price_bar_creation() -> None:
    bar = make_price_bar()
    assert bar.open == Decimal("100")
    assert bar.high == Decimal("110")
    assert bar.low == Decimal("95")
    assert bar.close == Decimal("105")
    assert bar.volume == 1_000


def test_price_bar_rejects_high_lower_than_other_values() -> None:
    with pytest.raises(ValueError):
        make_price_bar(open_="100", high="90", low="80", close="95")


def test_price_bar_rejects_low_higher_than_other_values() -> None:
    with pytest.raises(ValueError):
        make_price_bar(open_="100", high="110", low="105", close="102")


def test_price_bar_rejects_negative_volume() -> None:
    with pytest.raises(ValueError):
        make_price_bar(volume=-1)


def test_price_bar_rejects_timezone_naive_timestamp() -> None:
    with pytest.raises(ValueError):
        make_price_bar(timestamp=datetime(2026, 7, 21, 12, 0))


def test_market_snapshot_rejects_empty_price_bars() -> None:
    with pytest.raises(ValueError):
        MarketSnapshot(asset=make_asset(), generated_at=UTC_NOW, price_bars=[])


def test_market_snapshot_rejects_timezone_naive_generated_at() -> None:
    with pytest.raises(ValueError):
        MarketSnapshot(
            asset=make_asset(),
            generated_at=datetime(2026, 7, 21, 12, 0),
            price_bars=[make_price_bar()],
        )


def test_market_snapshot_latest_price_bar() -> None:
    earlier = make_price_bar(timestamp=UTC_NOW - timedelta(days=1))
    latest = make_price_bar(timestamp=UTC_NOW)
    snapshot = MarketSnapshot(
        asset=make_asset(),
        generated_at=UTC_NOW,
        price_bars=[latest, earlier],
        metrics={"sma_5": Decimal("101.5"), "rsi_14": None},
    )
    assert snapshot.latest_price_bar is latest
    assert isinstance(snapshot.price_bars, tuple)
