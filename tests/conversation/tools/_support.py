"""Shared fakes for conversation tool tests. Not collected by pytest (no test_ prefix)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Sequence

from investment_committee.application import (
    DecisionNotFoundInApplicationError,
    RecordDecisionResult,
)
from investment_committee.conversation.models import (
    ChannelType,
    ConversationIdentity,
    ConversationSession,
    Intent,
    IntentType,
)
from investment_committee.memory import DecisionRecord, DecisionRecordId

from tests.memory._support import UTC_NOW, make_asset, make_decision_record


def make_record(ticker: str = "AAPL", record_id: str = "rec-1", days: int = 0) -> DecisionRecord:
    return make_decision_record(
        asset=make_asset(ticker), record_id=record_id, recorded_at=UTC_NOW + timedelta(days=days)
    )


def make_session() -> ConversationSession:
    return ConversationSession(
        conversation_identity=ConversationIdentity(channel=ChannelType.TELEGRAM, channel_conversation_id="chat-1")
    )


def make_intent(intent_type: IntentType, **parameters: str) -> Intent:
    return Intent(type=intent_type, parameters=parameters, raw_text="test message")


class FakeRecordDecisionUseCase:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def analyze_and_record(self, symbol: str) -> RecordDecisionResult:
        self.calls.append(symbol)
        record = make_record(symbol, record_id=f"rec-{symbol}")
        return RecordDecisionResult(record=record, persisted_at=UTC_NOW)


class FakeDecisionQueryService:
    def __init__(self, records: Sequence[DecisionRecord] = ()) -> None:
        self._records = list(records)
        self.calls: list[tuple[str, object]] = []

    def get(self, record_id: DecisionRecordId) -> DecisionRecord:
        self.calls.append(("get", record_id))
        for record in self._records:
            if record.record_id == record_id:
                return record
        raise DecisionNotFoundInApplicationError(f"no such record_id: {record_id}")

    def list_by_asset(self, symbol: str) -> Sequence[DecisionRecord]:
        self.calls.append(("list_by_asset", symbol))
        return tuple(r for r in self._records if r.asset.ticker == symbol.strip().upper())

    def list_recent(self, limit: int) -> Sequence[DecisionRecord]:
        self.calls.append(("list_recent", limit))
        return tuple(sorted(self._records, key=lambda r: r.recorded_at, reverse=True))[:limit]


__all__ = [
    "UTC_NOW",
    "datetime",
    "make_record",
    "make_session",
    "make_intent",
    "FakeRecordDecisionUseCase",
    "FakeDecisionQueryService",
]
