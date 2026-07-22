"""JsonLinesDecisionLedger-specific behavior not covered by the conformance suite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from investment_committee.memory.errors import (
    DecisionNotFoundError,
    DecisionRecordDeserializationError,
    DuplicateDecisionError,
    LedgerWriteError,
)
from investment_committee.memory.ledgers.jsonl import DEFAULT_LEDGER_PATH, JsonLinesDecisionLedger
from investment_committee.memory.serialization import to_persisted_dict

from .._support import make_decision_record


def test_default_ledger_path() -> None:
    assert DEFAULT_LEDGER_PATH == Path("data") / "ledger" / "decisions.jsonl"


def test_constructing_against_a_missing_path_succeeds_and_creates_nothing(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "decisions.jsonl"

    ledger = JsonLinesDecisionLedger(path)

    assert ledger.list_all() == ()
    assert not path.exists()
    assert not path.parent.exists()


def test_file_and_parent_directory_created_only_on_first_successful_append(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "decisions.jsonl"
    ledger = JsonLinesDecisionLedger(path)
    assert not path.parent.exists()

    ledger.append(make_decision_record())

    assert path.exists()
    assert path.parent.exists()


def test_constructing_against_an_existing_valid_file_rebuilds_the_index(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    first_ledger = JsonLinesDecisionLedger(path)
    record = make_decision_record()
    first_ledger.append(record)

    second_ledger = JsonLinesDecisionLedger(path)

    assert second_ledger.get(record.record_id) == record
    with pytest.raises(DuplicateDecisionError):
        second_ledger.append(record)


@pytest.mark.parametrize("bad_line_position", ["only", "first", "last"])
def test_malformed_json_line_fails_construction(tmp_path: Path, bad_line_position: str) -> None:
    path = tmp_path / "decisions.jsonl"
    valid_line = json.dumps(to_persisted_dict(make_decision_record(record_id="valid-record")))
    malformed_line = "{not valid json"

    lines = {
        "only": [malformed_line],
        "first": [malformed_line, valid_line],
        "last": [valid_line, malformed_line],
    }[bad_line_position]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(DecisionRecordDeserializationError):
        JsonLinesDecisionLedger(path)


def test_duplicate_record_id_within_file_fails_construction(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    record = make_decision_record()
    line = json.dumps(to_persisted_dict(record))
    path.write_text(line + "\n" + line + "\n", encoding="utf-8")

    with pytest.raises(DuplicateDecisionError):
        JsonLinesDecisionLedger(path)


def test_unrecognized_schema_version_fails_construction(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    data = to_persisted_dict(make_decision_record())
    data["schema_version"] = 999
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")

    with pytest.raises(DecisionRecordDeserializationError):
        JsonLinesDecisionLedger(path)


def test_semantically_invalid_record_fails_construction(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    data = to_persisted_dict(make_decision_record())
    data["decision"]["confidence"] = "1.5"  # outside [0, 1]
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")

    with pytest.raises(DecisionRecordDeserializationError):
        JsonLinesDecisionLedger(path)


def test_blank_lines_are_skipped_during_construction(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    record = make_decision_record()
    line = json.dumps(to_persisted_dict(record))
    path.write_text(f"\n{line}\n\n   \n", encoding="utf-8")

    ledger = JsonLinesDecisionLedger(path)

    assert ledger.list_all() == (record,)


def test_construction_never_writes_to_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    record = make_decision_record()
    path.write_text(json.dumps(to_persisted_dict(record)) + "\n", encoding="utf-8")
    original_content = path.read_bytes()
    original_mtime = path.stat().st_mtime_ns

    JsonLinesDecisionLedger(path)

    assert path.read_bytes() == original_content
    assert path.stat().st_mtime_ns == original_mtime


def test_append_opens_the_file_in_append_mode_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "decisions.jsonl"
    ledger = JsonLinesDecisionLedger(path)
    record = make_decision_record()

    opened_modes: list[str] = []
    real_open = Path.open

    def _tracking_open(self: Path, mode: str = "r", *args: object, **kwargs: object) -> object:
        if self == path:
            opened_modes.append(mode)
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _tracking_open)

    ledger.append(record)

    assert opened_modes == ["a"]


def test_append_writes_exactly_one_line_per_call(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    ledger = JsonLinesDecisionLedger(path)

    ledger.append(make_decision_record(record_id="record-1"))
    ledger.append(make_decision_record(record_id="record-2"))

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        json.loads(line)  # each persisted line is valid, independently-parseable JSON


def test_duplicate_append_does_not_write_another_line(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    ledger = JsonLinesDecisionLedger(path)
    record = make_decision_record()
    ledger.append(record)

    with pytest.raises(DuplicateDecisionError):
        ledger.append(record)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_failed_append_does_not_mutate_in_memory_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "decisions.jsonl"
    ledger = JsonLinesDecisionLedger(path)
    record = make_decision_record()

    def _broken_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(Path, "mkdir", _broken_mkdir)

    with pytest.raises(LedgerWriteError):
        ledger.append(record)

    assert ledger.list_all() == ()
    with pytest.raises(DecisionNotFoundError):
        ledger.get(record.record_id)


def test_path_override_is_respected(tmp_path: Path) -> None:
    custom_path = tmp_path / "custom" / "my_decisions.jsonl"

    ledger = JsonLinesDecisionLedger(custom_path)
    ledger.append(make_decision_record())

    assert custom_path.exists()


def test_get_find_and_list_reflect_persisted_append_order(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    ledger = JsonLinesDecisionLedger(path)
    first = make_decision_record(record_id="record-1")
    second = make_decision_record(record_id="record-2")
    ledger.append(first)
    ledger.append(second)

    reloaded = JsonLinesDecisionLedger(path)

    assert reloaded.list_all() == (first, second)
    assert reloaded.get(first.record_id) == first
    assert reloaded.get(second.record_id) == second
