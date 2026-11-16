"""Human review notes on simulated trades, kept apart from the study.

A note is a reviewer's opinion about one trade as seen in one run. It is
qualitative: nothing in simulation/ reads this file, and a note can never
change a trade, a signal or a parameter.

Key: study hash + trade id + scenario + period. The same signal seen under
another scenario or period is another trade (other prices, other size), so
its notes are not mixed.

Storage: one JSON object per line, appended. Nothing is ever rewritten or
deleted: saving again adds a line, the current note is the last one for its
key and the earlier ones are its history. Standard library only.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

RATINGS = ("Buena entrada", "Mala entrada", "Discutible", "Pendiente")


@dataclass(frozen=True)
class NoteKey:
    study_sha256: str
    trade_id: str
    scenario: str
    period: str


@dataclass(frozen=True)
class Note:
    study_sha256: str
    trade_id: str
    scenario: str
    period: str
    rating: str
    comment: str
    problem: str
    improvement: str
    reviewer: str
    reviewed_at: str  # ISO 8601, UTC

    @property
    def key(self) -> NoteKey:
        return NoteKey(self.study_sha256, self.trade_id, self.scenario, self.period)


class NoteStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def add(self, key: NoteKey, *, rating: str, comment: str = "", problem: str = "", improvement: str = "", reviewer: str = "",
            reviewed_at: datetime | None = None) -> Note:
        if rating not in RATINGS:
            raise ValueError(f"la calificación debe ser una de {RATINGS}")
        moment = reviewed_at or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            raise ValueError("reviewed_at sin zona horaria")
        note = Note(**asdict(key), rating=rating, comment=comment.strip(), problem=problem.strip(), improvement=improvement.strip(),
                    reviewer=reviewer.strip(), reviewed_at=moment.astimezone(timezone.utc).isoformat(timespec="seconds"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as handle:  # append only
            handle.write((json.dumps(asdict(note), ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
        return note

    def all(self) -> list[Note]:
        """Every note ever saved, oldest first."""
        if not self.path.exists():
            return []
        return [Note(**json.loads(line)) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def history(self, key: NoteKey) -> list[Note]:
        return [note for note in self.all() if note.key == key]

    def current(self, key: NoteKey) -> Note | None:
        history = self.history(key)
        return history[-1] if history else None

    def current_by_trade(self, study_sha256: str, scenario: str, period: str) -> dict[str, Note]:
        """The current note of each reviewed trade of one run, by trade id."""
        out: dict[str, Note] = {}
        for note in self.all():
            if (note.study_sha256, note.scenario, note.period) == (study_sha256, scenario, period):
                out[note.trade_id] = note
        return out
