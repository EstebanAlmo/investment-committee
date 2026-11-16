"""Reproducible local copies of downloaded price bars.

A snapshot is one CSV per instrument plus a manifest (source, interval,
requested period, download time, first/last bar, bar count and the SHA-256
of each CSV). Re-running an evaluation from a snapshot uses exactly the same
bars; a hash mismatch on load is an error, never silently accepted.
Snapshots live under data/raw/ (git-ignored).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Mapping, Sequence

from investment_committee.domain import PriceBar

DEFAULT_SNAPSHOT_ROOT = Path("data") / "raw" / "snapshots"
_HEADER = ("timestamp", "open", "high", "low", "close", "volume")


class SnapshotError(Exception):
    pass


@dataclass(frozen=True)
class SeriesManifest:
    symbol: str
    file: str
    bars: int
    first_bar: str | None
    last_bar: str | None
    sha256: str


@dataclass(frozen=True)
class SnapshotManifest:
    source: str
    interval: str
    requested_from: str
    requested_to: str
    downloaded_at: str
    series: Sequence[SeriesManifest]

    @property
    def combined_sha256(self) -> str:
        digest = hashlib.sha256()
        for item in sorted(self.series, key=lambda s: s.symbol):
            digest.update(f"{item.symbol}:{item.sha256}\n".encode())
        return digest.hexdigest()


def bars_to_csv(bars: Sequence[PriceBar]) -> str:
    """Canonical text: UTC-offset ISO timestamps as the provider gave them,
    Decimal prices as strings, one row per bar in time order."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(_HEADER)
    for bar in sorted(bars, key=lambda b: b.timestamp):
        writer.writerow((bar.timestamp.isoformat(), str(bar.open), str(bar.high), str(bar.low), str(bar.close), bar.volume))
    return buffer.getvalue()


def bars_from_csv(text: str) -> list[PriceBar]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or tuple(rows[0]) != _HEADER:
        raise SnapshotError("unexpected snapshot header")
    return [
        PriceBar(datetime.fromisoformat(r[0]), Decimal(r[1]), Decimal(r[2]), Decimal(r[3]), Decimal(r[4]), int(r[5]))
        for r in rows[1:]
    ]


def write_snapshot(
    directory: Path, bars_by_symbol: Mapping[str, Sequence[PriceBar]], *, source: str, interval: str,
    requested_from: datetime, requested_to: datetime, downloaded_at: datetime,
) -> SnapshotManifest:
    directory.mkdir(parents=True, exist_ok=True)
    series = []
    for symbol in sorted(bars_by_symbol):
        bars = sorted(bars_by_symbol[symbol], key=lambda b: b.timestamp)
        text = bars_to_csv(bars)
        name = f"{symbol.replace('=', '_').replace('/', '_')}_{interval}.csv"
        (directory / name).write_text(text, encoding="utf-8", newline="")
        series.append(SeriesManifest(symbol, name, len(bars), bars[0].timestamp.isoformat() if bars else None,
                                     bars[-1].timestamp.isoformat() if bars else None, hashlib.sha256(text.encode("utf-8")).hexdigest()))
    manifest = SnapshotManifest(source, interval, requested_from.isoformat(), requested_to.isoformat(), downloaded_at.isoformat(), series)
    payload = {**{k: getattr(manifest, k) for k in ("source", "interval", "requested_from", "requested_to", "downloaded_at")},
               "series": [s.__dict__ for s in series], "combined_sha256": manifest.combined_sha256}
    (directory / "manifest.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    return manifest


def read_snapshot(directory: Path) -> tuple[SnapshotManifest, dict[str, list[PriceBar]]]:
    path = directory / "manifest.json"
    if not path.exists():
        raise SnapshotError(f"no snapshot manifest in {directory}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    series = [SeriesManifest(**s) for s in raw["series"]]
    manifest = SnapshotManifest(raw["source"], raw["interval"], raw["requested_from"], raw["requested_to"], raw["downloaded_at"], series)
    bars: dict[str, list[PriceBar]] = {}
    for item in series:
        text = (directory / item.file).read_text(encoding="utf-8")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != item.sha256:
            raise SnapshotError(f"{item.file}: content does not match the manifest hash")
        bars[item.symbol] = bars_from_csv(text)
    return manifest, bars
