"""OpportunityScanLedger: append-only audit trail of opportunity scans.

Lives next to the simulated-trade journal. data/ledger/opportunity_scans.jsonl
(git-ignored), one JSON object per scan with a schema version. Stored: the
scan id, UTC timestamps, universe, data source and every instrument's data
check (bars received, future bars dropped, forming bar excluded, last
closed bar), strategy name/version, the full signal and its plan, the
controls and their results, the score breakdown, the historical-validation
status, costs in USD, partial errors, and any AI review id. Never stored:
API keys or other secrets (none reach this module).

Each candidate also gets an `evaluation` block - status PENDIENTE,
hypothetical, evaluable only with bars closing after its signal - so a
later job can score what happened (hypothetical_outcome) without
look-ahead and without counting an unexecuted signal as real P&L.
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

from .opportunities import OpportunityScan

DEFAULT_OPPORTUNITY_LEDGER_PATH = Path("data") / "ledger" / "opportunity_scans.jsonl"
OPPORTUNITY_LEDGER_SCHEMA_VERSION = 1


class OpportunityLedgerError(Exception):
    """The scan ledger could not be read or written."""


class OpportunityScanLedger(ABC):
    @abstractmethod
    def append(self, scan: OpportunityScan) -> None: ...

    @abstractmethod
    def records(self) -> Sequence[dict[str, Any]]:
        """Every stored scan as plain JSON data, oldest first."""


class InMemoryOpportunityScanLedger(OpportunityScanLedger):
    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []

    def append(self, scan: OpportunityScan) -> None:
        record = to_audit_record(scan)
        if any(existing["scan_id"] == record["scan_id"] for existing in self._records):
            raise OpportunityLedgerError(f"opportunity scan {record['scan_id']} is already recorded")
        self._records.append(record)

    def records(self) -> Sequence[dict[str, Any]]:
        return tuple(self._records)


class JsonLinesOpportunityScanLedger(OpportunityScanLedger):
    def __init__(self, path: Path | str = DEFAULT_OPPORTUNITY_LEDGER_PATH) -> None:
        self._path = Path(path)

    def append(self, scan: OpportunityScan) -> None:
        line = json.dumps(to_audit_record(scan), ensure_ascii=False, sort_keys=True)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            raise OpportunityLedgerError(f"cannot write opportunity ledger {self._path}: {exc}") from exc

    def records(self) -> Sequence[dict[str, Any]]:
        if not self._path.exists():
            return ()
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
            return tuple(json.loads(line) for line in lines if line.strip())
        except (OSError, ValueError) as exc:
            raise OpportunityLedgerError(f"cannot read opportunity ledger {self._path}: {exc}") from exc


def to_audit_record(scan: OpportunityScan) -> dict[str, Any]:
    record: dict[str, Any] = {"schema_version": OPPORTUNITY_LEDGER_SCHEMA_VERSION, **_plain(scan)}
    for raw, instrument in zip(record["instruments"], scan.instruments):
        signal = instrument.signal
        raw["evaluation"] = (
            {
                "status": "PENDIENTE",
                "hypothetical": True,  # never a simulated or real trade: not P&L
                "evaluate_only_with_bars_closing_after": signal.data_as_of.isoformat() if signal and signal.data_as_of else None,
                "outcome": None,
            }
            if instrument.is_candidate
            else None
        )
    latest = [i.data.last_closed_bar_close for i in scan.instruments if i.data and i.data.last_closed_bar_close]
    record["data_integrity"] = {
        "latest_bar_close_used": max(latest).isoformat() if latest else None,
        "no_bar_after_scan_start": all(moment <= scan.started_at for moment in latest),
    }
    return record


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
