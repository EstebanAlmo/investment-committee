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


def make_fx_dataframe() -> pd.DataFrame:
    # Real EURUSD=X bars from Yahoo Finance (2025-10-14/15): on the second bar
    # Yahoo reports a Low above both Open and Close - its FX daily high/low
    # and open/close come from different quote snapshots.
    index = pd.to_datetime(["2025-10-13 23:00", "2025-10-14 23:00"], utc=True)
    return pd.DataFrame(
        {
            "Open": [1.1566850, 1.1602274],
            "High": [1.1607260, 1.1648223],
            "Low": [1.1550000, 1.1602544],
            "Close": [1.1566850, 1.1602274],
            "Volume": [0, 0],
        },
        index=index,
    )


@patch(PATCH_TARGET)
def test_fx_bar_with_close_outside_high_low_range_is_widened(mock_ticker_cls: MagicMock) -> None:
    mock_ticker_cls.return_value.history.return_value = make_fx_dataframe()

    snapshot = YFinanceProvider().get_daily_history("eurusd=x")

    assert snapshot.asset.ticker == "EURUSD=X"
    assert len(snapshot.price_bars) == 2
    widened = snapshot.price_bars[1]
    assert widened.close == Decimal("1.1602274")
    assert widened.low == Decimal("1.1602274")
    assert widened.high == Decimal("1.1648223")
    assert widened.volume == 0
    mock_ticker_cls.assert_called_once_with("EURUSD=X")


@patch(PATCH_TARGET)
def test_equity_bar_with_close_outside_high_low_range_still_raises(mock_ticker_cls: MagicMock) -> None:
    df = make_history_dataframe()
    df.loc[df.index[0], "Close"] = 98.0  # below Low=99.0
    mock_ticker_cls.return_value.history.return_value = df

    with pytest.raises(MarketDataError):
        YFinanceProvider().get_daily_history("AAPL")


@patch(PATCH_TARGET)
def test_rows_with_missing_prices_are_dropped(mock_ticker_cls: MagicMock) -> None:
    df = make_history_dataframe()
    df.loc[pd.Timestamp("2026-07-22", tz="UTC")] = [float("nan"), float("nan"), float("nan"), float("nan"), float("nan")]
    mock_ticker_cls.return_value.history.return_value = df

    snapshot = YFinanceProvider().get_daily_history("AAPL")

    assert len(snapshot.price_bars) == 2


@patch(PATCH_TARGET)
def test_missing_volume_on_fx_bar_becomes_zero(mock_ticker_cls: MagicMock) -> None:
    df = make_fx_dataframe()
    df["Volume"] = [float("nan"), float("nan")]
    mock_ticker_cls.return_value.history.return_value = df

    snapshot = YFinanceProvider().get_daily_history("EURUSD=X")

    assert [bar.volume for bar in snapshot.price_bars] == [0, 0]


@patch(PATCH_TARGET)
def test_all_rows_missing_prices_raises(mock_ticker_cls: MagicMock) -> None:
    df = make_history_dataframe()
    df[["Open", "High", "Low", "Close"]] = float("nan")
    mock_ticker_cls.return_value.history.return_value = df

    with pytest.raises(MarketDataError, match="no valid price bars"):
        YFinanceProvider().get_daily_history("EURUSD=X")


@pytest.mark.parametrize("bad_price", [0.0, -1.0, float("inf")])
@patch(PATCH_TARGET)
def test_non_positive_or_infinite_price_raises(mock_ticker_cls: MagicMock, bad_price: float) -> None:
    df = make_fx_dataframe()
    df.loc[df.index[0], "Open"] = bad_price
    mock_ticker_cls.return_value.history.return_value = df

    with pytest.raises(MarketDataError):
        YFinanceProvider().get_daily_history("EURUSD=X")


@patch(PATCH_TARGET)
def test_bars_are_sorted_chronologically_and_duplicates_dropped(mock_ticker_cls: MagicMock) -> None:
    df = make_history_dataframe()
    df = pd.concat([df.iloc[[1]], df.iloc[[0]], df.iloc[[1]]])
    mock_ticker_cls.return_value.history.return_value = df

    snapshot = YFinanceProvider().get_daily_history("AAPL")

    timestamps = [bar.timestamp for bar in snapshot.price_bars]
    assert timestamps == sorted(timestamps)
    assert len(timestamps) == 2


