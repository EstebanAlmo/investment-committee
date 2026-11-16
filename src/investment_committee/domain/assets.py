"""Asset: the tradable instrument (stock or ETF) other domain entities refer to."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AssetType(str, Enum):
    """The kind of instrument an Asset represents."""

    STOCK = "STOCK"
    ETF = "ETF"


@dataclass(frozen=True)
class Asset:
    """A tradable instrument identified by its ticker."""

    ticker: str
    name: str | None
    asset_type: AssetType
    exchange: str | None
    currency: str | None

    def __post_init__(self) -> None:
        normalized_ticker = self.ticker.strip().upper()
        if not normalized_ticker:
            raise ValueError("ticker must not be blank")
        object.__setattr__(self, "ticker", normalized_ticker)
