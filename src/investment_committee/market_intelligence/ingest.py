"""Ingest of historical bars from an external source into a local,
verifiable dataset. Independent of any strategy: nothing here computes a
signal or a return.

    original/    what the provider answered, byte for byte. Written once:
                 storing the same bytes again is a no-op, different bytes
                 under the same name are refused. Never edited.
    normalizado/ what the project reads: one CSV per instrument in the
                 snapshot format (market_intelligence/snapshots.py), UTC,
                 one bar per hour, in time order. Rebuilt ONLY from
                 original/ by normalize(); verify_ingest() rebuilds it and
                 compares.
    procedencia.json  provider, official URL, terms as understood, when it
                 was downloaded, period, resolution, time zone, kind of
                 price, and for every file its SHA-256 and size.
    calidad.json  what the audit found: rows refused, duplicates, bars
                 outside the calendar, expected hours without a bar.

What normalize() does, and nothing else:
  - reads each row strictly; a row without prices is dropped and counted
    (FILA_SIN_PRECIO), a row that cannot be read is counted (FILA_ILEGIBLE)
    and a non-positive price is counted (PRECIO_INVALIDO): none is repaired;
  - converts every label to UTC from the offset the provider wrote (so a
    daylight-saving change in the provider's zone cannot shift an hour);
  - Yahoo takes high/low and open/close from different snapshots, so an
    open or close can sit slightly outside [low, high]; the range is widened
    to contain the bar's OWN open and close and the bar is counted
    (OHLC_AMPLIADO). No price is taken from another bar;
  - drops bars at or after `not_after` (FUERA_DEL_PERIODO) and bars that
    had not closed when the data was fetched (VELA_INCOMPLETA);
  - hands the rest to quality.audit_bars: exact duplicates collapse
    (DUPLICADO), contradictory ones are all refused
    (DUPLICADO_CONTRADICTORIO), labels off the hourly grid are refused
    (FUERA_DE_REJILLA), bars outside the calendar are dropped
    (MERCADO_CERRADO), expected hours without a bar are listed as gaps.
Nothing is interpolated, no missing bar is filled, no spread is invented.

Bid/ask sources are checked with audit_bid_ask (bid <= ask, both positive,
each side a consistent OHLC). Ticks become hourly bars with
aggregation.aggregate: a bar labelled L holds the quotes of [L, L + 1h);
open is the first quote, close the last, and an hour without quotes gives
no bar (tests/market_intelligence/test_aggregation.py, test_ingest.py).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TypeVar

from investment_committee.domain import PriceBar

from .aggregation import BidAskBar
from .quality import BarAudit, Calendar, audit_bars
from .snapshots import bars_to_csv, read_snapshot, write_snapshot

UTC = timezone.utc
ORIGINAL, NORMALIZED = "original", "normalizado"
T = TypeVar("T")


class IngestError(Exception):
    pass


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def store_original(folder: Path, name: str, content: bytes) -> dict[str, Any]:
    """Keep `content` as `folder/name`, once. The same bytes again change
    nothing; different bytes under that name are an error."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if path.exists():
        if path.read_bytes() != content:
            raise IngestError(f"{name} ya existe con otro contenido: un archivo original no se sobrescribe")
    else:
        path.write_bytes(content)
    return {"file": name, "sha256": sha256_bytes(content), "bytes": len(content)}


def with_retries(action: Callable[[], T], *, attempts: int = 3, delays: Sequence[float] = (2.0, 5.0), sleep: Callable[[float], None],
                 retry_on: tuple[type[BaseException], ...] = (Exception,)) -> T:
    """`action()`, retried after a pause when it raises one of `retry_on`;
    the last failure is raised. `sleep` is injected so tests do not wait."""
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except retry_on:
            if attempt == attempts:
                raise
            sleep(delays[min(attempt - 1, len(delays) - 1)])
    raise AssertionError("unreachable")


@dataclass(frozen=True)
class Normalized:
    bars: Sequence[PriceBar]  # trusted, UTC, in time order, one per hour
    rows: int  # rows the original holds
    problems: Mapping[str, int]  # every row or bar that was not kept as received, by reason
    utc_offsets: Sequence[str]  # the offsets the provider wrote its labels with, e.g. ("+00:00", "+01:00")
    audit: BarAudit


