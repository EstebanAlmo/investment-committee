"""Static check: application/ and the dev CLI must not import memory, portfolio,
simulation, evaluation, or interfaces. See docs/specs/spec-004-application-service.md,
"Testing plan" -> "Composition-root / architecture check".
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

FORBIDDEN_MODULES = (
    "investment_committee.memory",
    "investment_committee.portfolio",
    "investment_committee.simulation",
    "investment_committee.evaluation",
    "investment_committee.interfaces",
)

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "investment_committee"

_CHECKED_FILES = [
    *sorted((_SRC_ROOT / "application").glob("*.py")),
    _SRC_ROOT / "cli.py",
    _SRC_ROOT / "__main__.py",
]


def _imported_module_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
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
