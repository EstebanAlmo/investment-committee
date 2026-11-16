"""Tests for market_intelligence.provenance: raw + processed files, their
transformation parameters and SHA-256 hashes. Temporary directories only."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from investment_committee.market_intelligence.aggregation import aggregate
from investment_committee.market_intelligence.provenance import (
    DatasetError,
    bid_ask_bars_from_csv,
    bid_ask_bars_to_csv,
    quotes_from_csv,
    quotes_to_csv,
    read_dataset,
    sha256_file,
    write_dataset,
)
from investment_committee.market_intelligence.quality import audit_quotes
from investment_committee.market_intelligence.quotes import QuoteKind

from tests.market_intelligence._support import SOURCE, synthetic_quotes

HOUR = timedelta(hours=1)
RETRIEVED = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)
PARAMETERS = {"timeframe": "1h", "max_spread": "0.00030", "duplicates": "exact collapse, conflicting rejected"}


def _write(tmp_path: Path, name: str = "sintetico-eurusd"):  # type: ignore[no-untyped-def]
    raw = tmp_path / "incoming" / "EURUSD-2026-11.csv"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"time,bid,ask\r\n2026-11-02 00:00:00,1.10000,1.10010\r\n")  # the file exactly as it arrived
    audit = audit_quotes(synthetic_quotes(count=180), max_spread=Decimal("0.00030"))
    bars = aggregate(audit.accepted, HOUR)
    manifest = write_dataset(
        tmp_path / "datasets" / name, symbol="EURUSD", kind=QuoteKind.SYNTHETIC, source=SOURCE, acquired_how="generado en la prueba",
        retrieved_at=RETRIEVED, raw_files=[raw], quotes=audit.accepted, bars=bars, parameters=PARAMETERS, quality={"received": audit.received, **audit.counts},
    )
    return tmp_path / "datasets" / name, manifest, audit, bars, raw


def test_csv_round_trips_are_exact() -> None:
    quotes = synthetic_quotes(count=130)
    bars = aggregate(quotes, HOUR)

    assert quotes_from_csv(quotes_to_csv(quotes)) == quotes
    assert bid_ask_bars_from_csv(bid_ask_bars_to_csv(bars)) == bars
    assert quotes_to_csv(quotes).splitlines()[1] == "2026-11-02T00:00:00+00:00,EURUSD,1.10000,1.10010,SINTETICA,generador sintético de pruebas"


def test_a_dataset_keeps_raw_and_processed_files_with_their_hashes(tmp_path: Path) -> None:
    directory, manifest, audit, bars, raw = _write(tmp_path)

    assert (directory / "raw" / raw.name).read_bytes() == raw.read_bytes()  # byte for byte, CRLF included
    on_disk = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk == manifest
    assert manifest["kind"] == "SINTETICA" and manifest["symbol"] == "EURUSD" and manifest["source"] == SOURCE
    assert manifest["retrieved_at"] == "2026-11-11T15:00:00+00:00" and manifest["parameters"] == PARAMETERS
    assert manifest["raw"] == [{"file": f"raw/{raw.name}", "bytes": raw.stat().st_size, "sha256": sha256_file(raw)}]
    processed = {p["file"]: p for p in manifest["processed"]}
    assert set(processed) == {"processed/quotes.csv", "processed/bars.csv"}
    assert processed["processed/quotes.csv"]["rows"] == 180 and processed["processed/bars.csv"]["rows"] == 3
    assert all(p["sha256"] == sha256_file(directory / p["file"]) for p in processed.values())
    assert manifest["first_quote"] == "2026-11-02T00:00:00+00:00" and manifest["last_quote"] == "2026-11-02T02:59:00+00:00"
    assert len(manifest["dataset_sha256"]) == 64


def test_reading_verifies_every_hash(tmp_path: Path) -> None:
    directory, manifest, audit, bars, _ = _write(tmp_path)

    loaded_manifest, quotes, loaded_bars = read_dataset(directory)

    assert loaded_manifest == manifest and quotes == list(audit.accepted) and loaded_bars == bars

    (directory / "processed" / "quotes.csv").write_text((directory / "processed" / "quotes.csv").read_text(encoding="utf-8").replace("1.10000", "1.10001", 1),
                                                         encoding="utf-8", newline="")
    with pytest.raises(DatasetError, match="quotes.csv"):
        read_dataset(directory)


def test_a_changed_raw_file_is_detected(tmp_path: Path) -> None:
    directory, _, _, _, raw = _write(tmp_path)
    (directory / "raw" / raw.name).write_bytes(b"tampered")

    with pytest.raises(DatasetError, match="raw"):
        read_dataset(directory)


def test_the_same_inputs_give_the_same_dataset_hash(tmp_path: Path) -> None:
    _, first, _, _, _ = _write(tmp_path / "a")
    _, second, _, _, _ = _write(tmp_path / "b", name="otro-nombre")

    assert first["dataset_sha256"] == second["dataset_sha256"]


def test_an_existing_dataset_is_never_overwritten(tmp_path: Path) -> None:
    directory, _, audit, bars, raw = _write(tmp_path)

    with pytest.raises(DatasetError, match="ya existe"):
        write_dataset(directory, symbol="EURUSD", kind=QuoteKind.SYNTHETIC, source=SOURCE, acquired_how="x", retrieved_at=RETRIEVED,
                      raw_files=[raw], quotes=audit.accepted, bars=bars, parameters=PARAMETERS, quality={})


def test_the_declared_kind_must_match_the_data(tmp_path: Path) -> None:
    quotes = synthetic_quotes(count=5)

    with pytest.raises(DatasetError, match="clase"):
        write_dataset(tmp_path / "d", symbol="EURUSD", kind=QuoteKind.OBSERVED, source=SOURCE, acquired_how="x", retrieved_at=RETRIEVED,
                      raw_files=[], quotes=quotes, bars=aggregate(quotes, HOUR), parameters={}, quality={})
    assert not (tmp_path / "d").exists()
