"""Tests for DecisionHistoryTool and ExplainDecisionTool. See spec-006 S15.2, S15.3, S26."""

from __future__ import annotations

from investment_committee.conversation.models import IntentType
from investment_committee.conversation.tools import (
    DecisionHistoryTool,
    ExplainDecisionTool,
    HelpContent,
    NotYetAvailable,
)
from investment_committee.memory import DecisionRecord, DecisionRecordId

from ._support import FakeDecisionQueryService, make_intent, make_record, make_session


def test_history_for_symbol_is_most_recent_first() -> None:
    old = make_record("AAPL", "old", days=0)
    new = make_record("AAPL", "new", days=3)
    other = make_record("MSFT", "msft", days=5)
    query = FakeDecisionQueryService([old, new, other])

    outcome = DecisionHistoryTool(query).execute(make_intent(IntentType.DECISION_HISTORY, symbol="aapl"), make_session())

    assert tuple(outcome) == (new, old)
    assert query.calls == [("list_by_asset", "AAPL")]


def test_history_respects_limit() -> None:
    records = [make_record("AAPL", f"r{i}", days=i) for i in range(5)]
    query = FakeDecisionQueryService(records)

    outcome = DecisionHistoryTool(query).execute(
        make_intent(IntentType.DECISION_HISTORY, symbol="AAPL", limit="2"), make_session()
    )

    assert [record.record_id for record in outcome] == ["r4", "r3"]


def test_history_without_symbol_lists_recent_with_default_limit() -> None:
    query = FakeDecisionQueryService([make_record("AAPL", "a"), make_record("MSFT", "m", days=1)])

    outcome = DecisionHistoryTool(query).execute(make_intent(IntentType.DECISION_HISTORY), make_session())

    assert len(outcome) == 2
    assert query.calls == [("list_recent", 10)]


def test_history_ignores_malformed_limit() -> None:
    query = FakeDecisionQueryService([make_record("AAPL", "a")])

    outcome = DecisionHistoryTool(query).execute(make_intent(IntentType.DECISION_HISTORY, limit="lots"), make_session())

    assert len(outcome) == 1


def test_history_empty_result_is_an_empty_sequence() -> None:
    outcome = DecisionHistoryTool(FakeDecisionQueryService()).execute(
        make_intent(IntentType.DECISION_HISTORY, symbol="AAPL"), make_session()
    )

    assert tuple(outcome) == ()


def test_explain_by_record_id() -> None:
    record = make_record("AAPL", "rec-9")
    query = FakeDecisionQueryService([record])

    outcome = ExplainDecisionTool(query).execute(make_intent(IntentType.EXPLAIN_DECISION, record_id="rec-9"), make_session())

    assert outcome is record


def test_explain_by_symbol_takes_most_recent() -> None:
    old = make_record("AAPL", "old", days=0)
    new = make_record("AAPL", "new", days=1)
    query = FakeDecisionQueryService([new, old])

    outcome = ExplainDecisionTool(query).execute(make_intent(IntentType.EXPLAIN_DECISION, symbol="AAPL"), make_session())

    assert outcome is new


def test_explain_by_symbol_with_no_history_suggests_analyzing_first() -> None:
    outcome = ExplainDecisionTool(FakeDecisionQueryService()).execute(
        make_intent(IntentType.EXPLAIN_DECISION, symbol="AAPL"), make_session()
    )

    assert isinstance(outcome, NotYetAvailable)
    assert "/analyze AAPL" in outcome.message


def test_explain_falls_back_to_session_last_record() -> None:
    record = make_record("TSLA", "last")
    session = make_session()
    session.last_record_id = DecisionRecordId("last")

    outcome = ExplainDecisionTool(FakeDecisionQueryService([record])).execute(
        make_intent(IntentType.EXPLAIN_DECISION), session
    )

    assert isinstance(outcome, DecisionRecord)
    assert outcome is record


def test_explain_with_no_target_asks_for_clarification() -> None:
    query = FakeDecisionQueryService()

    outcome = ExplainDecisionTool(query).execute(make_intent(IntentType.EXPLAIN_DECISION), make_session())

    assert isinstance(outcome, HelpContent)
    assert query.calls == []


def test_history_and_explain_for_forex_pair_query_the_canonical_symbol() -> None:
    record = make_record("EURUSD=X", "fx-1")
    query = FakeDecisionQueryService([record])

    history = DecisionHistoryTool(query).execute(make_intent(IntentType.DECISION_HISTORY, symbol="EUR/USD"), make_session())
    explained = ExplainDecisionTool(query).execute(make_intent(IntentType.EXPLAIN_DECISION, symbol="eur/usd"), make_session())

    assert tuple(history) == (record,)
    assert explained == record
    assert query.calls == [("list_by_asset", "EURUSD=X"), ("list_by_asset", "EURUSD=X")]
