"""Tests for memory.ai_ledger: append-only JSONL audit of AI analyses."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from investment_committee.committee.ai import AICommitteeCoordinator
from investment_committee.memory import JsonLinesDecisionLedger, LedgerWriteError
from investment_committee.memory.ai_ledger import InMemoryAICommitteeLedger, JsonLinesAICommitteeLedger

from tests.committee.ai._support import NOW, FakeLLM, packet


def _report(analysis_id: str = "ai-1"):  # type: ignore[no-untyped-def]
    return AICommitteeCoordinator(FakeLLM(), clock=lambda: NOW, id_factory=lambda: analysis_id).run(packet(), "MANTENER")


def test_jsonl_ledger_records_the_audit_fields(tmp_path: Path) -> None:
    ledger = JsonLinesAICommitteeLedger(tmp_path / "ai.jsonl")

    ledger.append(_report("ai-1"))
    ledger.append(_report("ai-2"))

    first, second = JsonLinesAICommitteeLedger(tmp_path / "ai.jsonl").records()
    assert (first["analysis_id"], second["analysis_id"]) == ("ai-1", "ai-2")
    for key in ("created_at", "committee_version", "provider", "model", "evidence", "agents", "decision", "controls", "validation_status", "usage", "calls"):
        assert key in first
    assert first["evidence"]["instrument"] == "EURUSD=X"
    assert first["evidence"]["items"][0]["source"] == "Yahoo Finance"
    assert first["usage"]["estimated_cost_usd"] == "0.004"
    assert first["decision"]["conclusion"] == "FAVORABLE"


def test_prompts_and_raw_replies_are_not_stored(tmp_path: Path) -> None:
    path = tmp_path / "ai.jsonl"
    JsonLinesAICommitteeLedger(path).append(_report())

    text = path.read_text(encoding="utf-8")
    assert "Reglas obligatorias" not in text  # system prompt
    assert "Responde ahora solo con el objeto JSON" not in text  # user prompt
    assert "reasoning" not in text and "api_key" not in text.lower()
    json.loads(text.splitlines()[0])


def test_duplicate_analysis_ids_are_rejected() -> None:
    ledger = InMemoryAICommitteeLedger()
    ledger.append(_report())

    with pytest.raises(LedgerWriteError):
        ledger.append(_report())


def test_the_decision_ledger_format_is_untouched(tmp_path: Path) -> None:
    decisions = tmp_path / "decisions.jsonl"
    JsonLinesAICommitteeLedger(tmp_path / "ai_committee.jsonl").append(_report())

    assert not decisions.exists()
    assert JsonLinesDecisionLedger(decisions).list_all() == ()
