"""Tests for investment_committee.market_intelligence.providers.yfinance_provider.

yfinance is mocked throughout: these tests must not require internet access.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from investment_committee.domain import AssetType, MarketSnapshot
from investment_committee.market_intelligence.providers.base import MarketDataError
from investment_committee.market_intelligence.providers.yfinance_provider import (
    YFinanceProvider,
)

PATCH_TARGET = "investment_committee.market_intelligence.providers.yfinance_provider.yf.Ticker"


def make_history_dataframe() -> pd.DataFrame:
    index = pd.to_datetime(["2026-07-20", "2026-07-21"], utc=True)
    return pd.DataFrame(
        {
            "Open": [100.0, 102.0],
            "High": [105.0, 106.0],
            "Low": [99.0, 101.0],
            "Close": [102.0, 104.0],
            "Volume": [1_000_000, 1_200_000],
        },
        index=index,
    )


@patch(PATCH_TARGET)
def test_get_daily_history_returns_market_snapshot(mock_ticker_cls: MagicMock) -> None:
    mock_ticker_cls.return_value.history.return_value = make_history_dataframe()

    snapshot = YFinanceProvider().get_daily_history("aapl")

    assert isinstance(snapshot, MarketSnapshot)
    assert snapshot.asset.ticker == "AAPL"
    assert snapshot.asset.asset_type == AssetType.STOCK
    assert len(snapshot.price_bars) == 2
    assert snapshot.price_bars[0].open == Decimal("100.0")
    assert snapshot.price_bars[0].volume == 1_000_000
    mock_ticker_cls.assert_called_once_with("AAPL")
    mock_ticker_cls.return_value.history.assert_called_once_with(period="1y", interval="1d")


@patch(PATCH_TARGET)
def test_get_daily_history_raises_on_empty_response(mock_ticker_cls: MagicMock) -> None:
    mock_ticker_cls.return_value.history.return_value = pd.DataFrame()

    with pytest.raises(MarketDataError):
        YFinanceProvider().get_daily_history("NOTATICKER")


@patch(PATCH_TARGET)
def test_get_daily_history_raises_on_provider_error(mock_ticker_cls: MagicMock) -> None:
    mock_ticker_cls.return_value.history.side_effect = RuntimeError("network down")

    with pytest.raises(MarketDataError):
        YFinanceProvider().get_daily_history("AAPL")


@patch(PATCH_TARGET)
def test_get_daily_history_raises_on_malformed_row(mock_ticker_cls: MagicMock) -> None:
    mock_ticker_cls.return_value.history.return_value = make_history_dataframe().drop(
        columns=["Volume"]
    )

    with pytest.raises(MarketDataError):
        YFinanceProvider().get_daily_history("AAPL")


@patch(PATCH_TARGET)
def test_get_daily_history_raises_on_malformed_timestamp(mock_ticker_cls: MagicMock) -> None:
    df = make_history_dataframe()
    df.index = ["not-a-timestamp", "still-not-a-timestamp"]
    mock_ticker_cls.return_value.history.return_value = df

    with pytest.raises(MarketDataError):
        YFinanceProvider().get_daily_history("AAPL")


def test_get_daily_history_rejects_blank_ticker() -> None:
    with pytest.raises(MarketDataError):
        YFinanceProvider().get_daily_history("   ")