def _number(text: str) -> Decimal | None:
    if text.strip() == "" or text.strip().lower() == "nan":
        return None
    value = Decimal(text)
    if not value.is_finite():
        raise InvalidOperation("not finite")
    return value


def normalize(original: str, *, timeframe: timedelta, calendar: Calendar, fetched_at: datetime, not_after: datetime | None = None) -> Normalized:
    """The trusted hourly bars of one original CSV (first column: the label
    with its UTC offset; columns Open, High, Low, Close; Volume optional)."""
    reader = csv.DictReader(io.StringIO(original))
    if reader.fieldnames is None or not {"Open", "High", "Low", "Close"} <= set(reader.fieldnames):
        raise IngestError("el archivo original no tiene las columnas Open, High, Low, Close")
    label_column = reader.fieldnames[0]
    problems: Counter[str] = Counter()
    read: list[PriceBar] = []
    offsets: set[str] = set()
    rows = 0
    for row in reader:
        rows += 1
        try:
            label = datetime.fromisoformat(row[label_column])
            if label.tzinfo is None:
                raise ValueError("label without a UTC offset")
            prices = [_number(row[name]) for name in ("Open", "High", "Low", "Close")]
        except (ValueError, InvalidOperation, TypeError):
            problems["FILA_ILEGIBLE"] += 1
            continue
        if any(price is None for price in prices):
            problems["FILA_SIN_PRECIO"] += 1
            continue
        open_, high, low, close = prices  # type: ignore[misc]
        if min(open_, high, low, close) <= 0:
            problems["PRECIO_INVALIDO"] += 1
            continue
        if high < max(open_, close) or low > min(open_, close) or high < low:
            problems["OHLC_AMPLIADO"] += 1
            high, low = max(open_, high, low, close), min(open_, high, low, close)
        moment = label.astimezone(UTC)
        offsets.add(label.isoformat()[-6:])
        if not_after is not None and moment >= not_after:
            problems["FUERA_DEL_PERIODO"] += 1
            continue
        if moment + timeframe > fetched_at:
            problems["VELA_INCOMPLETA"] += 1
            continue
        volume = row.get("Volume", "") or "0"
        read.append(PriceBar(moment, open_, high, low, close, 0 if volume.strip().lower() == "nan" else int(float(volume))))
    if not read:
        raise IngestError("el archivo original no contiene ninguna vela utilizable")
    audit = audit_bars(read, timeframe, calendar)
    for code, count in audit.problems.items():
        problems[code] += count
    return Normalized(tuple(audit.bars), rows, dict(sorted(problems.items())), tuple(sorted(offsets)), audit)


def audit_bid_ask(bars: Sequence[BidAskBar]) -> dict[str, int]:
    """Problems of bid/ask bars, by reason. A source with real bid and ask
    must pass this before it is used; nothing is repaired."""
    problems: Counter[str] = Counter()
    for bar in bars:
        sides = (bar.bid, bar.ask)  # each (open, high, low, close)
        if any(min(side) <= 0 for side in sides):
            problems["PRECIO_INVALIDO"] += 1
        if any(side[1] < max(side[0], side[3]) or side[2] > min(side[0], side[3]) for side in sides):
            problems["OHLC_INCONSISTENTE"] += 1
        if bar.bid[0] > bar.ask[0] or bar.bid[3] > bar.ask[3]:
            problems["BID_MAYOR_QUE_ASK"] += 1
        if bar.bid[0] == bar.ask[0] or bar.bid[3] == bar.ask[3]:
            problems["SPREAD_CERO"] += 1
    return dict(sorted(problems.items()))


def quality_of(symbol: str, result: Normalized) -> dict[str, Any]:
    audit = result.audit
    return {
        "symbol": symbol, "rows_in_original": result.rows, "bars_kept": len(result.bars),
        "first_bar_utc": audit.first.isoformat() if audit.first else None, "last_bar_utc": audit.last.isoformat() if audit.last else None,
        "utc_offsets_in_labels": list(result.utc_offsets), "problems": dict(result.problems),
        "expected_open_market_bars": audit.expected, "present": audit.present, "coverage": str(audit.coverage),
        "missing_bars": audit.expected - audit.present, "gaps": len(audit.gaps),
        "largest_gaps": [{"start": g.start.isoformat(), "end": g.end.isoformat(), "missing": g.missing}
                         for g in sorted(audit.gaps, key=lambda g: (-g.missing, g.start))[:10]],
    }


