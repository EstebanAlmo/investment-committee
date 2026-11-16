"""Static check: application/ and the dev CLI must not import portfolio,
evaluation, or interfaces. See docs/specs/spec-004-application-service.md,
"Testing plan" -> "Composition-root / architecture check".

`investment_committee.memory` is deliberately not in FORBIDDEN_MODULES: per
docs/architecture.md's dependency direction and
docs/specs/spec-005-decision-ledger.md, `application/` legitimately depends
on `memory/`'s port and models (RecordDecisionUseCase,
build_default_record_decision_use_case). However, only
`application/factory.py` is the sanctioned composition root allowed to
import a *concrete* DecisionLedger adapter (mirroring
`market_intelligence/providers/`'s port + adapter precedent) - see
test_only_factory_imports_concrete_ledger_adapters below.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# `investment_committee.simulation` was forbidden while it was empty (spec-004
# scoped Epic 6 only). docs/architecture.md has application/ compose it once
# simulated trading lands (application/trading.py) - same precedent as memory/
# above. Only factory.py may import its concrete journal adapters (below).
# `investment_committee.portfolio` followed the same path: forbidden while
# empty, allowed once the Portfolio Guardian landed (application/portfolio.py
# composes it, as docs/architecture.md describes); its concrete stores are
# built only in factory.py.
FORBIDDEN_MODULES = (
    "investment_committee.evaluation",
    "investment_committee.interfaces",
)

# The concrete DecisionLedger adapters - not the abstract DecisionLedger port or
# DecisionRecord/StrategyMetadata models, which application/ is allowed to depend
# on anywhere. These are checked by imported *name*, not module path: factory.py
# imports them via the public `from investment_committee.memory import
# JsonLinesDecisionLedger` re-export, not via investment_committee.memory.ledgers.jsonl
# directly, so a module-path-only check would miss the realistic import shape.
CONCRETE_LEDGER_ADAPTER_NAMES = (
    "JsonLinesDecisionLedger",
    "InMemoryDecisionLedger",
    "JsonLinesTradeJournal",
    "InMemoryTradeJournal",
    "JsonLinesAICommitteeLedger",
    "InMemoryAICommitteeLedger",
    "DeepSeekClient",
    "JsonLinesOpportunityScanLedger",
    "InMemoryOpportunityScanLedger",
    "JsonLinesTrackerLedger",
    "InMemoryTrackerLedger",
    "JsonLinesPositionStore",
    "InMemoryPositionStore",
    "JsonAlertStore",
    "InMemoryAlertStore",
    "SqliteOpportunityScanLedger",
    "SqliteTrackerLedger",
    "SqliteAlertStore",
)

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "investment_committee"

_CHECKED_FILES = [
    *sorted((_SRC_ROOT / "application").glob("*.py")),
    _SRC_ROOT / "cli.py",
    _SRC_ROOT / "__main__.py",
]

_NON_FACTORY_CHECKED_FILES = [path for path in _CHECKED_FILES if path.name != "factory.py"]


def _imported_module_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _imported_names(source: str) -> set[str]:
    """Every local name bound by an import statement - the alias if one is given,
    otherwise the imported name itself. Covers `from x import Y` and
    `from x import Y as Z` alike.
    """
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


@pytest.mark.parametrize("path", _CHECKED_FILES, ids=lambda p: p.name)
def test_no_forbidden_imports(path: Path) -> None:
    imported = _imported_module_names(path.read_text(encoding="utf-8"))
    for forbidden in FORBIDDEN_MODULES:
        offending = [
            module
            for module in imported
            if module == forbidden or module.startswith(forbidden + ".")
        ]
        assert not offending, f"{path.name} must not import {forbidden}, found {offending}"


@pytest.mark.parametrize("path", _NON_FACTORY_CHECKED_FILES, ids=lambda p: p.name)
def test_only_factory_imports_concrete_ledger_adapters(path: Path) -> None:
    imported = _imported_names(path.read_text(encoding="utf-8"))
    offending = imported & set(CONCRETE_LEDGER_ADAPTER_NAMES)
    assert not offending, (
        f"{path.name} must not import a concrete DecisionLedger adapter directly "
        f"({sorted(offending)}) - only application/factory.py may"
    )


def test_factory_is_permitted_to_import_concrete_ledger_adapters() -> None:
    factory_path = _SRC_ROOT / "application" / "factory.py"
    imported = _imported_names(factory_path.read_text(encoding="utf-8"))
    assert imported & set(CONCRETE_LEDGER_ADAPTER_NAMES), (
        "expected application/factory.py to import at least one concrete "
        "DecisionLedger adapter (that is the point of the composition root)"
    )
