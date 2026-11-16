"""Tests for market_intelligence.quality: quote and bar audits, market gaps.
Synthetic data only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from investment_committee.domain import PriceBar
from investment_committee.market_intelligence.quality import audit_bars, audit_quotes, missing_bars, quote_silences
from investment_committee.market_intelligence.quotes import QuoteKind

from tests.market_intelligence._support import MONDAY, always_open, quote, synthetic_quotes, weekdays_only

HOUR = timedelta(hours=1)
MINUTE = timedelta(minutes=1)
MAX_SPREAD = Decimal("0.00030")


def bar(at: datetime, close: str = "1.1000", wick: str = "0.0002") -> PriceBar:
    c, w = Decimal(close), Decimal(wick)
    return PriceBar(at, c, c + w, c - w, c, 0)


# --- quotes ---


def test_clean_quotes_pass_untouched_and_spreads_are_summarized() -> None:
    quotes = synthetic_quotes(count=10)

    audit = audit_quotes(quotes, max_spread=MAX_SPREAD)

    assert audit.received == 10 and list(audit.accepted) == quotes and audit.issues == () and audit.counts == {}
    assert (audit.symbol, audit.kind) == ("EURUSD", QuoteKind.SYNTHETIC)
    assert audit.spread_min == audit.spread_median == audit.spread_p95 == audit.spread_max == Decimal("0.00010")
    assert (audit.first, audit.last) == (quotes[0].timestamp, quotes[-1].timestamp)


def test_invalid_prices_and_crossed_quotes_are_rejected() -> None:
    quotes = [
        quote(MONDAY, "1.10000", "1.10010"),
        quote(MONDAY + MINUTE, "0", "1.10010"),
        quote(MONDAY + 2 * MINUTE, "1.10020", "-1"),
        quote(MONDAY + 3 * MINUTE, "1.10030", "1.10020"),  # bid above ask
        quote(MONDAY + 4 * MINUTE, "1.10000", "1.10000"),  # locked: bid == ask is allowed
    ]

    audit = audit_quotes(quotes, max_spread=MAX_SPREAD)

    assert [q.timestamp for q in audit.accepted] == [MONDAY, MONDAY + 4 * MINUTE]
    assert audit.counts == {"PRECIO_INVALIDO": 2, "BID_MAYOR_QUE_ASK": 1}
    assert [i.timestamp for i in audit.issues] == [MONDAY + MINUTE, MONDAY + 2 * MINUTE, MONDAY + 3 * MINUTE]


def test_a_wide_spread_is_flagged_but_kept() -> None:
    quotes = [quote(MONDAY, "1.10000", "1.10010"), quote(MONDAY + MINUTE, "1.10000", "1.10090")]

    audit = audit_quotes(quotes, max_spread=MAX_SPREAD)

    assert len(audit.accepted) == 2 and audit.counts == {"SPREAD_AMPLIO": 1}
    assert audit.spread_max == Decimal("0.00090")


def test_exact_duplicates_collapse_and_conflicting_ones_are_all_rejected() -> None:
    quotes = [
        quote(MONDAY, "1.10000", "1.10010"),
        quote(MONDAY, "1.10000", "1.10010"),  # the same quote twice
        quote(MONDAY + MINUTE, "1.10020", "1.10030"),
        quote(MONDAY + MINUTE, "1.10025", "1.10035"),  # same instant, another price: which is right is unknown
        quote(MONDAY + 2 * MINUTE, "1.10040", "1.10050"),
    ]

    audit = audit_quotes(quotes, max_spread=MAX_SPREAD)

    assert [q.timestamp for q in audit.accepted] == [MONDAY, MONDAY + 2 * MINUTE]
    assert audit.counts == {"DUPLICADO": 1, "DUPLICADO_CONTRADICTORIO": 2}


def test_out_of_order_input_is_counted_and_sorted() -> None:
    ordered = synthetic_quotes(count=4)

    audit = audit_quotes([ordered[2], ordered[0], ordered[3], ordered[1]], max_spread=MAX_SPREAD)

    assert list(audit.accepted) == ordered and audit.counts == {"FUERA_DE_ORDEN": 2}


def test_kinds_and_symbols_are_never_mixed() -> None:
    synthetic = synthetic_quotes(count=2)

    with pytest.raises(ValueError, match="clases"):
        audit_quotes([*synthetic, quote(MONDAY + HOUR, "1.1", "1.1001", kind=QuoteKind.OBSERVED)], max_spread=MAX_SPREAD)
    with pytest.raises(ValueError, match="instrumentos"):
        audit_quotes([*synthetic, quote(MONDAY + HOUR, "1.3", "1.3001", symbol="GBPUSD")], max_spread=MAX_SPREAD)
    with pytest.raises(ValueError):
        audit_quotes([], max_spread=MAX_SPREAD)


# --- gaps ---


def test_missing_bars_are_grouped_into_gaps() -> None:
    present = [MONDAY + i * HOUR for i in (0, 1, 2, 5, 6, 8)]

    gaps = missing_bars(present, HOUR, MONDAY, MONDAY + 10 * HOUR, always_open)

    assert [(g.start, g.end, g.missing) for g in gaps] == [
        (MONDAY + 3 * HOUR, MONDAY + 5 * HOUR, 2), (MONDAY + 7 * HOUR, MONDAY + 8 * HOUR, 1), (MONDAY + 9 * HOUR, MONDAY + 10 * HOUR, 1),
    ]


def test_a_closed_market_is_not_a_gap() -> None:
    friday = MONDAY + timedelta(days=4)
    present = [friday + i * HOUR for i in range(24)] + [friday + timedelta(days=3) + i * HOUR for i in range(3)]  # Friday, then Monday

    gaps = missing_bars(present, HOUR, friday, friday + timedelta(days=3, hours=3), weekdays_only)

    assert gaps == []
    assert missing_bars(present[:-1], HOUR, friday, friday + timedelta(days=3, hours=3), weekdays_only)[0].missing == 1


def test_quote_silences_count_only_open_market_time() -> None:
    friday_evening = MONDAY + timedelta(days=4, hours=23, minutes=59)
    quotes = [
        quote(MONDAY, "1.1", "1.1001"),
        quote(MONDAY + 2 * MINUTE, "1.1", "1.1001"),
        quote(MONDAY + 40 * MINUTE, "1.1", "1.1001"),  # 38 silent minutes with the market open
        quote(friday_evening, "1.1", "1.1001"),
        quote(friday_evening + timedelta(days=2, minutes=3), "1.1", "1.1001"),  # the weekend: 3 open minutes only
    ]

    silences = quote_silences(quotes, timedelta(minutes=5), weekdays_only)

    assert [(s.start, s.end) for s in silences] == [(MONDAY + 2 * MINUTE, MONDAY + 40 * MINUTE), (MONDAY + 40 * MINUTE, friday_evening)]
    assert silences[0].open_time == timedelta(minutes=38)


# --- bars ---


def test_a_complete_hourly_series_has_full_coverage() -> None:
    bars = [bar(MONDAY + i * HOUR) for i in range(48)]

    audit = audit_bars(bars, HOUR, always_open)

    assert (audit.received, audit.unique, audit.expected, audit.present) == (48, 48, 48, 48)
    assert audit.coverage == Decimal("1") and audit.gaps == () and audit.problems == {}
    assert audit.utc_offsets == ("+00:00",) and (audit.first, audit.last) == (MONDAY, MONDAY + 47 * HOUR)


def test_bar_timestamps_in_other_offsets_are_compared_as_instants() -> None:
    london_summer = timezone(timedelta(hours=1))
    same_instant = (MONDAY + HOUR).astimezone(london_summer)  # 02:00+01:00 is 01:00 UTC
    bars = [bar(MONDAY), bar(MONDAY + HOUR), bar(same_instant), bar(MONDAY + 2 * HOUR)]

    audit = audit_bars(bars, HOUR, always_open)

    assert audit.unique == 3 and audit.problems == {"DUPLICADO": 1}
    assert audit.utc_offsets == ("+00:00", "+01:00") and [b.timestamp for b in audit.bars] == [MONDAY, MONDAY + HOUR, MONDAY + 2 * HOUR]
    assert all(b.timestamp.utcoffset() == timedelta(0) for b in audit.bars)


def test_bar_problems_are_classified() -> None:
    flat = PriceBar(MONDAY + 3 * HOUR, Decimal("1.1"), Decimal("1.1"), Decimal("1.1"), Decimal("1.1"), 0)
    zero = PriceBar(MONDAY + 4 * HOUR, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), 0)
    saturday = MONDAY + timedelta(days=5)
    bars = [
        bar(MONDAY), bar(MONDAY + HOUR), bar(MONDAY + HOUR, close="1.2000"),  # same instant, another price
        bar(MONDAY + 2 * HOUR + timedelta(minutes=17)),  # off the hourly grid
        flat, zero, bar(saturday),
    ]

    audit = audit_bars(bars, HOUR, weekdays_only, start=MONDAY, end=MONDAY + 6 * HOUR)

    assert audit.problems == {"DUPLICADO_CONTRADICTORIO": 2, "FUERA_DE_REJILLA": 1, "VELA_PLANA": 1, "PRECIO_INVALIDO": 1, "MERCADO_CERRADO": 1}
    assert [b.timestamp for b in audit.bars] == [MONDAY, MONDAY + 3 * HOUR]  # only bars that can be trusted
    assert audit.expected == 6 and audit.present == 2 and audit.coverage == Decimal("0.3333")
    assert [(g.start, g.missing) for g in audit.gaps] == [(MONDAY + HOUR, 2), (MONDAY + 4 * HOUR, 2)]


def test_an_empty_series_is_refused() -> None:
    with pytest.raises(ValueError):
        audit_bars([], HOUR, always_open)