def write_ingest(
    directory: Path, originals: Mapping[str, bytes], *, provenance: Mapping[str, Any], timeframe: timedelta, calendar: Calendar,
    fetched_at: datetime, requested_from: datetime, not_after: datetime,
) -> dict[str, Any]:
    """Store the originals (symbol -> bytes of its CSV), normalize them and
    write the dataset with its provenance and quality report. A second call
    with the same originals rewrites nothing and returns the same record."""
    if fetched_at.tzinfo is None:
        raise IngestError("fetched_at sin zona horaria")
    stored, series, quality = {}, {}, {}
    for symbol in sorted(originals):
        name = "".join(c if c.isalnum() else "_" for c in symbol) + ".csv"
        stored[symbol] = store_original(directory / ORIGINAL, name, originals[symbol])
        result = normalize(originals[symbol].decode("utf-8"), timeframe=timeframe, calendar=calendar, fetched_at=fetched_at, not_after=not_after)
        series[symbol], quality[symbol] = result.bars, quality_of(symbol, result)
    record_path = directory / "procedencia.json"
    if record_path.exists():  # already ingested: the originals above were checked to be the same bytes
        return verify_ingest(directory, timeframe=timeframe, calendar=calendar)
    manifest = write_snapshot(directory / NORMALIZED, series, source=str(provenance["provider"]), interval=str(provenance["resolution"]),
                              requested_from=requested_from, requested_to=not_after, downloaded_at=fetched_at)
    record = {
        **provenance, "downloaded_at": fetched_at.astimezone(UTC).isoformat(), "requested_from": requested_from.astimezone(UTC).isoformat(),
        "requested_to_exclusive": not_after.astimezone(UTC).isoformat(), "normalized_time_zone": "UTC", "originals": stored,
        "normalized": {s.symbol: {"file": s.file, "sha256": s.sha256, "bars": s.bars, "first_bar": s.first_bar, "last_bar": s.last_bar,
                                  "bytes": (directory / NORMALIZED / s.file).stat().st_size} for s in manifest.series},
        "dataset_sha256": manifest.combined_sha256,
    }
    for name, data in (("procedencia.json", record), ("calidad.json", quality)):
        (directory / name).write_bytes((json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    return record


def verify_ingest(directory: Path, *, timeframe: timedelta, calendar: Calendar) -> dict[str, Any]:
    """Check an ingested dataset end to end: every original against its
    recorded hash, every normalized file against its hash, and the
    normalized bars against a fresh normalization of the originals."""
    try:
        record = json.loads((directory / "procedencia.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise IngestError(f"no se puede leer procedencia.json: {error}") from error
    fetched_at, not_after = datetime.fromisoformat(record["downloaded_at"]), datetime.fromisoformat(record["requested_to_exclusive"])
    try:
        manifest, bars = read_snapshot(directory / NORMALIZED)  # checks each normalized file against its hash
    except Exception as error:
        raise IngestError(f"el dataset normalizado no coincide con sus hashes: {error}") from error
    if manifest.combined_sha256 != record["dataset_sha256"]:
        raise IngestError("el hash del dataset normalizado no es el registrado en la procedencia")
    for symbol, original in record["originals"].items():
        path = directory / ORIGINAL / original["file"]
        if not path.is_file() or sha256_bytes(path.read_bytes()) != original["sha256"]:
            raise IngestError(f"el original {original['file']} falta o fue modificado")
        again = normalize(path.read_text(encoding="utf-8"), timeframe=timeframe, calendar=calendar, fetched_at=fetched_at, not_after=not_after)
        if bars_to_csv(again.bars) != bars_to_csv(bars.get(symbol, [])):
            raise IngestError(f"{symbol}: el dataset normalizado no se reproduce a partir del original")
    return record
