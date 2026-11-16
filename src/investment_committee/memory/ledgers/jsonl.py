"""JsonLinesDecisionLedger: the durable DecisionLedger adapter backed by one JSON Lines file.

One JSON object per line, one line per DecisionRecord. Standard-library
only. See docs/specs/spec-005-decision-ledger.md S15 for the exact
concurrency/durability guarantees this adapter makes, and does not make:
correct behavior is guaranteed only for one process running one long-lived
instance with one writer at a time - no file locking, no cross-process
atomicity.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Sequence

from investment_committee.domain import Asset

from ..errors import (
    DecisionNotFoundError,
    DecisionRecordDeserializationError,
    DuplicateDecisionError,
    LedgerWriteError,
)
from ..models import DecisionRecord, DecisionRecordId
from ..serialization import from_persisted_dict, to_persisted_dict
from .base import DecisionLedger

DEFAULT_LEDGER_PATH: Path = Path("data") / "ledger" / "decisions.jsonl"
"""Default location for the durable ledger file, relative to the process's
working directory - matches the existing data/raw/, data/processed/,
data/reports/ convention. Importing this module performs no I/O; the
constant is just a Path value.
"""


class JsonLinesDecisionLedger(DecisionLedger):
    """Durable DecisionLedger backed by a single append-only JSON Lines file."""

    def __init__(self, path: Path = DEFAULT_LEDGER_PATH) -> None:
        """Prepare the ledger at `path`, eagerly validating it if it exists.

        If `path` exists, every non-empty line is read and parsed
        immediately, in order, failing fast on malformed JSON, an
        unrecognized schema_version, a record_id that repeats one already
        seen earlier in the file, or a value that fails a domain
        dataclass's own __post_init__ validation once reconstructed. Any of
        these raises directly from __init__ - the object is never left
        partially constructed and usable.

        If `path` does not exist, construction succeeds immediately with an
        empty index. Neither `path`'s parent directories nor `path` itself
        are created at this point, and construction never writes to `path`
        or performs network I/O.
        """
        self._path = path
        self._records: dict[DecisionRecordId, DecisionRecord] = {}

        if not path.exists():
            return

        with path.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise DecisionRecordDeserializationError(
                        f"{path}: line {line_number}: malformed JSON"
                    ) from exc

                record = from_persisted_dict(data)

                if record.record_id in self._records:
                    raise DuplicateDecisionError(
                        f"{path}: line {line_number}: duplicate record_id {record.record_id}"
                    )

                self._records[record.record_id] = record

    def append(self, record: DecisionRecord) -> None:
        """Serialize `record` as one JSON line and append it to `path`.

        Rejects a duplicate record_id before any I/O. Creates missing
        parent directories and lazily creates the file on first append.
        The in-memory index is updated only after the write has succeeded -
        a failed write never leaves the index claiming the record exists.
        """
        if record.record_id in self._records:
            raise DuplicateDecisionError(f"record_id already exists: {record.record_id}")

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(to_persisted_dict(record))
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise LedgerWriteError(f"failed to append record to {self._path}: {exc}") from exc

        self._records[record.record_id] = record

    def get(self, record_id: DecisionRecordId) -> DecisionRecord:
        try:
            return self._records[record_id]
        except KeyError as exc:
            raise DecisionNotFoundError(f"no such record_id: {record_id}") from exc

    def find_by_asset(self, asset: Asset) -> Sequence[DecisionRecord]:
        return tuple(record for record in self._records.values() if record.asset == asset)

    def list_all(self) -> Sequence[DecisionRecord]:
        return tuple(self._records.values())
