"""Static check: the Telegram channel adapter depends on conversation/ only.

See docs/specs/spec-006-conversational-interface.md S8: a channel adapter
never imports application/, memory/, or any lower layer directly.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

FORBIDDEN_MODULES = (
    "investment_committee.application",
    "investment_committee.memory",
    "investment_committee.committee",
    "investment_committee.indicators",
    "investment_committee.market_intelligence",
    "investment_committee.integrations",
    "investment_committee.portfolio",
    "investment_committee.simulation",
    "investment_committee.evaluation",
)

_SRC_ROOT = Path(__file__).resolve().parents[3] / "src" / "investment_committee"
_CHECKED_FILES = sorted((_SRC_ROOT / "interfaces" / "telegram").rglob("*.py"))


def _imported_module_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_files_are_checked() -> None:
    assert any(path.name == "adapter.py" for path in _CHECKED_FILES)


@pytest.mark.parametrize("path", _CHECKED_FILES, ids=lambda p: p.name)
def test_no_forbidden_imports(path: Path) -> None:
    imported = _imported_module_names(ast.parse(path.read_text(encoding="utf-8")))
    for forbidden in FORBIDDEN_MODULES:
        offending = [m for m in imported if m == forbidden or m.startswith(forbidden + ".")]
        assert not offending, f"{path.name} must not import {forbidden}, found {offending}"
