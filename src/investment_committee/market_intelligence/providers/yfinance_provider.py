"""YFinanceProvider: MarketDataProvider backed by the yfinance library.

This is the only file in the project allowed to import yfinance. Everything
downstream depends on MarketDataProvider instead.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import yfinance as yf

from investment_committee.domain import Asset, AssetType, MarketSnapshot, PriceBar

from .base import MarketDataError, MarketDataProvider


class YFinanceProvider(MarketDataProvider):
    """Fetches one year of daily OHLCV history from Yahoo Finance via yfinance."""

    def get_daily_history(self, ticker: str) -> MarketSnapshot:
        normalized_ticker = ticker.strip().upper()

        if not normalized_ticker:
            raise MarketDataError("ticker must not be blank")

        try:
            history = yf.Ticker(normalized_ticker).history(
                period="1y",
                interval="1d",
            )
        except Exception as exc:  # yfinance raises varied, undocumented exceptions
            raise MarketDataError(
                f"failed to fetch history for {normalized_ticker}: {exc}"
            ) from exc

        if history is None or history.empty:
            raise MarketDataError(
                f"no price history returned for {normalized_ticker}"
            )

        price_bars: list[PriceBar] = []

        for timestamp, row in history.iterrows():
            try:
                py_timestamp = timestamp.to_pydatetime()

                if py_timestamp.tzinfo is None:
                    py_timestamp = py_timestamp.replace(tzinfo=timezone.utc)

                price_bars.append(
                    PriceBar(
                        timestamp=py_timestamp,
                        open=Decimal(str(row["Open"])),
                        high=Decimal(str(row["High"])),
                        low=Decimal(str(row["Low"])),
                        close=Decimal(str(row["Close"])),
                        volume=int(row["Volume"]),
                    )
                )
            except (AttributeError, KeyError, ValueError, TypeError) as exc:
                raise MarketDataError(
                    f"malformed price row for {normalized_ticker} "
                    f"at {timestamp}: {exc}"
                ) from exc

        asset = Asset(
            ticker=normalized_ticker,
            name=None,
            asset_type=AssetType.STOCK,
            exchange=None,
            currency=None,
        )

        return MarketSnapshot(
            asset=asset,
            generated_at=datetime.now(timezone.utc),
            price_bars=price_bars,
        )