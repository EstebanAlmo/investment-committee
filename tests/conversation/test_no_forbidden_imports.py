"""Static check: conversation/ must not bypass the Application Layer.

See docs/specs/spec-006-conversational-interface.md S8, S23, S26, and
docs/plans/epic-8-conversational-interface-implementation-plan.md's ground
rules: "conversation/ code never imports memory, committee, indicators, or
market_intelligence directly, except type references."

Two checks, mirroring tests/application/test_no_forbidden_imports.py and
tests/memory/test_no_forbidden_imports.py's existing import-statement-scan
approach (S26: "approximated... as an import-statement scan, not a full
call-graph analysis"), but going one step further for the two modules
conversation/ is allowed a type-reference-only relationship with
(investment_committee.domain, investment_committee.memory): the import
statement itself is permitted, but no name imported from either module may
ever appear as the target of a call.

investment_committee.application is the one layer conversation/ calls for
behavior (S8: "conversation/ -> application/ for every behavioral call") -
it is allowed everywhere in conversation/ and asserted as the positive case
below. conversation/factory.py needs no exemption: the deterministic
composition root wires only application/ factories, never a concrete
integrations/ adapter.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

FORBIDDEN_MODULES = (
    "investment_committee.committee",
    "investment_committee.indicators",
    "investment_committee.market_intelligence",
    "investment_committee.interfaces",
    "investment_committee.integrations",
    "investment_committee.portfolio",
    "investment_committee.simulation",
    "investment_committee.evaluation",
)

# Legitimately imported for type references only (S8) - never called.
TYPE_REFERENCE_ONLY_MODULES = (
    "investment_committee.domain",
    "investment_committee.memory",
)

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "investment_committee"

_CHECKED_FILES = sorted((_SRC_ROOT / "conversation").rglob("*.py"))


def _imported_module_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _type_reference_only_imported_local_names(tree: ast.AST) -> set[str]:
    """Local names bound by `from <TYPE_REFERENCE_ONLY_MODULES member> import ...`."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in TYPE_REFERENCE_ONLY_MODULES:
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def _call_root_names(tree: ast.AST) -> set[str]:
    """The root Name of every call expression (`Foo(...)` -> "Foo"; `x.Foo(...)` -> "x")."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        while isinstance(func, ast.Attribute):
            func = func.value
        if isinstance(func, ast.Name):
            roots.add(func.id)
    return roots


@pytest.mark.parametrize("path", _CHECKED_FILES, ids=lambda p: str(p.relative_to(_SRC_ROOT)))
def test_no_forbidden_imports(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = _imported_module_names(tree)
    for forbidden in FORBIDDEN_MODULES:
        offending = [module for module in imported if module == forbidden or module.startswith(forbidden + ".")]
        assert not offending, f"{path.name} must not import {forbidden}, found {offending}"


@pytest.mark.parametrize("path", _CHECKED_FILES, ids=lambda p: str(p.relative_to(_SRC_ROOT)))
def test_domain_and_memory_imports_are_type_references_only(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    type_only_names = _type_reference_only_imported_local_names(tree)
    called_names = _call_root_names(tree)

    offending = type_only_names & called_names
    assert not offending, (
        f"{path.name} calls {sorted(offending)}, imported from a type-reference-only module "
        f"({TYPE_REFERENCE_ONLY_MODULES}) - conversation/ may reference these types in "
        "signatures but must never call into domain/ or memory/ directly"
    )


def test_conversation_depends_on_domain_and_memory_for_type_references() -> None:
    """Positive check: conversation/ is allowed to, and does, reference these layers' types."""
    all_imported: set[str] = set()
    for path in _CHECKED_FILES:
        all_imported |= _imported_module_names(ast.parse(path.read_text(encoding="utf-8")))

    for allowed in TYPE_REFERENCE_ONLY_MODULES:
        matches = [module for module in all_imported if module == allowed or module.startswith(allowed + ".")]
        assert matches, f"expected conversation/ to reference {allowed}, found no such import"


def test_conversation_depends_on_application_for_behavior() -> None:
    """Positive check (S26): the Application Layer is conversation/'s behavioral dependency."""
    all_imported: set[str] = set()
    for path in _CHECKED_FILES:
        all_imported |= _imported_module_names(ast.parse(path.read_text(encoding="utf-8")))

    assert any(
        module == "investment_committee.application" or module.startswith("investment_committee.application.")
        for module in all_imported
    )
