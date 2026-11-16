"""Tests for investment_committee.domain.assets."""

import pytest

from investment_committee.domain.assets import Asset, AssetType


def test_ticker_is_normalized_to_uppercase() -> None:
    asset = Asset(
        ticker=" aapl ",
        name="Apple Inc.",
        asset_type=AssetType.STOCK,
        exchange="NASDAQ",
        currency="USD",
    )
    assert asset.ticker == "AAPL"


def test_blank_ticker_is_rejected() -> None:
    with pytest.raises(ValueError):
        Asset(
            ticker="   ",
            name=None,
            asset_type=AssetType.STOCK,
            exchange=None,
            currency=None,
        )


def test_asset_type_enum_values() -> None:
    assert AssetType.STOCK.value == "STOCK"
    assert AssetType.ETF.value == "ETF"
    assert {member.value for member in AssetType} == {"STOCK", "ETF"}
