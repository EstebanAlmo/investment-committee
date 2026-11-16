"""Static check: simulation/ and education/ stay below application/.

docs/architecture.md: portfolio/simulation/education depend on
market_intelligence and memory (and domain) only, and are composed by
application/. They must never reach up into application/, conversation/,
or interfaces/, nor into committee/indicators - and nothing in them may
import a broker or any network client (CLAUDE.md: no execution).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "investment_committee"

FORBIDDEN_MODULES = (
    "investment_committee.application",
    "investment_committee.conversation",
    "investment_committee.interfaces",
    "investment_committee.committee",
    "investment_committee.indicators",
    "investment_committee.integrations",
    "yfinance",
    "requests",
    "httpx",
    "telegram",
    "socket",
)

_CHECKED_FILES = sorted([*(_SRC_ROOT / "simulation").rglob("*.py"), *(_SRC_ROOT / "education").rglob("*.py")])


def _imported_module_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("path", _CHECKED_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_forbidden_imports(path: Path) -> None:
    imported = _imported_module_names(ast.parse(path.read_text(encoding="utf-8")))
    offending = [m for m in imported for f in FORBIDDEN_MODULES if m == f or m.startswith(f + ".")]
    assert not offending, f"{path} must not import {offending}"


def test_education_does_not_depend_on_simulation() -> None:
    for path in (_SRC_ROOT / "education").rglob("*.py"):
        imported = _imported_module_names(ast.parse(path.read_text(encoding="utf-8")))
        assert not [m for m in imported if m.startswith("investment_committee.simulation")], path
