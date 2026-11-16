"""TradeJournal: append-only record of simulated signals and their outcomes.

A signal is written once, when it is produced, and never edited. Each time
an open trade is re-evaluated and reaches a final status, a separate
outcome entry is appended that references the signal - the original signal
stays exactly as it was recorded (same principle as the Decision Ledger).

JsonLinesTradeJournal stores one JSON object per line under data/ledger/
(git-ignored). Nothing here talks to a broker.
"""

from __future__ import annotations

import dataclasses
import json
import types
import typing
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

from .models import SimulatedTrade, TradeOutcome, TradeSignal, TradeStatus

DEFAULT_TRADE_JOURNAL_PATH = Path("data") / "ledger" / "simulated_trades.jsonl"


class TradeJournalError(Exception):
    """Raised when the journal cannot be read or written."""


class TradeJournal(ABC):
    @abstractmethod
    def append_signal(self, signal: TradeSignal) -> None: ...

    @abstractmethod
    def append_outcome(self, outcome: TradeOutcome) -> None: ...

    @abstractmethod
    def signals(self) -> Sequence[TradeSignal]:
        """All recorded signals, in recording order."""

    @abstractmethod
    def final_outcomes(self) -> dict[str, TradeOutcome]:
        """The final (non-OPEN) outcome per signal id, where one exists."""

    def get_signal(self, signal_id: str) -> TradeSignal | None:
        return next((signal for signal in self.signals() if signal.signal_id == signal_id), None)

    def trades(self) -> Sequence[SimulatedTrade]:
        outcomes = self.final_outcomes()
        return tuple(SimulatedTrade(signal=s, outcome=outcomes.get(s.signal_id)) for s in self.signals())


class InMemoryTradeJournal(TradeJournal):
    def __init__(self) -> None:
        self._signals: list[TradeSignal] = []
        self._outcomes: dict[str, TradeOutcome] = {}

    def append_signal(self, signal: TradeSignal) -> None:
        if any(existing.signal_id == signal.signal_id for existing in self._signals):
            raise TradeJournalError(f"signal {signal.signal_id} is already recorded")
        self._signals.append(signal)

    def append_outcome(self, outcome: TradeOutcome) -> None:
        _check_outcome(outcome, self._outcomes, {s.signal_id for s in self._signals})
        self._outcomes[outcome.signal_id] = outcome

    def signals(self) -> Sequence[TradeSignal]:
        return tuple(self._signals)

    def final_outcomes(self) -> dict[str, TradeOutcome]:
        return dict(self._outcomes)


class JsonLinesTradeJournal(TradeJournal):
    """Reads the whole file once at construction, then appends. A process
    must share one instance between writers and readers."""

    def __init__(self, path: Path | str = DEFAULT_TRADE_JOURNAL_PATH) -> None:
        self._path = Path(path)
        self._memory = InMemoryTradeJournal()
        if self._path.exists():
            try:
                for line_number, line in enumerate(self._path.read_text(encoding="utf-8").splitlines(), start=1):
                    if line.strip():
                        self._load(json.loads(line), line_number)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise TradeJournalError(f"cannot read trade journal {self._path}: {exc}") from exc

    def _load(self, entry: dict[str, Any], line_number: int) -> None:
        kind = entry.get("type")
        if kind == "signal":
            self._memory.append_signal(_decode(TradeSignal, entry["data"]))
        elif kind == "outcome":
            self._memory.append_outcome(_decode(TradeOutcome, entry["data"]))
        else:
            raise ValueError(f"line {line_number}: unknown entry type {kind!r}")

    def append_signal(self, signal: TradeSignal) -> None:
        self._memory.append_signal(signal)
        self._write({"type": "signal", "data": _encode(signal)})

    def append_outcome(self, outcome: TradeOutcome) -> None:
        self._memory.append_outcome(outcome)
        self._write({"type": "outcome", "data": _encode(outcome)})

    def signals(self) -> Sequence[TradeSignal]:
        return self._memory.signals()

    def final_outcomes(self) -> dict[str, TradeOutcome]:
        return self._memory.final_outcomes()

    def _write(self, entry: dict[str, Any]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
        except OSError as exc:
            raise TradeJournalError(f"cannot write trade journal {self._path}: {exc}") from exc


def _check_outcome(outcome: TradeOutcome, existing: dict[str, TradeOutcome], known_ids: set[str]) -> None:
    if outcome.status is TradeStatus.OPEN:
        raise TradeJournalError("only final outcomes are recorded; OPEN is recomputed on demand")
    if outcome.signal_id not in known_ids:
        raise TradeJournalError(f"outcome references unknown signal {outcome.signal_id}")
    if outcome.signal_id in existing:
        raise TradeJournalError(f"signal {outcome.signal_id} already has a final outcome")


# --- Serialization: tagged JSON for Decimal/datetime/timedelta, type-driven
# decoding for enums and nested dataclasses. ---


def _encode(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _encode(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, timedelta):
        return {"$seconds": value.total_seconds()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    return value


def _decode(annotation: Any, raw: Any) -> Any:
    if isinstance(raw, dict) and len(raw) == 1:
        tag, payload = next(iter(raw.items()))
        if tag == "$decimal":
            return Decimal(payload)
        if tag == "$datetime":
            return datetime.fromisoformat(payload)
        if tag == "$seconds":
            return timedelta(seconds=payload)
    if raw is None:
        return None
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        options = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        return _decode(options[0], raw)
    if origin is not None and isinstance(raw, list):
        (item_type,) = typing.get_args(annotation)[:1]
        return tuple(_decode(item_type, item) for item in raw)
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return annotation(raw)
    if isinstance(annotation, type) and dataclasses.is_dataclass(annotation):
        hints = typing.get_type_hints(annotation)
        return annotation(**{f.name: _decode(hints[f.name], raw[f.name]) for f in dataclasses.fields(annotation) if f.name in raw})
    return raw
