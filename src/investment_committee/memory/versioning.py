"""DETERMINISTIC_STRATEGY_METADATA: version metadata for the deterministic baseline strategy.

Landed here, in memory/, rather than committee/__init__.py or
indicators/__init__.py (its cleaner long-term home) as a deliberate,
flagged, temporary concession to this Epic not modifying those existing
packages - see docs/specs/spec-005-decision-ledger.md S13.
"""

from __future__ import annotations

import investment_committee

from .models import StrategyMetadata

DETERMINISTIC_STRATEGY_METADATA = StrategyMetadata(
    strategy_name="deterministic_committee",
    strategy_version="1",
    indicator_engine_version="1",
    application_version=investment_committee.__version__,
)
