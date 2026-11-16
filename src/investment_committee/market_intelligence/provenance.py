"""Provenance of a quote dataset: where it came from, what was done to it,
and SHA-256 hashes that prove neither the original nor the result changed.

A dataset is a directory (under data/raw/quote_datasets/ by default, which
is git-ignored - datasets are never committed):

    <dataset>/
      manifest.json           source, kind, how and when it was obtained,
                              transformation parameters, quality figures,
                              and the hash of every file below
      raw/<original files>    byte-for-byte copies of what was received
      processed/quotes.csv    audited quotes, UTC, one per row
      processed/bars.csv      bid/ask/mid bars built from them

`kind` (OBSERVADA / ESTIMADA / SINTETICA) is recorded in the manifest and in
every processed row, and must match the data. `dataset_sha256` combines the
file hashes, the kind, the symbol and the parameters - not the directory
name or the retrieval time - so the same inputs always give the same id.
A dataset is written once: an existing directory is never overwritten, and
reading verifies every hash and fails on any mismatch.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from .aggregation import BidAskBar
from .quotes import Quote, QuoteKind, to_utc

DEFAULT_DATASET_ROOT = Path("data") / "raw" / "quote_datasets"
FORMAT_VERSION = 1
_QUOTE_HEADER = ("timestamp", "symbol", "bid", "ask", "kind", "source")
_BAR_HEADER = (
    "timestamp", "symbol", "timeframe_seconds", "bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close",
    "mid_open", "mid_high", "mid_low", "mid_close", "quotes", "spread_open", "spread_close", "spread_max", "spread_mean", "first_quote", "last_quote", "kind",
)


class DatasetError(Exception):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _csv(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def _rows(text: str, header: Sequence[str]) -> list[list[str]]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or tuple(rows[0]) != tuple(header):
        raise DatasetError("unexpected CSV header")
    return rows[1:]


def quotes_to_csv(quotes: Sequence[Quote]) -> str:
    """Canonical text: UTC ISO timestamps, Decimals as written."""
    return _csv(_QUOTE_HEADER, [(q.timestamp.isoformat(), q.symbol, str(q.bid), str(q.ask), q.kind.value, q.source) for q in quotes])


def quotes_from_csv(text: str) -> list[Quote]:
    return [Quote(r[1], datetime.fromisoformat(r[0]), Decimal(r[2]), Decimal(r[3]), QuoteKind(r[4]), r[5]) for r in _rows(text, _QUOTE_HEADER)]


def bid_ask_bars_to_csv(bars: Sequence[BidAskBar]) -> str:
    return _csv(_BAR_HEADER, [
        (b.timestamp.isoformat(), b.symbol, int(b.timeframe.total_seconds()), *map(str, b.bid), *map(str, b.ask), *map(str, b.mid), b.quotes,
         str(b.spread_open), str(b.spread_close), str(b.spread_max), str(b.spread_mean), b.first_quote.isoformat(), b.last_quote.isoformat(), b.kind.value)
        for b in bars
    ])


def bid_ask_bars_from_csv(text: str) -> list[BidAskBar]:
    def ohlc(values: Sequence[str]) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        o, h, l, c = (Decimal(v) for v in values)
        return o, h, l, c

    return [
        BidAskBar(
            symbol=r[1], timestamp=datetime.fromisoformat(r[0]), timeframe=timedelta(seconds=int(r[2])), bid=ohlc(r[3:7]), ask=ohlc(r[7:11]),
            mid=ohlc(r[11:15]), quotes=int(r[15]), spread_open=Decimal(r[16]), spread_close=Decimal(r[17]), spread_max=Decimal(r[18]),
            spread_mean=Decimal(r[19]), first_quote=datetime.fromisoformat(r[20]), last_quote=datetime.fromisoformat(r[21]), kind=QuoteKind(r[22]),
        )
        for r in _rows(text, _BAR_HEADER)
    ]


def write_dataset(
    directory: Path, *, symbol: str, kind: QuoteKind, source: str, acquired_how: str, retrieved_at: datetime, raw_files: Sequence[Path],
    quotes: Sequence[Quote], bars: Sequence[BidAskBar], parameters: Mapping[str, Any], quality: Mapping[str, Any],
) -> dict[str, Any]:
    """Write the dataset and return its manifest. `parameters` are the
    transformation settings that produced `quotes` and `bars` from the raw
    files; `quality` the audit's figures."""
    directory = Path(directory)
    if directory.exists():
        raise DatasetError(f"{directory} ya existe: un dataset no se sobrescribe")
    if any(q.kind is not kind for q in quotes) or any(b.kind is not kind for b in bars):
        raise DatasetError(f"la clase declarada ({kind.value}) no coincide con la de los datos")
    if any(q.symbol != symbol for q in quotes) or any(b.symbol != symbol for b in bars):
        raise DatasetError(f"el instrumento declarado ({symbol}) no coincide con el de los datos")
    names = [Path(p).name for p in raw_files]
    if len(set(names)) != len(names):
        raise DatasetError("dos archivos originales tienen el mismo nombre")

    (directory / "raw").mkdir(parents=True)
    (directory / "processed").mkdir()
    raw = []
    for path in raw_files:
        target = directory / "raw" / Path(path).name
        shutil.copyfile(path, target)
        raw.append({"file": f"raw/{target.name}", "bytes": target.stat().st_size, "sha256": sha256_file(target)})
    processed = []
    for name, text, count in (("quotes.csv", quotes_to_csv(quotes), len(quotes)), ("bars.csv", bid_ask_bars_to_csv(bars), len(bars))):
        target = directory / "processed" / name
        target.write_text(text, encoding="utf-8", newline="")
        processed.append({"file": f"processed/{name}", "rows": count, "sha256": sha256_file(target)})

    parameters_json = json.dumps(parameters, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256()
    for line in [f"kind:{kind.value}", f"symbol:{symbol}", f"parameters:{parameters_json}",
                 *(f"{item['file']}:{item['sha256']}" for item in sorted([*raw, *processed], key=lambda i: i["file"]))]:
        digest.update(line.encode("utf-8") + b"\n")

    manifest = json.loads(json.dumps({
        "format_version": FORMAT_VERSION, "symbol": symbol, "kind": kind.value, "source": source, "acquired_how": acquired_how,
        "retrieved_at": to_utc(retrieved_at, "retrieved_at").isoformat(), "parameters": parameters, "quality": quality,
        "first_quote": quotes[0].timestamp.isoformat() if quotes else None, "last_quote": quotes[-1].timestamp.isoformat() if quotes else None,
        "raw": raw, "processed": processed, "dataset_sha256": digest.hexdigest(),
    }, ensure_ascii=False, default=str))
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return manifest


def read_dataset(directory: Path) -> tuple[dict[str, Any], list[Quote], list[BidAskBar]]:
    """(manifest, quotes, bars) after verifying the hash of every file."""
    directory = Path(directory)
    path = directory / "manifest.json"
    if not path.exists():
        raise DatasetError(f"no hay manifest.json en {directory}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for item in [*manifest["raw"], *manifest["processed"]]:
        target = directory / item["file"]
        if not target.exists() or sha256_file(target) != item["sha256"]:
            raise DatasetError(f"{item['file']}: el contenido no coincide con el hash del manifiesto")
    quotes = quotes_from_csv((directory / "processed" / "quotes.csv").read_text(encoding="utf-8"))
    bars = bid_ask_bars_from_csv((directory / "processed" / "bars.csv").read_text(encoding="utf-8"))
    return manifest, quotes, bars
