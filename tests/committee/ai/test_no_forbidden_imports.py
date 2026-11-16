"""Static check: the AI committee and the LLM adapter keep their boundaries.

- committee/ai/ reaches the model only through integrations.llm (the
  LLMClient port): no HTTP client, no provider SDK, no application/,
  simulation/, conversation/, interfaces/, or market data provider imports.
- integrations/llm/ is the only package that imports httpx for LLM calls,
  and it imports nothing from the business layers.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[3] / "src" / "investment_committee"

AI_FORBIDDEN = (
    "httpx", "requests", "openai", "anthropic", "yfinance",
    "investment_committee.application", "investment_committee.simulation", "investment_committee.conversation",
    "investment_committee.interfaces", "investment_committee.market_intelligence.providers",
    "investment_committee.integrations.llm.deepseek",
)
LLM_FORBIDDEN = (
    "investment_committee.application", "investment_committee.committee", "investment_committee.conversation",
    "investment_committee.interfaces", "investment_committee.simulation", "investment_committee.memory",
)


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _offending(path: Path, forbidden: tuple[str, ...]) -> list[str]:
    return [m for m in _imports(path) for f in forbidden if m == f or m.startswith(f + ".")]


@pytest.mark.parametrize("path", sorted((_SRC / "committee" / "ai").rglob("*.py")), ids=lambda p: p.name)
def test_ai_committee_imports(path: Path) -> None:
    assert _offending(path, AI_FORBIDDEN) == []


@pytest.mark.parametrize("path", sorted((_SRC / "integrations" / "llm").rglob("*.py")), ids=lambda p: p.name)
def test_llm_adapter_imports(path: Path) -> None:
    assert _offending(path, LLM_FORBIDDEN) == []


def test_only_the_llm_adapter_and_telegram_stack_import_httpx() -> None:
    importers = [
        path.relative_to(_SRC).as_posix()
        for path in _SRC.rglob("*.py")
        if any(m == "httpx" or m.startswith("httpx.") for m in _imports(path))
    ]
    assert importers == ["integrations/llm/deepseek.py"]


def test_deterministic_committee_does_not_depend_on_the_ai_committee() -> None:
    for path in (_SRC / "committee").glob("*.py"):
        assert not [m for m in _imports(path) if m.startswith("investment_committee.committee.ai") or m == ".ai"], path
    for path in (_SRC / "committee" / "analysts").glob("*.py"):
        assert not [m for m in _imports(path) if "ai" in m.split(".")], path
