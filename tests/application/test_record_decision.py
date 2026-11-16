"""Tests for investment_committee.application.record_decision.RecordDecisionUseCase."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from investment_committee.application.errors import InvalidTickerError
from investment_committee.application.models import AnalysisResult
from investment_committee.application.record_decision import (
    DecisionRecordingError,
    RecordDecisionResult,
    RecordDecisionUseCase,
)
from investment_committee.memory.errors import DuplicateDecisionError, LedgerWriteError
from investment_committee.memory.models import DecisionRecordId

from ._support import (
    FakeApplicationService,
    FakeDecisionLedger,
    make_asset,
    make_decision,
    make_indicator_snapshot,
    make_market_snapshot,
    make_strategy_metadata,
)

RECORDED_AT = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
PERSISTED_AT = datetime(2026, 7, 21, 12, 0, 5, tzinfo=timezone.utc)
FIXED_ID = DecisionRecordId("22222222-2222-4222-8222-222222222222")


def _make_analysis_result(asset=None) -> AnalysisResult:
    asset = asset if asset is not None else make_asset()
    market_snapshot = make_market_snapshot(asset)
    indicator_snapshot = make_indicator_snapshot(market_snapshot)
    decision = make_decision(asset)
    return AnalysisResult(
        market_snapshot=market_snapshot,
        indicator_snapshot=indicator_snapshot,
        decision=decision,
    )


def _use_case(
    ledger: FakeDecisionLedger,
    application_service: FakeApplicationService | None = None,
    clock=lambda: RECORDED_AT,
    id_factory=lambda: FIXED_ID,
) -> RecordDecisionUseCase:
    return RecordDecisionUseCase(
        application_service=application_service,
        ledger=ledger,
        strategy_metadata=make_strategy_metadata(),
        clock=clock,
        id_factory=id_factory,
    )


# --- record() -------------------------------------------------------------


def test_record_happy_path_returns_result_wrapping_constructed_record() -> None:
    analysis_result = _make_analysis_result()
    ledger = FakeDecisionLedger()
    application_service = FakeApplicationService(analysis_result)
    use_case = _use_case(ledger, application_service)

    result = use_case.record(analysis_result)

    assert isinstance(result, RecordDecisionResult)
    assert result.record.record_id == FIXED_ID
    assert result.record.recorded_at == RECORDED_AT
    assert result.record.market_snapshot is analysis_result.market_snapshot
    assert result.record.indicator_snapshot is analysis_result.indicator_snapshot
    assert result.record.decision is analysis_result.decision
    assert ledger.appended == [result.record]


def test_record_never_calls_the_application_service() -> None:
    analysis_result = _make_analysis_result()
    ledger = FakeDecisionLedger()
    application_service = FakeApplicationService(analysis_result)
    use_case = _use_case(ledger, application_service)

    use_case.record(analysis_result)

    assert application_service.calls == []


def test_record_translates_duplicate_decision_error() -> None:
    analysis_result = _make_analysis_result()
    original = DuplicateDecisionError("record_id already exists")
    ledger = FakeDecisionLedger(failure=original)
    use_case = _use_case(ledger)

    with pytest.raises(DecisionRecordingError) as exc_info:
        use_case.record(analysis_result)

    assert exc_info.value.analysis_result is analysis_result
    assert exc_info.value.__cause__ is original


def test_record_translates_ledger_write_error() -> None:
    analysis_result = _make_analysis_result()
    original = LedgerWriteError("disk full")
    ledger = FakeDecisionLedger(failure=original)
    use_case = _use_case(ledger)

    with pytest.raises(DecisionRecordingError) as exc_info:
        use_case.record(analysis_result)

    assert exc_info.value.analysis_result is analysis_result
    assert exc_info.value.__cause__ is original


def test_record_id_and_recorded_at_come_from_injected_factories_not_hardcoded() -> None:
    analysis_result = _make_analysis_result()
    ledger = FakeDecisionLedger()
    custom_id = DecisionRecordId("33333333-3333-4333-8333-333333333333")
    custom_time = datetime(2020, 1, 1, tzinfo=timezone.utc)
    use_case = _use_case(ledger, clock=lambda: custom_time, id_factory=lambda: custom_id)

    result = use_case.record(analysis_result)

    assert result.record.record_id == custom_id
    assert result.record.recorded_at == custom_time


def test_persisted_at_is_constructed_only_after_append_succeeds() -> None:
    analysis_result = _make_analysis_result()
    call_log: list[str] = []
    timestamps = iter([RECORDED_AT, PERSISTED_AT])

    def _clock() -> datetime:
        call_log.append("clock")
        return next(timestamps)

    class _LoggingLedger:
        def append(self, record: object) -> None:
            call_log.append("append")

    use_case = RecordDecisionUseCase(
        application_service=None,
        ledger=_LoggingLedger(),
        strategy_metadata=make_strategy_metadata(),
        clock=_clock,
        id_factory=lambda: FIXED_ID,
    )

    result = use_case.record(analysis_result)

    assert call_log == ["clock", "append", "clock"]
    assert result.record.recorded_at == RECORDED_AT
    assert result.persisted_at == PERSISTED_AT


def test_persisted_at_is_not_constructed_when_append_fails() -> None:
    analysis_result = _make_analysis_result()
    call_log: list[str] = []

    def _clock() -> datetime:
        call_log.append("clock")
        return RECORDED_AT

    class _FailingLedger:
        def append(self, record: object) -> None:
            call_log.append("append")
            raise LedgerWriteError("boom")

    use_case = RecordDecisionUseCase(
        application_service=None,
        ledger=_FailingLedger(),
        strategy_metadata=make_strategy_metadata(),
        clock=_clock,
        id_factory=lambda: FIXED_ID,
    )

    with pytest.raises(DecisionRecordingError):
        use_case.record(analysis_result)

    assert call_log == ["clock", "append"]  # no second clock() call for persisted_at


def test_strategy_metadata_is_attached_to_the_constructed_record() -> None:
    analysis_result = _make_analysis_result()
    ledger = FakeDecisionLedger()
    metadata = make_strategy_metadata(strategy_version="7")
    use_case = RecordDecisionUseCase(
        application_service=None,
        ledger=ledger,
        strategy_metadata=metadata,
        clock=lambda: RECORDED_AT,
        id_factory=lambda: FIXED_ID,
    )

    result = use_case.record(analysis_result)

    assert result.record.strategy_metadata is metadata


def test_unexpected_exception_from_ledger_propagates_unmodified() -> None:
    analysis_result = _make_analysis_result()

    class _BrokenLedger:
        def append(self, record: object) -> None:
            raise RuntimeError("unexpected ledger bug")

    use_case = RecordDecisionUseCase(
        application_service=None,
        ledger=_BrokenLedger(),
        strategy_metadata=make_strategy_metadata(),
        clock=lambda: RECORDED_AT,
        id_factory=lambda: FIXED_ID,
    )

    with pytest.raises(RuntimeError):
        use_case.record(analysis_result)


# --- analyze_and_record() --------------------------------------------------


def test_analyze_and_record_calls_analyze_asset_exactly_once_then_records() -> None:
    analysis_result = _make_analysis_result()
    application_service = FakeApplicationService(analysis_result)
    ledger = FakeDecisionLedger()
    use_case = _use_case(ledger, application_service)

    result = use_case.analyze_and_record("MSFT")

    assert application_service.calls == ["MSFT"]
    assert result.record.market_snapshot is analysis_result.market_snapshot
    assert result.record.decision is analysis_result.decision
    assert ledger.appended == [result.record]


def test_analyze_and_record_propagates_analysis_failure_and_never_appends() -> None:
    application_service = FakeApplicationService(InvalidTickerError("blank ticker"))
    ledger = FakeDecisionLedger()
    use_case = _use_case(ledger, application_service)

    with pytest.raises(InvalidTickerError):
        use_case.analyze_and_record("")

    assert ledger.appended == []


# --- RecordDecisionResult --------------------------------------------------


def test_record_decision_result_is_frozen_with_exactly_two_fields() -> None:
    field_names = {field.name for field in dataclasses.fields(RecordDecisionResult)}
    assert field_names == {"record", "persisted_at"}

    result = _use_case(FakeDecisionLedger()).record(_make_analysis_result())
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.persisted_at = PERSISTED_AT  # type: ignore[misc]


def test_record_decision_result_rejects_naive_persisted_at() -> None:
    record = _use_case(FakeDecisionLedger()).record(_make_analysis_result()).record

    with pytest.raises(ValueError):
        RecordDecisionResult(record=record, persisted_at=datetime(2026, 7, 21, 12, 0))
