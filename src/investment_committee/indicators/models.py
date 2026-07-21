"""IndicatorSnapshot: computed technical and risk indicators for a MarketSnapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from investment_committee.domain import Asset, MarketSnapshot


@dataclass(frozen=True)
class IndicatorSnapshot:
    """Technical and risk indicators derived from a single MarketSnapshot.

    Indicators that cannot be calculated because there is insufficient
    historical data are None rather than raising an error.
    """

    market_snapshot: MarketSnapshot
    generated_at: datetime
    latest_close: Decimal | None
    sma_20: Decimal | None
    sma_50: Decimal | None
    sma_200: Decimal | None
    return_1d: Decimal | None
    return_30d: Decimal | None
    return_90d: Decimal | None
    return_ytd: Decimal | None
    annualized_volatility: Decimal | None
    maximum_drawdown: Decimal | None
    rsi_14: Decimal | None

    def __post_init__(self) -> None:
        if self.generated_at.tzinfo is None or self.generated_at.tzinfo.utcoffset(self.generated_at) is None:
            raise ValueError("generated_at must be timezone-aware")

    @property
    def asset(self) -> Asset:
        """The asset this snapshot's indicators were calculated for."""
        return self.market_snapshot.asset
