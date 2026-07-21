"""Tests for investment_committee.indicators.engine."""

from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Sequence

import pytest

from investment_committee.domain import Asset, AssetType, MarketSnapshot, PriceBar
from investment_committee.indicators.engine import IndicatorCalculationError, IndicatorEngine

UTC_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_asset() -> Asset:
    return Asset(
        ticker="AAPL",
        name="Apple Inc.",
        asset_type=AssetType.STOCK,
        exchange="NASDAQ",
        currency="USD",
    )


def make_bar(timestamp: datetime, close: str) -> PriceBar:
    price = Decimal(close)
    return PriceBar(
        timestamp=timestamp,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=1_000,
    )


def make_bars(closes: Sequence[str], start: datetime = UTC_START) -> list[PriceBar]:
    return [make_bar(start + timedelta(days=i), close) for i, close in enumerate(closes)]


def make_snapshot(bars: Sequence[PriceBar]) -> MarketSnapshot:
    return MarketSnapshot(
        asset=make_asset(),
        generated_at=bars[-1].timestamp + timedelta(days=1),
        price_bars=bars,
    )


def test_valid_calculation_with_sufficient_history() -> None:
    closes = [str(100 + i) for i in range(200)]
    snapshot = make_snapshot(make_bars(closes))

    result = IndicatorEngine().calculate(snapshot)

    assert result.latest_close == Decimal("299")
    assert result.sma_20 == Decimal("289.5")
    assert result.sma_50 == Decimal("274.5")
    assert result.sma_200 == Decimal("199.5")
    assert result.return_1d is not None
    assert result.return_30d is not None
    assert result.return_90d is not None
    assert result.annualized_volatility is not None
    assert result.maximum_drawdown is not None
    assert result.rsi_14 == Decimal("100")
    assert result.generated_at.tzinfo is not None


def test_sma_20_50_200() -> None:
    closes = [str(100 + i) for i in range(200)]
    snapshot = make_snapshot(make_bars(closes))

    result = IndicatorEngine().calculate(snapshot)

    assert result.sma_20 == Decimal("289.5")
    assert result.sma_50 == Decimal("274.5")
    assert result.sma_200 == Decimal("199.5")


def test_returns_1d_30d_90d() -> None:
    closes = ["100"] * 90 + ["100", "110"]
    snapshot = make_snapshot(make_bars(closes))

    result = IndicatorEngine().calculate(snapshot)

    assert result.return_1d == Decimal("0.1")
    assert result.return_30d == Decimal("0.1")
    assert result.return_90d == Decimal("0.1")


def test_return_ytd() -> None:
    bars = [
        make_bar(datetime(2025, 12, 30, tzinfo=timezone.utc), "999"),
        make_bar(datetime(2026, 1, 2, tzinfo=timezone.utc), "100"),
        make_bar(datetime(2026, 1, 5, tzinfo=timezone.utc), "110"),
        make_bar(datetime(2026, 6, 1, tzinfo=timezone.utc), "130"),
    ]
    snapshot = make_snapshot(bars)

    result = IndicatorEngine().calculate(snapshot)

    assert result.return_ytd == Decimal("0.3")


def test_return_ytd_is_none_with_only_one_bar_in_current_year() -> None:
    bars = [
        make_bar(datetime(2025, 6, 1, tzinfo=timezone.utc), "100"),
        make_bar(datetime(2026, 1, 2, tzinfo=timezone.utc), "130"),
    ]
    snapshot = make_snapshot(bars)

    result = IndicatorEngine().calculate(snapshot)

    assert result.return_ytd is None


def test_annualized_volatility() -> None:
    closes = ["100", "102", "99", "104", "97", "105"]
    snapshot = make_snapshot(make_bars(closes))

    result = IndicatorEngine().calculate(snapshot)

    daily_returns = [
        (Decimal(closes[i]) / Decimal(closes[i - 1])) - 1 for i in range(1, len(closes))
    ]
    import statistics

    expected = statistics.stdev(daily_returns) * Decimal(252).sqrt()
    assert result.annualized_volatility == expected


