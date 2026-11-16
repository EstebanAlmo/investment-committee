"""AICommitteeLedger: append-only audit trail of AI committee analyses.

Separate from the Decision Ledger (data/ledger/decisions.jsonl), which keeps
its format unchanged: AI reports live in data/ledger/ai_committee.jsonl
(git-ignored), one JSON object per line with a schema version. What is
stored: the analysis id, instrument, timestamps, committee version,
provider and model, the full evidence packet (values, sources, timestamps,
sanitised external documents), every agent's structured result, the
controls applied, the final decision, the validation status, token usage
and estimated cost. What is never stored: API keys, the prompts sent, raw
model replies, or hidden reasoning.
"""

from __future__ import annotations

import dataclasses
import json
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

from investment_committee.committee.ai import AICommitteeReport

from .errors import LedgerReadError, LedgerWriteError

DEFAULT_AI_LEDGER_PATH = Path("data") / "ledger" / "ai_committee.jsonl"
AI_LEDGER_SCHEMA_VERSION = 1


class AICommitteeLedger(ABC):
    @abstractmethod
    def append(self, report: AICommitteeReport) -> None: ...

    @abstractmethod
    def records(self) -> Sequence[dict[str, Any]]:
        """Every stored analysis as plain JSON data, oldest first."""


class InMemoryAICommitteeLedger(AICommitteeLedger):
    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []

    def append(self, report: AICommitteeReport) -> None:
        record = to_audit_record(report)
        if any(existing["analysis_id"] == record["analysis_id"] for existing in self._records):
            raise LedgerWriteError(f"AI analysis {record['analysis_id']} is already recorded")
        self._records.append(record)

    def records(self) -> Sequence[dict[str, Any]]:
        return tuple(self._records)


class JsonLinesAICommitteeLedger(AICommitteeLedger):
    def __init__(self, path: Path | str = DEFAULT_AI_LEDGER_PATH) -> None:
        self._path = Path(path)

    def append(self, report: AICommitteeReport) -> None:
        line = json.dumps(to_audit_record(report), ensure_ascii=False, sort_keys=True)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            raise LedgerWriteError(f"cannot write AI committee ledger {self._path}: {exc}") from exc

    def records(self) -> Sequence[dict[str, Any]]:
        if not self._path.exists():
            return ()
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
            return tuple(json.loads(line) for line in lines if line.strip())
        except (OSError, ValueError) as exc:
            raise LedgerReadError(f"cannot read AI committee ledger {self._path}: {exc}") from exc


def to_audit_record(report: AICommitteeReport) -> dict[str, Any]:
    return {"schema_version": AI_LEDGER_SCHEMA_VERSION, **_plain(report)}


def _plain(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    return value
