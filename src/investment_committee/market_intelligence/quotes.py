"""Normalized bid/ask quote, independent of any provider.

A Quote is one instant of one instrument: a bid, an ask, when, from where,
and - never optional - WHAT KIND of number it is:
  OBSERVADA  a quote a provider actually published (historical or live);
  ESTIMADA   derived from something else under a stated assumption, e.g. a
             mid price plus an assumed spread. Never an executable price;
  SINTETICA  made up for tests or demonstrations.
The three kinds are never mixed in one series, one bar or one dataset
(quality.py, aggregation.py and provenance.py refuse it), so an estimate
can never be read later as an observation.

Timestamps are stored in UTC. A naive datetime is refused, never guessed.
Prices are Decimals. A Quote may hold an invalid pair (bid above ask, a
non-positive price): validating is quality.audit_quotes' job, so that bad
rows are counted and reported rather than silently lost while parsing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

UTC = timezone.utc


class QuoteKind(str, Enum):
    OBSERVED = "OBSERVADA"
    ESTIMATED = "ESTIMADA"
    SYNTHETIC = "SINTETICA"


def to_utc(moment: datetime, name: str = "timestamp") -> datetime:
    """The same instant in UTC. Naive datetimes are refused."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{name} sin zona horaria: no se asume ninguna")
    return moment.astimezone(UTC)


@dataclass(frozen=True)
class Quote:
    symbol: str
    timestamp: datetime
    bid: Decimal
    ask: Decimal
    kind: QuoteKind
    source: str

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol must not be blank")
        if not self.source.strip():
            raise ValueError("source must not be blank: every quote says where it comes from")
        if not isinstance(self.bid, Decimal) or not isinstance(self.ask, Decimal):
            raise TypeError("bid and ask must be Decimal")
        if not isinstance(self.kind, QuoteKind):
            raise TypeError("kind must be a QuoteKind")
        object.__setattr__(self, "timestamp", to_utc(self.timestamp))

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid


def estimated_from_mid(symbol: str, timestamp: datetime, mid: Decimal, spread: Decimal, source: str) -> Quote:
    """An ESTIMATED quote around a mid price with an ASSUMED spread. The
    assumption is written into the quote's source."""
    if spread < 0:
        raise ValueError("the assumed spread must not be negative")
    half = spread / 2
    return Quote(symbol, timestamp, mid - half, mid + half, QuoteKind.ESTIMATED, f"{source} (precio medio; spread asumido {spread})")
