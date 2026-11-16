"""PriceBar and MarketSnapshot: raw and aggregated market data for an Asset."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping, Sequence

from ._validation import require_timezone_aware
from .assets import Asset


@dataclass(frozen=True)
class PriceBar:
    """A single OHLCV price bar."""

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    def __post_init__(self) -> None:
        require_timezone_aware(self.timestamp, "timestamp")
        if self.volume < 0:
            raise ValueError("volume must not be negative")
        if self.high < self.open or self.high < self.low or self.high < self.close:
            raise ValueError("high must not be lower than open, low, or close")
        if self.low > self.open or self.low > self.high or self.low > self.close:
            raise ValueError("low must not be higher than open, high, or close")


@dataclass(frozen=True)
class MarketSnapshot:
    """An asset's price history plus derived metrics, as of a point in time."""

    asset: Asset
    generated_at: datetime
    price_bars: Sequence[PriceBar]
    metrics: Mapping[str, Decimal | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_timezone_aware(self.generated_at, "generated_at")
        if len(self.price_bars) == 0:
            raise ValueError("price_bars must not be empty")
        object.__setattr__(self, "price_bars", tuple(self.price_bars))
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))

    @property
    def latest_price_bar(self) -> PriceBar:
        """The price bar with the most recent timestamp."""
        return max(self.price_bars, key=lambda bar: bar.timestamp)
