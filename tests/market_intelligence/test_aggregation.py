"""Tests for market_intelligence.aggregation (quotes -> H1 bid/ask bars) and
market_intelligence.comparison (two bar series, side by side)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from investment_committee.domain import PriceBar
from investment_committee.market_intelligence.aggregation import aggregate, to_price_bars
from investment_committee.market_intelligence.comparison import compare_bars
from investment_committee.market_intelligence.quotes import QuoteKind

from tests.market_intelligence._support import MONDAY, quote, synthetic_quotes

HOUR = timedelta(hours=1)
MINUTE = timedelta(minutes=1)
PIP = Decimal("0.0001")


def test_quotes_become_hourly_bid_ask_and_mid_bars() -> None:
    quotes = [
        quote(MONDAY, "1.10000", "1.10010"),
        quote(MONDAY + 10 * MINUTE, "1.10050", "1.10070"),
        quote(MONDAY + 59 * MINUTE + timedelta(seconds=59), "1.09980", "1.09990"),
        quote(MONDAY + HOUR, "1.10020", "1.10030"),  # the boundary belongs to the next bar
    ]

    first, second = aggregate(quotes, HOUR)

    assert (first.timestamp, first.quotes, first.kind, first.symbol) == (MONDAY, 3, QuoteKind.SYNTHETIC, "EURUSD")
    assert first.bid == (Decimal("1.10000"), Decimal("1.10050"), Decimal("1.09980"), Decimal("1.09980"))
    assert first.ask == (Decimal("1.10010"), Decimal("1.10070"), Decimal("1.09990"), Decimal("1.09990"))
    assert first.mid == (Decimal("1.10005"), Decimal("1.10060"), Decimal("1.09985"), Decimal("1.09985"))
    assert (first.spread_open, first.spread_close, first.spread_max) == (Decimal("0.00010"), Decimal("0.00010"), Decimal("0.00020"))
    assert first.spread_mean == (Decimal("0.00040") / 3)
    assert (first.first_quote, first.last_quote) == (MONDAY, MONDAY + 59 * MINUTE + timedelta(seconds=59))
    assert (second.timestamp, second.quotes) == (MONDAY + HOUR, 1)


def test_an_hour_without_quotes_produces_no_bar() -> None:
    quotes = [quote(MONDAY, "1.1", "1.1001"), quote(MONDAY + 3 * HOUR, "1.1", "1.1001")]

    bars = aggregate(quotes, HOUR)

    assert [b.timestamp for b in bars] == [MONDAY, MONDAY + 3 * HOUR]  # nothing is filled in or carried forward


def test_aggregation_requires_audited_input() -> None:
    ordered = synthetic_quotes(count=3)
    with pytest.raises(ValueError, match="orden"):
        aggregate([ordered[1], ordered[0]], HOUR)
    with pytest.raises(ValueError, match="clases"):
        aggregate([ordered[0], quote(MONDAY + HOUR, "1.1", "1.1001", kind=QuoteKind.OBSERVED)], HOUR)
    with pytest.raises(ValueError, match="bid"):
        aggregate([quote(MONDAY, "1.2", "1.1")], HOUR)
    assert aggregate([], HOUR) == []


def test_aggregation_is_deterministic() -> None:
    quotes = synthetic_quotes(count=600)

    assert aggregate(quotes, HOUR) == aggregate(list(quotes), HOUR) and len(aggregate(quotes, HOUR)) == 10


def test_price_bars_for_one_side() -> None:
    bars = aggregate(synthetic_quotes(count=120), HOUR)

    mid, bid, ask = to_price_bars(bars), to_price_bars(bars, "bid"), to_price_bars(bars, "ask")

    assert all(isinstance(b, PriceBar) for b in mid) and [b.timestamp for b in mid] == [MONDAY, MONDAY + HOUR]
    assert mid[0].volume == 60  # quotes in the bar, not traded volume
    assert all(a.close - b.close == Decimal("0.00010") and m.close == (a.close + b.close) / 2 for m, b, a in zip(mid, bid, ask))
    with pytest.raises(ValueError):
        to_price_bars(bars, "last")


# --- comparison ---


def bar(hour: int, close: str, wick: str = "0.0002") -> PriceBar:
    c, w = Decimal(close), Decimal(wick)
    return PriceBar(MONDAY + hour * HOUR, c, c + w, c - w, c, 0)


def test_two_sources_are_compared_bar_by_bar_in_pips() -> None:
    reference = [bar(0, "1.1000"), bar(1, "1.1010"), bar(2, "1.1020"), bar(3, "1.1030")]
    other = [bar(1, "1.1011"), bar(2, "1.1017"), bar(3, "1.1030"), bar(4, "1.1040")]

    result = compare_bars(reference, other, PIP)

    assert result.common == 3 and result.only_in_reference == (MONDAY,) and result.only_in_other == (MONDAY + 4 * HOUR,)
    close = result.fields["close"]
    assert (close.mean_abs, close.median_abs, close.max_abs) == (Decimal("4") / 3, Decimal("1"), Decimal("3"))
    assert close.mean_signed == Decimal("-2") / 3  # other minus reference
    assert result.fields["high"].max_abs == Decimal("3")
    assert result.worst_close == (MONDAY + 2 * HOUR, Decimal("-3"))


def test_comparison_aligns_instants_across_utc_offsets() -> None:
    from datetime import timezone

    london = timezone(timedelta(hours=1))
    reference = [bar(0, "1.1000"), bar(1, "1.1010")]
    other = [PriceBar(b.timestamp.astimezone(london), b.open, b.high, b.low, b.close, 0) for b in reference]

    result = compare_bars(reference, other, PIP)

    assert result.common == 2 and result.fields["close"].max_abs == 0


def test_comparison_without_common_bars() -> None:
    result = compare_bars([bar(0, "1.1")], [bar(5, "1.1")], PIP)

    assert result.common == 0 and result.fields == {} and result.worst_close is None
