"""Tests of market_intelligence.ingest on made-up files: immutable
originals, retries, strict reading, UTC conversion across daylight saving,
duplicates, gaps, incomplete bars, bid/ask checks, ticks to hourly bars,
integrity and reproducibility. No network and no market data."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from investment_committee.market_intelligence.aggregation import aggregate
from investment_committee.market_intelligence.ingest import (
    IngestError,
    audit_bid_ask,
    normalize,
    sha256_bytes,
    store_original,
    verify_ingest,
    with_retries,
    write_ingest,
)
from investment_committee.market_intelligence.providers.base import MarketDataError
from investment_committee.market_intelligence.providers.yfinance_provider import YFinanceProvider
from investment_committee.market_intelligence.snapshots import read_snapshot

from ._support import MONDAY, always_open, quote, weekdays_only

UTC = timezone.utc
HOUR = timedelta(hours=1)
D = Decimal
FETCHED = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
HEADER = "Datetime,Open,High,Low,Close,Volume"
PROVENANCE = {"provider": "proveedor de prueba", "official_url": "https://example.invalid", "resolution": "1h", "price_kind": "SINTETICO",
              "source_time_zone": "UTC", "terms": "datos inventados"}


def csv_of(*rows: str) -> str:
    return "\n".join([HEADER, *rows]) + "\n"


def row(label: str, o: str = "1.1000", h: str = "1.1010", low: str = "1.0990", c: str = "1.1005", volume: str = "0") -> str:
    return f"{label},{o},{h},{low},{c},{volume}"


def norm(text: str, **options):  # type: ignore[no-untyped-def]
    return normalize(text, **{**dict(timeframe=HOUR, calendar=always_open, fetched_at=FETCHED), **options})


def hours(count: int, start: datetime = MONDAY) -> list[str]:
    return [row((start + n * HOUR).isoformat(sep=" ")) for n in range(count)]


# --- originals ---


def test_an_original_is_written_once_and_never_overwritten(tmp_path) -> None:  # type: ignore[no-untyped-def]
    first = store_original(tmp_path / "original", "EURUSD_X.csv", b"abc")
    again = store_original(tmp_path / "original", "EURUSD_X.csv", b"abc")

    assert first == again == {"file": "EURUSD_X.csv", "sha256": sha256_bytes(b"abc"), "bytes": 3}
    with pytest.raises(IngestError, match="no se sobrescribe"):
        store_original(tmp_path / "original", "EURUSD_X.csv", b"abd")
    assert (tmp_path / "original" / "EURUSD_X.csv").read_bytes() == b"abc"


def test_a_failed_download_is_retried_with_pauses_and_the_last_failure_is_raised() -> None:
    calls, waits = [], []

    def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise MarketDataError("sin respuesta")
        return "datos"

    assert with_retries(flaky, attempts=3, delays=(2.0, 5.0), sleep=waits.append, retry_on=(MarketDataError,)) == "datos"
    assert (len(calls), waits) == (3, [2.0, 5.0])

    def broken() -> str:
        raise MarketDataError("caído")

    waits.clear()
    with pytest.raises(MarketDataError, match="caído"):
        with_retries(broken, attempts=2, sleep=waits.append, retry_on=(MarketDataError,))
    assert waits == [2.0]
    with pytest.raises(ValueError):  # an error that is not a download failure is not retried
        with_retries(lambda: int("x"), attempts=3, sleep=waits.append, retry_on=(MarketDataError,))
    assert waits == [2.0]


# --- strict reading ---


def test_rows_are_read_strictly_and_nothing_is_repaired() -> None:
    text = csv_of(
        row("2026-11-02 00:00:00+00:00"),
        row("2026-11-02 01:00:00+00:00", o="nan", h="nan", low="nan", c="nan"),  # no prices
        row("2026-11-02 02:00:00+00:00", o="abc"),  # unreadable number
        row("no es una fecha"),
        row("2026-11-02 04:00:00", o="1.1"),  # no UTC offset: no zone is assumed
        row("2026-11-02 05:00:00+00:00", o="0"),  # non-positive
        row("2026-11-02 06:00:00+00:00", o="inf"),
        row("2026-11-02 07:00:00+00:00"),
    )
    result = norm(text)

    assert [b.timestamp for b in result.bars] == [MONDAY, MONDAY + 7 * HOUR] and result.rows == 8
    assert result.problems == {"FILA_ILEGIBLE": 4, "FILA_SIN_PRECIO": 1, "PRECIO_INVALIDO": 1}
    assert result.bars[0] .open == D("1.1000") and result.bars[0].close == D("1.1005")  # decimals read from the text, not through a float


def test_a_file_without_price_columns_or_without_usable_bars_is_refused() -> None:
    with pytest.raises(IngestError, match="columnas"):
        norm("Datetime,Precio\n2026-11-02 00:00:00+00:00,1.1\n")
    with pytest.raises(IngestError, match="ninguna vela"):
        norm(csv_of(row("2026-11-02 00:00:00+00:00", o="nan", h="nan", low="nan", c="nan")))


def test_an_open_or_close_outside_the_range_widens_it_with_the_bars_own_prices_and_is_counted() -> None:
    result = norm(csv_of(row("2026-11-02 00:00:00+00:00", o="1.1000", h="1.1010", low="1.0990", c="1.1012"),
                         row("2026-11-02 01:00:00+00:00")))

    assert (result.bars[0].high, result.bars[0].low, result.bars[0].close) == (D("1.1012"), D("1.0990"), D("1.1012"))
    assert result.problems == {"OHLC_AMPLIADO": 1} and result.bars[1].high == D("1.1010")


# --- time ---


def test_labels_are_converted_to_utc_from_the_offset_they_carry() -> None:
    """London time across the change of 25 October 2026: 00:00+01:00 is 23:00 UTC of the day before."""
    result = norm(csv_of(row("2026-10-23 00:00:00+01:00"), row("2026-10-23 01:00:00+01:00"), row("2026-10-26 00:00:00+00:00"),
                         row("2026-10-26 01:00:00+00:00")))

    assert [b.timestamp for b in result.bars] == [datetime(2026, 10, 22, 23, tzinfo=UTC), datetime(2026, 10, 23, 0, tzinfo=UTC),
                                                  datetime(2026, 10, 26, 0, tzinfo=UTC), datetime(2026, 10, 26, 1, tzinfo=UTC)]
    assert all(b.timestamp.tzinfo is UTC for b in result.bars) and list(result.utc_offsets) == ["+00:00", "+01:00"]


def test_the_same_instant_written_with_two_offsets_is_one_bar_or_a_contradiction() -> None:
    same = norm(csv_of(row("2026-11-02 01:00:00+01:00"), row("2026-11-02 00:00:00+00:00"), row("2026-11-02 01:00:00+00:00")))
    different = norm(csv_of(row("2026-11-02 01:00:00+01:00"), row("2026-11-02 00:00:00+00:00", c="1.1001"), row("2026-11-02 01:00:00+00:00")))

    assert [b.timestamp for b in same.bars] == [MONDAY, MONDAY + HOUR] and same.problems == {"DUPLICADO": 1}
    assert [b.timestamp for b in different.bars] == [MONDAY + HOUR] and different.problems == {"DUPLICADO_CONTRADICTORIO": 2}  # neither is trusted


def test_a_bar_that_had_not_closed_when_the_data_was_fetched_is_not_kept() -> None:
    result = norm(csv_of(*hours(4, FETCHED - 3 * HOUR)))  # labels 09, 10, 11 and 12: fetched at 12:00

    assert [b.timestamp for b in result.bars] == [FETCHED - 3 * HOUR, FETCHED - 2 * HOUR, FETCHED - HOUR]
    assert result.problems == {"VELA_INCOMPLETA": 1}


def test_nothing_at_or_after_the_end_of_the_period_is_kept() -> None:
    result = norm(csv_of(*hours(5)), not_after=MONDAY + 3 * HOUR)

    assert [b.timestamp for b in result.bars] == [MONDAY, MONDAY + HOUR, MONDAY + 2 * HOUR] and result.problems == {"FUERA_DEL_PERIODO": 2}


def test_labels_off_the_hourly_grid_and_bars_outside_the_calendar_are_refused() -> None:
    saturday = MONDAY - 2 * 24 * HOUR
    result = norm(csv_of(row("2026-11-02 00:30:00+00:00"), row(saturday.isoformat(sep=" ")), *hours(2)), calendar=weekdays_only)

    assert [b.timestamp for b in result.bars] == [MONDAY, MONDAY + HOUR] and result.problems == {"FUERA_DE_REJILLA": 1, "MERCADO_CERRADO": 1}


def test_expected_hours_without_a_bar_are_reported_as_gaps_and_never_filled() -> None:
    rows = hours(10)
    result = norm(csv_of(*rows[:3], *rows[5:8], rows[9]))
    audit = result.audit

    assert len(result.bars) == 7 and (audit.expected, audit.present, str(audit.coverage)) == (10, 7, "0.7000")
    assert [(g.start, g.end, g.missing) for g in audit.gaps] == [(MONDAY + 3 * HOUR, MONDAY + 5 * HOUR, 2), (MONDAY + 8 * HOUR, MONDAY + 9 * HOUR, 1)]  # [start, end)
    assert MONDAY + 3 * HOUR not in {b.timestamp for b in result.bars}


def test_bars_come_out_in_time_order_whatever_the_order_of_the_file() -> None:
    rows = hours(6)

    assert norm(csv_of(*reversed(rows))).bars == norm(csv_of(*rows)).bars


# --- bid/ask and ticks ---


def bid_ask_bar(bid=("1.1000", "1.1010", "1.0990", "1.1005"), ask=("1.1001", "1.1011", "1.0991", "1.1006")):  # type: ignore[no-untyped-def]
    base = aggregate([quote(MONDAY, "1.1000", "1.1001"), quote(MONDAY + timedelta(minutes=5), "1.1005", "1.1006")], HOUR)[0]
    return replace(base, bid=tuple(D(v) for v in bid), ask=tuple(D(v) for v in ask))


def test_bid_ask_bars_are_checked_side_by_side() -> None:
    good = bid_ask_bar()
    crossed = bid_ask_bar(bid=("1.1002", "1.1010", "1.0990", "1.1005"))  # opens above the ask
    flat = bid_ask_bar(ask=("1.1000", "1.1011", "1.0991", "1.1006"))  # no spread at the open
    broken = bid_ask_bar(ask=("1.1001", "1.1003", "1.0991", "1.1006"))  # the ask closes above its own high
    negative = bid_ask_bar(bid=("0", "1.1010", "0", "1.1005"))

    assert audit_bid_ask([good]) == {}
    assert audit_bid_ask([good, crossed, flat, broken, negative]) == {"BID_MAYOR_QUE_ASK": 1, "OHLC_INCONSISTENTE": 1, "PRECIO_INVALIDO": 1, "SPREAD_CERO": 1}


def test_ticks_become_hourly_bars_labelled_by_their_open_with_the_first_and_last_quote() -> None:
    """A bar labelled L holds the quotes of [L, L + 1h): a quote at exactly
    L + 1h opens the next bar; an hour without quotes gives no bar."""
    ticks = [quote(MONDAY, "1.1000", "1.1002"), quote(MONDAY + timedelta(minutes=30), "1.1010", "1.1012"),
             quote(MONDAY + timedelta(minutes=59, seconds=59), "1.0995", "1.0998"), quote(MONDAY + HOUR, "1.1020", "1.1021"),
             quote(MONDAY + 3 * HOUR, "1.1030", "1.1032")]
    bars = aggregate(ticks, HOUR)

    assert [b.timestamp for b in bars] == [MONDAY, MONDAY + HOUR, MONDAY + 3 * HOUR]  # nothing for the silent hour
    assert bars[0].bid == (D("1.1000"), D("1.1010"), D("1.0995"), D("1.0995")) and bars[0].ask == (D("1.1002"), D("1.1012"), D("1.0998"), D("1.0998"))
    assert (bars[0].quotes, bars[0].first_quote, bars[0].last_quote) == (3, MONDAY, MONDAY + timedelta(minutes=59, seconds=59))
    assert (bars[0].spread_open, bars[0].spread_close, bars[0].spread_max) == (D("0.0002"), D("0.0003"), D("0.0003"))
    assert bars[1].bid[0] == D("1.1020") and audit_bid_ask(bars) == {}


# --- the dataset ---


def ingest(tmp_path, eur: str | None = None, name: str = "prueba"):  # type: ignore[no-untyped-def]
    originals = {"EURUSD=X": (eur or csv_of(*hours(30))).encode("utf-8"), "GBPUSD=X": csv_of(*hours(24)).encode("utf-8")}
    return tmp_path / name, write_ingest(tmp_path / name, originals, provenance=PROVENANCE, timeframe=HOUR, calendar=always_open, fetched_at=FETCHED,
                                         requested_from=MONDAY, not_after=MONDAY + 28 * HOUR)


def test_the_dataset_keeps_originals_normalized_bars_provenance_and_quality_apart(tmp_path) -> None:  # type: ignore[no-untyped-def]
    folder, record = ingest(tmp_path)

    assert sorted(p.name for p in folder.iterdir()) == ["calidad.json", "normalizado", "original", "procedencia.json"]
    assert sorted(p.name for p in (folder / "original").iterdir()) == ["EURUSD_X.csv", "GBPUSD_X.csv"]
    assert (folder / "original" / "EURUSD_X.csv").read_text(encoding="utf-8") == csv_of(*hours(30))  # byte for byte
    manifest, bars = read_snapshot(folder / "normalizado")
    assert (len(bars["EURUSD=X"]), len(bars["GBPUSD=X"])) == (28, 24) and manifest.combined_sha256 == record["dataset_sha256"]
    assert record == json.loads((folder / "procedencia.json").read_text(encoding="utf-8"))
    for field in ("provider", "official_url", "resolution", "price_kind", "source_time_zone", "terms"):
        assert record[field] == PROVENANCE[field]
    assert (record["downloaded_at"], record["requested_from"], record["requested_to_exclusive"], record["normalized_time_zone"]) == (
        "2026-11-12T12:00:00+00:00", "2026-11-02T00:00:00+00:00", "2026-11-03T04:00:00+00:00", "UTC")
    original = record["originals"]["EURUSD=X"]
    assert original == {"file": "EURUSD_X.csv", "sha256": sha256_bytes(csv_of(*hours(30)).encode()), "bytes": len(csv_of(*hours(30)).encode())}
    assert record["normalized"]["EURUSD=X"]["bars"] == 28 and len(record["normalized"]["EURUSD=X"]["sha256"]) == 64
    quality = json.loads((folder / "calidad.json").read_text(encoding="utf-8"))
    assert quality["EURUSD=X"]["problems"] == {"FUERA_DEL_PERIODO": 2} and quality["EURUSD=X"]["missing_bars"] == 0
    assert (quality["GBPUSD=X"]["rows_in_original"], quality["GBPUSD=X"]["bars_kept"], quality["GBPUSD=X"]["coverage"]) == (24, 24, "1.0000")
    assert not [p for p in folder.rglob("*") if p.is_file() and str(tmp_path).encode() in p.read_bytes()]  # no local path inside


def test_ingesting_the_same_originals_again_changes_nothing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    folder, first = ingest(tmp_path)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.rglob("*") if p.is_file()}
    _, second = ingest(tmp_path)

    assert second == first and {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.rglob("*") if p.is_file()} == before


def test_different_data_under_the_same_name_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    ingest(tmp_path)
    with pytest.raises(IngestError, match="no se sobrescribe"):
        ingest(tmp_path, eur=csv_of(*hours(29)))


def test_the_same_inputs_give_the_same_dataset_hash(tmp_path) -> None:  # type: ignore[no-untyped-def]
    one, two = ingest(tmp_path, name="uno")[1], ingest(tmp_path, name="dos")[1]

    assert one["dataset_sha256"] == two["dataset_sha256"] and one == two
    assert ingest(tmp_path, eur=csv_of(*hours(27)), name="tres")[1]["dataset_sha256"] != one["dataset_sha256"]


def test_verification_catches_a_changed_original_a_changed_normalized_file_and_a_missing_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    folder, record = ingest(tmp_path)
    options = dict(timeframe=HOUR, calendar=always_open)
    assert verify_ingest(folder, **options) == record  # type: ignore[arg-type]

    original = folder / "original" / "EURUSD_X.csv"
    kept = original.read_bytes()
    original.write_bytes(kept.replace(b"1.1005", b"1.1006", 1))
    with pytest.raises(IngestError, match="original"):
        verify_ingest(folder, **options)  # type: ignore[arg-type]
    original.write_bytes(kept)

    normalized = folder / "normalizado" / "EURUSD_X_1h.csv"
    good = normalized.read_bytes()
    normalized.write_bytes(good.replace(b"1.1005", b"1.1007", 1))
    with pytest.raises(IngestError, match="normalizado"):
        verify_ingest(folder, **options)  # type: ignore[arg-type]
    normalized.write_bytes(good)

    assert verify_ingest(folder, **options) == record  # type: ignore[arg-type]
    (folder / "original" / "GBPUSD_X.csv").unlink()
    with pytest.raises(IngestError, match="falta"):
        verify_ingest(folder, **options)  # type: ignore[arg-type]
    with pytest.raises(IngestError):
        verify_ingest(tmp_path / "no_existe", **options)  # type: ignore[arg-type]


# --- the provider's bounded request ---


@patch("investment_committee.market_intelligence.providers.yfinance_provider.yf.Ticker")
def test_a_date_range_is_requested_exactly_and_the_answer_is_returned_as_received(mock_ticker_cls: MagicMock) -> None:
    frame = pd.DataFrame({"Open": [1.1, 1.2], "High": [1.15, 1.25], "Low": [1.05, 1.15], "Close": [1.12, 1.22], "Volume": [0, 0]},
                         index=pd.to_datetime(["2026-03-20 10:00", "2026-03-20 11:00"], utc=True))
    frame.index.name = "Datetime"
    mock_ticker_cls.return_value.history.return_value = frame
    start, end = datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 3, 23, tzinfo=UTC)

    received, snapshot = YFinanceProvider(clock=lambda: FETCHED).get_intraday_range("eurusd=x", "1h", start, end)

    mock_ticker_cls.return_value.history.assert_called_once_with(start=start, end=end, interval="1h")  # nothing at or after `end` is asked for
    assert received.splitlines()[0] == "Datetime,Open,High,Low,Close,Volume" and "\r" not in received and len(snapshot.price_bars) == 2
    assert [b.close for b in norm(received).bars] == [D("1.12"), D("1.22")]
    with pytest.raises(MarketDataError):
        YFinanceProvider().get_intraday_range("EURUSD=X", "1h", end, start)
    with pytest.raises(MarketDataError):
        YFinanceProvider().get_intraday_range("EURUSD=X", "1h", datetime(2025, 1, 1), end)
