"""YFinanceProvider: MarketDataProvider backed by the yfinance library.

This is the only file in the project allowed to import yfinance. Everything
downstream depends on MarketDataProvider instead.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Callable
from decimal import Decimal, InvalidOperation

import yfinance as yf

from investment_committee.domain import Asset, AssetType, MarketSnapshot, PriceBar

from .base import MarketDataError, MarketDataProvider

_PRICE_COLUMNS = ("Open", "High", "Low", "Close")
_FX_SUFFIX = "=X"


class YFinanceProvider(MarketDataProvider):
    """Fetches daily (one year) and intraday OHLCV history from Yahoo Finance via yfinance.

    Bars stamped AFTER the fetch moment are dropped. Yahoo labels daily FX
    bars with the trading day they belong to at 00:00 Europe/London, and the
    FX day rolls over at 22:00 London time: from then on the series carries
    a bar for the NEXT day, e.g. "2026-10-14 00:00+01:00" (= 2026-10-13
    23:00 UTC) fetched at 21:00 UTC. Using it would date the evidence in the
    future; dropping it leaves the last fully closed FX day as the latest
    bar. (A still-forming bar stamped at or before now is kept; consumers
    that need closed bars filter them, e.g. simulation.closed_bars.)"""

    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._clock = clock

    def get_daily_history(self, ticker: str) -> MarketSnapshot:
        return self._history(ticker, period="1y", interval="1d")

    def get_intraday_history(self, ticker: str, interval: str, period: str) -> MarketSnapshot:
        # Yahoo limits: 1h bars reach back at most 730 days, 15m/5m bars 60
        # days. The latest bar is returned while it is still forming.
        return self._history(ticker, period=period, interval=interval)

    def get_intraday_range(self, ticker: str, interval: str, start: datetime, end: datetime) -> tuple[str, MarketSnapshot]:
        """Intraday history from `start` (inclusive) to `end` (exclusive),
        both timezone-aware: nothing at or after `end` is requested. Returns
        the provider's response as CSV text exactly as received (for an
        immutable original, see market_intelligence/ingest.py) together
        with the parsed snapshot. Yahoo serves 1h bars only for the last
        730 days."""
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise MarketDataError("start and end must be timezone-aware and end after start")
        return self._fetch(ticker, start=start, end=end, interval=interval)

    def _history(self, ticker: str, period: str, interval: str) -> MarketSnapshot:
        return self._fetch(ticker, period=period, interval=interval)[1]

    def _fetch(self, ticker: str, **query: object) -> tuple[str, MarketSnapshot]:
        normalized_ticker = ticker.strip().upper()

        if not normalized_ticker:
            raise MarketDataError("ticker must not be blank")

        try:
            history = yf.Ticker(normalized_ticker).history(**query)
        except Exception as exc:  # yfinance raises varied, undocumented exceptions
            raise MarketDataError(
                f"failed to fetch history for {normalized_ticker}: {exc}"
            ) from exc

        if history is None or history.empty:
            raise MarketDataError(
                f"no price history returned for {normalized_ticker}"
            )

        received = history.to_csv(lineterminator="\n")  # as the provider answered, before any reading
        is_fx = normalized_ticker.endswith(_FX_SUFFIX)
        fetched_at = self._clock()
        bars_by_timestamp: dict[datetime, PriceBar] = {}

        for timestamp, row in history.iterrows():
            try:
                py_timestamp = timestamp.to_pydatetime()

                if py_timestamp.tzinfo is None:
                    py_timestamp = py_timestamp.replace(tzinfo=timezone.utc)
                if py_timestamp > fetched_at:
                    continue  # stamped in the future (see the class docstring)

                prices = [float(row[column]) for column in _PRICE_COLUMNS]
                if any(math.isnan(price) for price in prices):
                    # Yahoo emits partial rows (e.g. a not-yet-settled current
                    # day) with missing prices; such a bar carries no data.
                    continue
                if any(math.isinf(price) or price <= 0 for price in prices):
                    raise ValueError(f"prices must be positive and finite, got {prices}")

                open_, high, low, close = (Decimal(str(price)) for price in prices)
                if is_fx:
                    # Yahoo's daily FX bars take High/Low and Open/Close from
                    # different quote snapshots, so Close (or Open) can sit
                    # slightly outside [Low, High] - observed on real
                    # EURUSD=X history. Widen the range to contain them
                    # instead of rejecting the whole series.
                    high = max(open_, high, low, close)
                    low = min(open_, high, low, close)

                raw_volume = float(row["Volume"])
                volume = 0 if is_fx and math.isnan(raw_volume) else int(raw_volume)

                bars_by_timestamp[py_timestamp] = PriceBar(
                    timestamp=py_timestamp,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                )
            except (AttributeError, KeyError, ValueError, TypeError, InvalidOperation) as exc:
                raise MarketDataError(
                    f"malformed price row for {normalized_ticker} "
                    f"at {timestamp}: {exc}"
                ) from exc

        if not bars_by_timestamp:
            raise MarketDataError(
                f"no valid price bars returned for {normalized_ticker}"
            )

        # Chronological order, one bar per timestamp (a repeated timestamp
        # keeps the last row the provider sent).
        price_bars = [bars_by_timestamp[key] for key in sorted(bars_by_timestamp)]

        asset = Asset(
            ticker=normalized_ticker,
            name=None,
            asset_type=AssetType.STOCK,
            exchange=None,
            currency=None,
        )

        return received, MarketSnapshot(
            asset=asset,
            generated_at=fetched_at,
            price_bars=price_bars,
        )
