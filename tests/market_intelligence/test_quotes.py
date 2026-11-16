"""Tests for market_intelligence.quotes: the normalized bid/ask quote."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from investment_committee.market_intelligence.quotes import Quote, QuoteKind, estimated_from_mid, to_utc

from tests.market_intelligence._support import MONDAY, SOURCE, quote

BOGOTA = timezone(timedelta(hours=-5))


def test_timestamps_are_normalized_to_utc() -> None:
    local = datetime(2026, 11, 2, 7, 30, tzinfo=BOGOTA)

    q = Quote("EURUSD", local, Decimal("1.1"), Decimal("1.1001"), QuoteKind.SYNTHETIC, SOURCE)

    assert q.timestamp == datetime(2026, 11, 2, 12, 30, tzinfo=timezone.utc) and q.timestamp.utcoffset() == timedelta(0)
    assert to_utc(datetime(2026, 3, 29, 1, 0, tzinfo=timezone(timedelta(hours=1)))).isoformat() == "2026-03-29T00:00:00+00:00"


def test_a_naive_timestamp_is_refused_not_guessed() -> None:
    with pytest.raises(ValueError, match="zona horaria"):
        to_utc(datetime(2026, 11, 2, 12, 0))
    with pytest.raises(ValueError):
        Quote("EURUSD", datetime(2026, 11, 2, 12, 0), Decimal("1.1"), Decimal("1.1001"), QuoteKind.OBSERVED, "x")


def test_mid_and_spread() -> None:
    q = quote(MONDAY, "1.10000", "1.10012")

    assert q.mid == Decimal("1.10006") and q.spread == Decimal("0.00012")


def test_a_quote_needs_a_symbol_a_source_and_decimal_prices() -> None:
    with pytest.raises(ValueError):
        Quote("", MONDAY, Decimal("1"), Decimal("1"), QuoteKind.OBSERVED, "x")
    with pytest.raises(ValueError):
        Quote("EURUSD", MONDAY, Decimal("1"), Decimal("1"), QuoteKind.OBSERVED, " ")
    with pytest.raises(TypeError):  # floats would bring binary rounding into prices
        Quote("EURUSD", MONDAY, 1.1, 1.1001, QuoteKind.OBSERVED, "x")  # type: ignore[arg-type]


def test_the_three_kinds_are_distinct_and_named() -> None:
    assert {k.value for k in QuoteKind} == {"OBSERVADA", "ESTIMADA", "SINTETICA"}


def test_a_quote_estimated_from_a_mid_price_says_so() -> None:
    q = estimated_from_mid("EURUSD", MONDAY, Decimal("1.10000"), Decimal("0.00010"), "Yahoo Finance · cierre H1")

    assert q.kind is QuoteKind.ESTIMATED
    assert (q.bid, q.ask, q.mid) == (Decimal("1.09995"), Decimal("1.10005"), Decimal("1.10000"))
    assert "Yahoo Finance · cierre H1" in q.source and "spread asumido 0.00010" in q.source
    with pytest.raises(ValueError):
        estimated_from_mid("EURUSD", MONDAY, Decimal("1.1"), Decimal("-0.0001"), "x")