@patch(PATCH_TARGET)
def test_a_year_of_quirky_fx_bars_flows_through_indicators_and_committee(mock_ticker_cls: MagicMock) -> None:
    """A Yahoo-shaped year of EURUSD=X bars - zero volume, a trailing
    partial row with missing prices, and periodic bars whose Close sits
    outside [Low, High] - must produce a full committee decision."""
    from investment_committee.application import build_default_application_service

    index = pd.date_range("2025-10-13 23:00", periods=260, freq="D", tz="UTC")
    closes = [1.16 + 0.0004 * ((day % 11) - 5) + 0.0001 * day for day in range(260)]
    df = pd.DataFrame(
        {
            "Open": closes,
            "High": [close + 0.003 for close in closes],
            "Low": [close - 0.003 for close in closes],
            "Close": closes,
            "Volume": [0] * 260,
        },
        index=index,
    )
    for day in range(5, 260, 29):  # Yahoo's quirk: Close just below the reported Low
        df.iloc[day, df.columns.get_loc("Low")] = closes[day] + 0.00003
    df.loc[index[-1] + pd.Timedelta(days=1)] = [float("nan")] * 4 + [0]
    mock_ticker_cls.return_value.history.return_value = df

    result = build_default_application_service().analyze_asset("EURUSD=X")

    assert result.asset.ticker == "EURUSD=X"
    assert len(result.market_snapshot.price_bars) == 260
    assert result.indicator_snapshot.sma_200 is not None
    assert result.decision.action.value in {"BUY", "SELL", "HOLD"}
    mock_ticker_cls.assert_called_once_with("EURUSD=X")


# --- Intraday history ---


@patch(PATCH_TARGET)
def test_get_intraday_history_fetches_requested_interval_and_period(mock_ticker_cls: MagicMock) -> None:
    df = make_fx_dataframe()
    df.index = pd.to_datetime(["2026-10-13 08:00", "2026-10-13 09:00"], utc=True)
    mock_ticker_cls.return_value.history.return_value = df

    snapshot = YFinanceProvider().get_intraday_history("eurusd=x", interval="1h", period="60d")

    assert snapshot.asset.ticker == "EURUSD=X"
    assert [bar.timestamp.hour for bar in snapshot.price_bars] == [8, 9]
    mock_ticker_cls.return_value.history.assert_called_once_with(period="60d", interval="1h")


@patch(PATCH_TARGET)
def test_get_intraday_history_raises_on_empty_response(mock_ticker_cls: MagicMock) -> None:
    mock_ticker_cls.return_value.history.return_value = pd.DataFrame()

    with pytest.raises(MarketDataError):
        YFinanceProvider().get_intraday_history("EURUSD=X", interval="1h", period="60d")


def test_base_provider_reports_intraday_as_unsupported_by_default() -> None:
    from investment_committee.market_intelligence.providers.base import MarketDataProvider

    class DailyOnly(MarketDataProvider):
        def get_daily_history(self, ticker: str) -> MarketSnapshot:
            raise NotImplementedError

    with pytest.raises(MarketDataError, match="intraday"):
        DailyOnly().get_intraday_history("EURUSD=X", interval="1h", period="60d")


@patch(PATCH_TARGET)
def test_fx_daily_bar_stamped_for_the_next_day_is_dropped(mock_ticker_cls: MagicMock) -> None:
    # Real case (2026-10-13): fetched at 21:00 UTC, Yahoo already had a
    # daily EURUSD=X bar for 14 Oct stamped 00:00 Europe/London (= 13 Oct
    # 23:00 UTC), because the FX day rolls over at 22:00 London time.
    from datetime import datetime, timezone

    index = pd.DatetimeIndex(["2026-10-12", "2026-10-13", "2026-10-14"]).tz_localize("Europe/London")
    df = pd.DataFrame(
        {"Open": [1.12, 1.121, 1.1201], "High": [1.125, 1.126, 1.1205], "Low": [1.118, 1.119, 1.1199], "Close": [1.121, 1.1201, 1.1202], "Volume": [0, 0, 0]},
        index=index,
    )
    mock_ticker_cls.return_value.history.return_value = df
    fetched_at = datetime(2026, 10, 13, 21, 1, tzinfo=timezone.utc)

    snapshot = YFinanceProvider(clock=lambda: fetched_at).get_daily_history("EURUSD=X")

    assert [bar.timestamp.date().isoformat() for bar in snapshot.price_bars] == ["2026-10-12", "2026-10-13"]
    assert all(bar.timestamp <= fetched_at for bar in snapshot.price_bars)
    assert snapshot.generated_at == fetched_at


@patch(PATCH_TARGET)
def test_a_bar_stamped_exactly_at_the_fetch_moment_is_kept(mock_ticker_cls: MagicMock) -> None:
    from datetime import datetime, timezone

    mock_ticker_cls.return_value.history.return_value = make_history_dataframe()
    at_last_bar = datetime(2026, 7, 21, tzinfo=timezone.utc)

    snapshot = YFinanceProvider(clock=lambda: at_last_bar).get_daily_history("AAPL")

    assert len(snapshot.price_bars) == 2
