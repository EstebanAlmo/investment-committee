"""Synthetic bid/ask quotes for the data-layer tests. Not collected by pytest.

Everything built here is QuoteKind.SYNTHETIC: it is made up, deterministic,
and must never be mistaken for observed market data."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from investment_committee.market_intelligence.quotes import Quote, QuoteKind

UTC = timezone.utc
SOURCE = "generador sintético de pruebas"
# Monday 2026-11-02 00:00 UTC: an open FX week, outside any real dataset.
MONDAY = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)


def quote(at: datetime, bid: str, ask: str, symbol: str = "EURUSD", kind: QuoteKind = QuoteKind.SYNTHETIC) -> Quote:
    return Quote(symbol, at, Decimal(bid), Decimal(ask), kind, SOURCE)


def synthetic_quotes(start: datetime = MONDAY, count: int = 120, step: timedelta = timedelta(minutes=1), first_bid: str = "1.10000",
                     spread: str = "0.00010", symbol: str = "EURUSD") -> list[Quote]:
    """A zigzag: +2, -1, +2, -1 ... tenths of a pip per step, constant spread."""
    bid, width, out = Decimal(first_bid), Decimal(spread), []
    for index in range(count):
        out.append(Quote(symbol, start + index * step, bid, bid + width, QuoteKind.SYNTHETIC, SOURCE))
        bid += Decimal("0.00002") if index % 2 == 0 else Decimal("-0.00001")
    return out


def always_open(_moment: datetime) -> bool:
    return True


def weekdays_only(moment: datetime) -> bool:
    """A simple test calendar: open Monday to Friday, UTC days."""
    return moment.astimezone(UTC).weekday() < 5
