"""AnalysisResult: everything one ApplicationService.analyze_asset() call produced."""

from __future__ import annotations

from dataclasses import dataclass

from investment_committee.committee import InvestmentDecision
from investment_committee.domain import Asset, MarketSnapshot
from investment_committee.indicators import IndicatorSnapshot


@dataclass(frozen=True)
class AnalysisResult:
    """Everything one ApplicationService.analyze_asset() call produced.

    Preserves the exact MarketSnapshot, IndicatorSnapshot, and
    InvestmentDecision instances from a single analysis pass, unmodified —
    this is what a future Epic 7 ledger will persist verbatim.
    """

    market_snapshot: MarketSnapshot
    indicator_snapshot: IndicatorSnapshot
    decision: InvestmentDecision

    def __post_init__(self) -> None:
        if not (
            self.market_snapshot.asset
            == self.indicator_snapshot.asset
            == self.decision.asset
        ):
            raise ValueError(
                "AnalysisResult requires market_snapshot, indicator_snapshot, "
                "and decision to share the same asset"
            )

    @property
    def asset(self) -> Asset:
        """The asset this result's analysis was performed for."""
        return self.decision.asset
