"""Shared test-only builders and fakes for application tests. Not collected by pytest (no test_ prefix)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from investment_committee.committee import (
    AgentReport,
    AgentStance,
    CommitteeRole,
    InvestmentAction,
    InvestmentDecision,
)
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


def make_asset(ticker: str = "MSFT") -> Asset:
    return Asset(
        ticker=ticker,
        name=None,
        asset_type=AssetType.STOCK,
        exchange=None,
        currency=None,
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


def make_indicator_snapshot(
    market_snapshot: MarketSnapshot | None = None, **indicators: Decimal | None
) -> IndicatorSnapshot:
    market_snapshot = market_snapshot if market_snapshot is not None else make_market_snapshot()
    fields: dict[str, Decimal | None] = {name: None for name in _INDICATOR_FIELDS}
    fields.update(indicators)
    return IndicatorSnapshot(market_snapshot=market_snapshot, generated_at=UTC_NOW, **fields)


def make_decision(asset: Asset | None = None) -> InvestmentDecision:
    asset = asset if asset is not None else make_asset()

    def _report(role: CommitteeRole) -> AgentReport:
        return AgentReport(
            asset=asset,
            role=role,
            stance=AgentStance.NEUTRAL,
            confidence=Decimal("0.50"),
            summary=f"{role.value} summary",
            reasoning=f"{role.value} reasoning",
            evidence=(),
            generated_at=UTC_NOW,
        )

    reports = [
        _report(CommitteeRole.TECHNICAL_ANALYST),
        _report(CommitteeRole.RISK_MANAGER),
        _report(CommitteeRole.DEVILS_ADVOCATE),
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


class FakeMarketIntelligenceService:
    """Duck-typed MarketIntelligenceService stand-in; no subclassing required."""

    def __init__(self, result: MarketSnapshot | Exception, call_log: list[str] | None = None) -> None:
        self._result = result
        self._call_log = call_log
        self.calls: list[str] = []

    def get_daily_history(self, ticker: str) -> MarketSnapshot:
        self.calls.append(ticker)
        if self._call_log is not None:
            self._call_log.append("market")
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeIndicatorEngine:
    """Duck-typed IndicatorEngine stand-in; no subclassing required."""

    def __init__(self, result: IndicatorSnapshot | Exception, call_log: list[str] | None = None) -> None:
        self._result = result
        self._call_log = call_log
        self.calls: list[MarketSnapshot] = []

    def calculate(self, snapshot: MarketSnapshot) -> IndicatorSnapshot:
        self.calls.append(snapshot)
        if self._call_log is not None:
            self._call_log.append("indicator")
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeCommitteeEngine:
    """Duck-typed DeterministicCommitteeEngine stand-in; no subclassing required."""

    def __init__(self, result: InvestmentDecision | Exception, call_log: list[str] | None = None) -> None:
        self._result = result
        self._call_log = call_log
        self.calls: list[IndicatorSnapshot] = []

    def run(self, snapshot: IndicatorSnapshot) -> InvestmentDecision:
        self.calls.append(snapshot)
        if self._call_log is not None:
            self._call_log.append("committee")
        if isinstance(self._result, Exception):
            raise self._result
        return self._result
