"""Explicit DecisionRecord <-> JSON-compatible dict serialization (schema_version 1).

Deliberately not dataclasses.asdict(), pickle, or repr - see
docs/specs/spec-005-decision-ledger.md S16. Every field's encoding is
explicit: Decimal <-> str (never float), datetime <-> ISO 8601 string
(timezone-aware), Enum <-> its .value, and EvidenceItem.value carries an
explicit "decimal"/"string" discriminator so a categorical string that
happens to look numeric is never misread as a Decimal.

market_snapshot.asset is the single source of truth for asset identity on
the wire: DecisionRecord.__post_init__ already guarantees market_snapshot,
indicator_snapshot, and decision share one asset, so indicator_snapshot's
embedded MarketSnapshot and every AgentReport's asset are not repeated on
the wire - they are reconstructed from the one serialized asset.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from investment_committee.committee import (
    AgentReport,
    AgentStance,
    CommitteeRole,
    EvidenceItem,
    InvestmentAction,
    InvestmentDecision,
    RiskItem,
    RiskSeverity,
)
from investment_committee.domain import Asset, AssetType, MarketSnapshot, PriceBar
from investment_committee.indicators import IndicatorSnapshot

from .errors import DecisionRecordDeserializationError
from .models import DecisionRecord, DecisionRecordId, StrategyMetadata

SCHEMA_VERSION = 1

_INDICATOR_DECIMAL_FIELDS = (
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


def to_persisted_dict(record: DecisionRecord) -> dict[str, Any]:
    """Return a JSON-compatible dict representing `record`, schema_version 1."""
    return {
        "schema_version": SCHEMA_VERSION,
        "record_id": str(record.record_id),
        "recorded_at": record.recorded_at.isoformat(),
        "strategy_metadata": _strategy_metadata_to_dict(record.strategy_metadata),
        "market_snapshot": _market_snapshot_to_dict(record.market_snapshot),
        "indicator_snapshot": _indicator_snapshot_to_dict(record.indicator_snapshot),
        "decision": _decision_to_dict(record.decision),
    }


def from_persisted_dict(data: dict[str, Any]) -> DecisionRecord:
    """Reconstruct a DecisionRecord from a dict produced by to_persisted_dict().

    Raises DecisionRecordDeserializationError for an unsupported
    schema_version, a missing or malformed required field, or a value that
    fails a reconstructed domain dataclass's own __post_init__ validation.
    Unknown top-level keys are ignored. The original exception is preserved
    as __cause__.
    """
    try:
        return _from_persisted_dict(data)
    except (KeyError, ValueError, TypeError, ArithmeticError, AttributeError) as exc:
        raise DecisionRecordDeserializationError(
            f"could not deserialize DecisionRecord: {exc}"
        ) from exc


def _from_persisted_dict(data: dict[str, Any]) -> DecisionRecord:
    schema_version = data.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise DecisionRecordDeserializationError(f"unsupported schema_version: {schema_version!r}")

    market_snapshot = _market_snapshot_from_dict(data["market_snapshot"])
    asset = market_snapshot.asset

    return DecisionRecord(
        record_id=DecisionRecordId(data["record_id"]),
        recorded_at=datetime.fromisoformat(data["recorded_at"]),
        strategy_metadata=_strategy_metadata_from_dict(data["strategy_metadata"]),
        market_snapshot=market_snapshot,
        indicator_snapshot=_indicator_snapshot_from_dict(data["indicator_snapshot"], market_snapshot),
        decision=_decision_from_dict(data["decision"], asset),
    )


def _strategy_metadata_to_dict(metadata: StrategyMetadata) -> dict[str, Any]:
    return {
        "strategy_name": metadata.strategy_name,
        "strategy_version": metadata.strategy_version,
        "indicator_engine_version": metadata.indicator_engine_version,
        "application_version": metadata.application_version,
    }


def _strategy_metadata_from_dict(data: dict[str, Any]) -> StrategyMetadata:
    return StrategyMetadata(
        strategy_name=data["strategy_name"],
        strategy_version=data["strategy_version"],
        indicator_engine_version=data["indicator_engine_version"],
        application_version=data["application_version"],
    )


def _asset_to_dict(asset: Asset) -> dict[str, Any]:
    return {
        "ticker": asset.ticker,
        "name": asset.name,
        "asset_type": asset.asset_type.value,
        "exchange": asset.exchange,
        "currency": asset.currency,
    }


def _asset_from_dict(data: dict[str, Any]) -> Asset:
    return Asset(
        ticker=data["ticker"],
        name=data["name"],
        asset_type=AssetType(data["asset_type"]),
        exchange=data["exchange"],
        currency=data["currency"],
    )


def _price_bar_to_dict(bar: PriceBar) -> dict[str, Any]:
    return {
        "timestamp": bar.timestamp.isoformat(),
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": bar.volume,
    }


def _price_bar_from_dict(data: dict[str, Any]) -> PriceBar:
    return PriceBar(
        timestamp=datetime.fromisoformat(data["timestamp"]),
        open=Decimal(data["open"]),
        high=Decimal(data["high"]),
        low=Decimal(data["low"]),
        close=Decimal(data["close"]),
        volume=data["volume"],
    )


def _market_snapshot_to_dict(snapshot: MarketSnapshot) -> dict[str, Any]:
    return {
        "asset": _asset_to_dict(snapshot.asset),
        "generated_at": snapshot.generated_at.isoformat(),
        "price_bars": [_price_bar_to_dict(bar) for bar in snapshot.price_bars],
        "metrics": {
            key: (str(value) if value is not None else None) for key, value in snapshot.metrics.items()
        },
    }


def _market_snapshot_from_dict(data: dict[str, Any]) -> MarketSnapshot:
    return MarketSnapshot(
        asset=_asset_from_dict(data["asset"]),
        generated_at=datetime.fromisoformat(data["generated_at"]),
        price_bars=[_price_bar_from_dict(bar) for bar in data["price_bars"]],
        metrics={
            key: (Decimal(value) if value is not None else None) for key, value in data["metrics"].items()
        },
    )


def _indicator_snapshot_to_dict(snapshot: IndicatorSnapshot) -> dict[str, Any]:
    result: dict[str, Any] = {"generated_at": snapshot.generated_at.isoformat()}
    for field_name in _INDICATOR_DECIMAL_FIELDS:
        value = getattr(snapshot, field_name)
        result[field_name] = str(value) if value is not None else None
    return result


def _indicator_snapshot_from_dict(data: dict[str, Any], market_snapshot: MarketSnapshot) -> IndicatorSnapshot:
    fields = {
        field_name: (Decimal(data[field_name]) if data[field_name] is not None else None)
        for field_name in _INDICATOR_DECIMAL_FIELDS
    }
    return IndicatorSnapshot(
        market_snapshot=market_snapshot,
        generated_at=datetime.fromisoformat(data["generated_at"]),
        **fields,
    )


def _evidence_item_to_dict(item: EvidenceItem) -> dict[str, Any]:
    if isinstance(item.value, Decimal):
        value_type = "decimal"
        value: Any = str(item.value)
    else:
        value_type = "string"
        value = item.value
    return {
        "key": item.key,
        "value_type": value_type,
        "value": value,
        "interpretation": item.interpretation,
    }


def _evidence_item_from_dict(data: dict[str, Any]) -> EvidenceItem:
    value_type = data.get("value_type")
    if value_type == "decimal":
        value: Decimal | str = Decimal(data["value"])
    elif value_type == "string":
        value = data["value"]
    else:
        raise DecisionRecordDeserializationError(
            f"EvidenceItem.value_type must be 'decimal' or 'string', got {value_type!r}"
        )
    return EvidenceItem(key=data["key"], value=value, interpretation=data["interpretation"])


def _agent_report_to_dict(report: AgentReport) -> dict[str, Any]:
    return {
        "role": report.role.value,
        "stance": report.stance.value,
        "confidence": str(report.confidence),
        "summary": report.summary,
        "reasoning": report.reasoning,
        "evidence": [_evidence_item_to_dict(item) for item in report.evidence],
        "generated_at": report.generated_at.isoformat(),
    }


def _agent_report_from_dict(data: dict[str, Any], asset: Asset) -> AgentReport:
    return AgentReport(
        asset=asset,
        role=CommitteeRole(data["role"]),
        stance=AgentStance(data["stance"]),
        confidence=Decimal(data["confidence"]),
        summary=data["summary"],
        reasoning=data["reasoning"],
        evidence=[_evidence_item_from_dict(item) for item in data["evidence"]],
        generated_at=datetime.fromisoformat(data["generated_at"]),
    )


def _risk_item_to_dict(risk: RiskItem) -> dict[str, Any]:
    return {
        "key": risk.key,
        "severity": risk.severity.value,
        "description": risk.description,
        "source_role": risk.source_role.value,
    }


def _risk_item_from_dict(data: dict[str, Any]) -> RiskItem:
    return RiskItem(
        key=data["key"],
        severity=RiskSeverity(data["severity"]),
        description=data["description"],
        source_role=CommitteeRole(data["source_role"]),
    )


def _decision_to_dict(decision: InvestmentDecision) -> dict[str, Any]:
    return {
        "action": decision.action.value,
        "confidence": str(decision.confidence),
        "summary": decision.summary,
        "agent_reports": [_agent_report_to_dict(report) for report in decision.agent_reports],
        "dissenting_opinions": [_agent_report_to_dict(report) for report in decision.dissenting_opinions],
        "risks": [_risk_item_to_dict(risk) for risk in decision.risks],
        "generated_at": decision.generated_at.isoformat(),
    }


def _decision_from_dict(data: dict[str, Any], asset: Asset) -> InvestmentDecision:
    return InvestmentDecision(
        asset=asset,
        action=InvestmentAction(data["action"]),
        confidence=Decimal(data["confidence"]),
        summary=data["summary"],
        agent_reports=[_agent_report_from_dict(report, asset) for report in data["agent_reports"]],
        dissenting_opinions=[
            _agent_report_from_dict(report, asset) for report in data["dissenting_opinions"]
        ],
        risks=[_risk_item_from_dict(risk) for risk in data["risks"]],
        generated_at=datetime.fromisoformat(data["generated_at"]),
    )
