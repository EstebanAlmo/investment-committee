"""Memory and Storage: the Decision Ledger, the durable record every completed analysis.

`memory/` depends only on `domain/`, `indicators/`, and `committee/` - never
on `application/`, the CLI, `integrations/`, or any other layer's concrete
persistence/integration adapters. `DecisionRecord` embeds the exact
MarketSnapshot, IndicatorSnapshot, and InvestmentDecision produced by one
analysis pass; `DecisionLedger` is the append-only port every adapter
implements. See docs/architecture.md, layer 4, and
docs/specs/spec-005-decision-ledger.md.
"""

from __future__ import annotations

from .errors import (
    DecisionNotFoundError,
    DecisionRecordDeserializationError,
    DuplicateDecisionError,
    LedgerError,
    LedgerReadError,
    LedgerWriteError,
)
from .ledgers import DEFAULT_LEDGER_PATH, DecisionLedger, InMemoryDecisionLedger, JsonLinesDecisionLedger
from .models import DecisionRecord, DecisionRecordId, StrategyMetadata
from .versioning import DETERMINISTIC_STRATEGY_METADATA

__all__ = [
    "DecisionRecord",
    "DecisionRecordId",
    "StrategyMetadata",
    "DecisionLedger",
    "InMemoryDecisionLedger",
    "JsonLinesDecisionLedger",
    "DEFAULT_LEDGER_PATH",
    "LedgerError",
    "DuplicateDecisionError",
    "DecisionNotFoundError",
    "LedgerWriteError",
    "LedgerReadError",
    "DecisionRecordDeserializationError",
    "DETERMINISTIC_STRATEGY_METADATA",
]