def test_maximum_drawdown() -> None:
    closes = ["100", "120", "60", "90", "150"]
    snapshot = make_snapshot(make_bars(closes))

    result = IndicatorEngine().calculate(snapshot)

    assert result.maximum_drawdown == Decimal("-0.5")


def test_rsi_14() -> None:
    closes = ["100", "103", "102", "105", "104", "107", "106", "109", "108", "111", "110", "113", "112", "115", "114"]
    assert len(closes) == 15
    snapshot = make_snapshot(make_bars(closes))

    result = IndicatorEngine().calculate(snapshot)

    assert result.rsi_14 == Decimal("75")


def test_rsi_14_is_100_when_average_loss_is_zero() -> None:
    closes = [str(100 + i) for i in range(20)]
    snapshot = make_snapshot(make_bars(closes))

    result = IndicatorEngine().calculate(snapshot)

    assert result.rsi_14 == Decimal("100")


def test_rsi_14_is_50_when_gains_and_losses_are_both_zero() -> None:
    closes = ["100"] * 20
    snapshot = make_snapshot(make_bars(closes))

    result = IndicatorEngine().calculate(snapshot)

    assert result.rsi_14 == Decimal("50")


def test_insufficient_history_returns_none_for_long_period_indicators() -> None:
    closes = ["100", "101", "102", "101", "103"]
    snapshot = make_snapshot(make_bars(closes))

    result = IndicatorEngine().calculate(snapshot)

    assert result.sma_20 is None
    assert result.sma_50 is None
    assert result.sma_200 is None
    assert result.return_30d is None
    assert result.return_90d is None
    assert result.rsi_14 is None
    assert result.return_1d is not None
    assert result.maximum_drawdown is not None
    assert result.annualized_volatility is not None


def test_insufficient_history_for_a_single_bar() -> None:
    snapshot = make_snapshot(make_bars(["100"]))

    result = IndicatorEngine().calculate(snapshot)

    assert result.latest_close == Decimal("100")
    assert result.return_1d is None
    assert result.annualized_volatility is None
    assert result.maximum_drawdown is None
    assert result.rsi_14 is None


def test_flat_prices() -> None:
    closes = ["100"] * 20
    snapshot = make_snapshot(make_bars(closes))

    result = IndicatorEngine().calculate(snapshot)

    assert result.sma_20 == Decimal("100")
    assert result.return_1d == Decimal("0")
    assert result.annualized_volatility == Decimal("0")
    assert result.maximum_drawdown == Decimal("0")
    assert result.rsi_14 == Decimal("50")


def test_rising_prices() -> None:
    closes = [str(100 + i) for i in range(20)]
    snapshot = make_snapshot(make_bars(closes))

    result = IndicatorEngine().calculate(snapshot)

    assert result.maximum_drawdown == Decimal("0")
    assert result.rsi_14 == Decimal("100")


def test_unordered_price_bars_raise_indicator_calculation_error() -> None:
    bars = make_bars(["100", "101", "102"])
    bars[1], bars[2] = bars[2], bars[1]
    snapshot = make_snapshot(bars)

    with pytest.raises(IndicatorCalculationError):
        IndicatorEngine().calculate(snapshot)


def test_empty_market_snapshot_raises_indicator_calculation_error() -> None:
    snapshot = make_snapshot(make_bars(["100"]))
    object.__setattr__(snapshot, "price_bars", ())

    with pytest.raises(IndicatorCalculationError):
        IndicatorEngine().calculate(snapshot)


def test_invalid_closing_price_raises_indicator_calculation_error() -> None:
    bars = make_bars(["100", "101"])
    bars.append(make_bar(UTC_START + timedelta(days=2), "0"))
    snapshot = make_snapshot(bars)

    with pytest.raises(IndicatorCalculationError):
        IndicatorEngine().calculate(snapshot)


def test_no_network_access(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("IndicatorEngine must not access the network")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    closes = [str(100 + i) for i in range(200)]
    snapshot = make_snapshot(make_bars(closes))

    result = IndicatorEngine().calculate(snapshot)

    assert result.latest_close == Decimal("299")
