"""Shared test-only builders for committee tests. Not collected by pytest (no test_ prefix)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

from investment_committee.committee.models import AgentReport, AgentStance, CommitteeRole, EvidenceItem
from investment_committee.domain import Asset, AssetType, MarketSnapshot, PriceBar
from investment_committee.indicators import IndicatorSnapshot

UTC_NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)

_INDICATOR_FIELDS = (
    "latest_close",
    "sma_20",
    "sma_50",
    "sma_200",
    "return_1d",
    "return_30d",
    "return_90d",
    "return_ytd",
    "annualized_volatility",
    "maximum_drawdown",
    "rsi_14",
)


def make_asset(ticker: str = "AAPL") -> Asset:
    return Asset(
        ticker=ticker,
        name="Apple Inc.",
        asset_type=AssetType.STOCK,
        exchange="NASDAQ",
        currency="USD",
    )


def make_market_snapshot(asset: Asset | None = None) -> MarketSnapshot:
    asset = asset if asset is not None else make_asset()
    bar = PriceBar(
        timestamp=UTC_NOW,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=1_000,
    )
    return MarketSnapshot(asset=asset, generated_at=UTC_NOW, price_bars=[bar])


def make_snapshot(asset: Asset | None = None, **indicators: Decimal | None) -> IndicatorSnapshot:
    """Build an IndicatorSnapshot with every indicator defaulting to None.

    Pass indicator field names (e.g. `sma_20=Decimal("100")`) to override.
    """
    fields: dict[str, Decimal | None] = {name: None for name in _INDICATOR_FIELDS}
    fields.update(indicators)
    return IndicatorSnapshot(
        market_snapshot=make_market_snapshot(asset),
        generated_at=UTC_NOW,
        **fields,
    )


def make_report(
    role: CommitteeRole,
    stance: AgentStance,
    confidence: Decimal = Decimal("0.90"),
    *,
    asset: Asset | None = None,
    evidence: Sequence[EvidenceItem] = (),
    generated_at: datetime = UTC_NOW,
) -> AgentReport:
    return AgentReport(
        asset=asset if asset is not None else make_asset(),
        role=role,
        stance=stance,
        confidence=confidence,
        summary=f"{stance.value} summary",
        reasoning=f"{stance.value} reasoning",
        evidence=evidence,
        generated_at=generated_at,
    )
