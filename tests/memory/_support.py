"""Shared test-only builders for memory tests. Not collected by pytest (no test_ prefix)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from investment_committee.committee import (
    AgentReport,
    AgentStance,
    CommitteeRole,
    EvidenceItem,
    InvestmentAction,
    InvestmentDecision,
)
from investment_committee.domain import Asset, AssetType, MarketSnapshot, PriceBar
from investment_committee.indicators import IndicatorSnapshot
from investment_committee.memory.models import DecisionRecord, DecisionRecordId, StrategyMetadata

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


def make_asset(ticker: str = "MSFT") -> Asset:
    return Asset(ticker=ticker, name=None, asset_type=AssetType.STOCK, exchange=None, currency=None)


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


def make_indicator_snapshot(
    market_snapshot: MarketSnapshot | None = None, **indicators: Decimal | None
) -> IndicatorSnapshot:
    market_snapshot = market_snapshot if market_snapshot is not None else make_market_snapshot()
    fields: dict[str, Decimal | None] = {name: None for name in _INDICATOR_FIELDS}
    fields.update(indicators)
    return IndicatorSnapshot(market_snapshot=market_snapshot, generated_at=UTC_NOW, **fields)


def make_evidence_item(
    key: str = "close_price",
    value: Decimal | str = Decimal("151.02"),
    interpretation: str = "close is 151.02",
) -> EvidenceItem:
    return EvidenceItem(key=key, value=value, interpretation=interpretation)


def make_agent_report(
    asset: Asset | None = None,
    role: CommitteeRole = CommitteeRole.TECHNICAL_ANALYST,
    evidence: list[EvidenceItem] | None = None,
) -> AgentReport:
    asset = asset if asset is not None else make_asset()
    return AgentReport(
        asset=asset,
        role=role,
        stance=AgentStance.NEUTRAL,
        confidence=Decimal("0.50"),
        summary=f"{role.value} summary",
        reasoning=f"{role.value} reasoning",
        evidence=evidence if evidence is not None else [make_evidence_item()],
        generated_at=UTC_NOW,
    )


def make_decision(asset: Asset | None = None) -> InvestmentDecision:
    asset = asset if asset is not None else make_asset()
    reports = [
        make_agent_report(asset=asset, role=CommitteeRole.TECHNICAL_ANALYST),
        make_agent_report(asset=asset, role=CommitteeRole.RISK_MANAGER),
        make_agent_report(asset=asset, role=CommitteeRole.DEVILS_ADVOCATE),
    ]
    return InvestmentDecision(
        asset=asset,
        action=InvestmentAction.HOLD,
        confidence=Decimal("0.90"),
        summary=f"HOLD {asset.ticker}: unanimous neutral.",
        agent_reports=reports,
        dissenting_opinions=(),
        risks=(),
        generated_at=UTC_NOW,
    )


def make_strategy_metadata(**overrides: str) -> StrategyMetadata:
    fields = {
        "strategy_name": "deterministic_committee",
        "strategy_version": "1",
        "indicator_engine_version": "1",
        "application_version": "0.1.0",
    }
    fields.update(overrides)
    return StrategyMetadata(**fields)


def make_decision_record(
    asset: Asset | None = None,
    record_id: str = "11111111-1111-4111-8111-111111111111",
    recorded_at: datetime = UTC_NOW,
) -> DecisionRecord:
    asset = asset if asset is not None else make_asset()
    market_snapshot = make_market_snapshot(asset)
    indicator_snapshot = make_indicator_snapshot(market_snapshot)
    decision = make_decision(asset)
    return DecisionRecord(
        record_id=DecisionRecordId(record_id),
        recorded_at=recorded_at,
        strategy_metadata=make_strategy_metadata(),
        market_snapshot=market_snapshot,
        indicator_snapshot=indicator_snapshot,
        decision=decision,
    )
