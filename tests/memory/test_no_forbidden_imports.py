"""Static check: memory/ must not import application, the CLI, integrations, or any
other layer's concrete persistence adapters. See
docs/specs/spec-005-decision-ledger.md S6, "Dependency direction".
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

FORBIDDEN_MODULES = (
    "investment_committee.application",
    "investment_committee.portfolio",
    "investment_committee.simulation",
    "investment_committee.evaluation",
    "investment_committee.interfaces",
    "investment_committee.integrations",
    "investment_committee.market_intelligence",
    "investment_committee.cli",
)

ALLOWED_MODULES = (
    "investment_committee.domain",
    "investment_committee.indicators",
    "investment_committee.committee",
)

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "investment_committee"

_CHECKED_FILES = sorted((_SRC_ROOT / "memory").rglob("*.py"))


def _imported_module_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("path", _CHECKED_FILES, ids=lambda p: str(p.relative_to(_SRC_ROOT)))
def test_no_forbidden_imports(path: Path) -> None:
    imported = _imported_module_names(path.read_text(encoding="utf-8"))
    for forbidden in FORBIDDEN_MODULES:
        offending = [
            module for module in imported if module == forbidden or module.startswith(forbidden + ".")
        ]
        assert not offending, f"{path.name} must not import {forbidden}, found {offending}"


def test_memory_depends_on_domain_indicators_and_committee() -> None:
    """Positive check: memory/ is allowed to, and does, depend on these layers."""
    all_imported: set[str] = set()
    for path in _CHECKED_FILES:
        all_imported |= _imported_module_names(path.read_text(encoding="utf-8"))

    for allowed in ALLOWED_MODULES:
        matches = [module for module in all_imported if module == allowed or module.startswith(allowed + ".")]
        assert matches, f"expected memory/ to depend on {allowed}, found no such import"
