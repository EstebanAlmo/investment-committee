"""Tests for conversation.factory.build_default_conversation_service. See spec-006 S25."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from investment_committee.application import DecisionQueryService
from investment_committee.conversation import (
    ChannelType,
    ConversationIdentity,
    ConversationMessage,
    UserIdentity,
    build_default_conversation_service,
)
from investment_committee.memory import InMemoryDecisionLedger

from .tools._support import FakeDecisionQueryService, FakeRecordDecisionUseCase, make_record


def _message(text: str) -> ConversationMessage:
    return ConversationMessage(
        conversation_identity=ConversationIdentity(ChannelType.TELEGRAM, "1"),
        user_identity=UserIdentity(user_id="42", display_name=None),
        text=text,
        audio=None,
        received_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )


def test_default_build_performs_no_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    service = build_default_conversation_service(allowed_user_ids=frozenset({"42"}))

    assert "/analyze" in service.handle_message(_message("/help")).text
    assert list(tmp_path.iterdir()) == []


def test_overrides_must_be_passed_together() -> None:
    with pytest.raises(ValueError, match="share one ledger"):
        build_default_conversation_service(frozenset({"42"}), record_decision_use_case=FakeRecordDecisionUseCase())  # type: ignore[arg-type]


def test_wires_every_tool_to_the_injected_collaborators() -> None:
    use_case = FakeRecordDecisionUseCase()
    ledger = InMemoryDecisionLedger()
    ledger.append(make_record("MSFT", "m1"))
    service = build_default_conversation_service(
        frozenset({"42"}), record_decision_use_case=use_case, decision_query_service=DecisionQueryService(ledger)  # type: ignore[arg-type]
    )

    assert service.handle_message(_message("/analyze AAPL")).text.startswith("Análisis del comité: AAPL")
    assert service.handle_message(_message("/compare MSFT TSLA")).text.startswith("Comparación")
    assert "MSFT: " in service.handle_message(_message("/history MSFT")).text
    assert service.handle_message(_message("/explain MSFT")).text.startswith("Análisis del comité: MSFT")
    assert "todavía no está disponible" in service.handle_message(_message("/portfolio")).text
    assert "todavía no está disponible" in service.handle_message(_message("/performance")).text
    assert "No ejecuto operaciones" in service.handle_message(_message("Sell Tesla")).text
    assert use_case.calls == ["AAPL", "MSFT", "TSLA"]


def test_unused_fake_query_service_is_accepted() -> None:
    service = build_default_conversation_service(
        frozenset({"42"}),
        record_decision_use_case=FakeRecordDecisionUseCase(),  # type: ignore[arg-type]
        decision_query_service=FakeDecisionQueryService(),  # type: ignore[arg-type]
    )

    assert "Todavía no hay decisiones registradas" in service.handle_message(_message("/history")).text
