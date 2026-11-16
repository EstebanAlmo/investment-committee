"""Quotes -> bid/ask bars, independent of any provider.

A bar labelled t (UTC, on the timeframe's grid) is built from the quotes
with t <= timestamp < t + timeframe: the label is the bar's OPEN time, the
convention the rest of the project uses (simulation/sessions.py). For each
side - bid, ask, and mid = (bid + ask) / 2 quote by quote - it keeps the
first, highest, lowest and last value. The mid high is the highest mid
quote, not the average of the bid high and the ask high.

Nothing is invented: an interval without quotes produces NO bar (it is not
filled with the previous close), and a bar says how many quotes it holds and
when its first and last quote arrived, so a thin bar can be told from a
full one. Input must already be audited (quality.audit_quotes): one
instrument, one kind, strictly increasing time, bid <= ask. Anything else is
refused. A bar inherits its quotes' kind.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Sequence

from investment_committee.domain import PriceBar

from .quotes import Quote, QuoteKind

OHLC = tuple[Decimal, Decimal, Decimal, Decimal]
SIDES = ("mid", "bid", "ask")


@dataclass(frozen=True)
class BidAskBar:
    symbol: str
    timestamp: datetime  # the bar's open time, UTC
    timeframe: timedelta
    bid: OHLC
    ask: OHLC
    mid: OHLC
    quotes: int
    spread_open: Decimal
    spread_close: Decimal
    spread_max: Decimal
    spread_mean: Decimal
    first_quote: datetime
    last_quote: datetime
    kind: QuoteKind


def _ohlc(values: Sequence[Decimal]) -> OHLC:
    return values[0], max(values), min(values), values[-1]


def aggregate(quotes: Sequence[Quote], timeframe: timedelta) -> list[BidAskBar]:
    if not quotes:
        return []
    if len({q.kind for q in quotes}) != 1:
        raise ValueError("cotizaciones de varias clases (observadas, estimadas, sintéticas) no se mezclan")
    if len({q.symbol for q in quotes}) != 1:
        raise ValueError("cotizaciones de varios instrumentos en una misma serie")
    if any(later.timestamp <= earlier.timestamp for earlier, later in zip(quotes, quotes[1:])):
        raise ValueError("las cotizaciones deben venir en orden temporal estricto, sin duplicados (audítalas primero)")
    if any(q.bid <= 0 or q.bid > q.ask for q in quotes):
        raise ValueError("cotización con bid no positivo o bid mayor que ask (audítalas primero)")

    step = int(timeframe.total_seconds())
    buckets: dict[int, list[Quote]] = {}
    for q in quotes:
        buckets.setdefault(int(q.timestamp.timestamp()) // step * step, []).append(q)

    bars = []
    for label in sorted(buckets):
        group = buckets[label]
        spreads = [q.spread for q in group]
        bars.append(BidAskBar(
            symbol=group[0].symbol, timestamp=datetime.fromtimestamp(label, tz=timezone.utc), timeframe=timeframe,
            bid=_ohlc([q.bid for q in group]), ask=_ohlc([q.ask for q in group]), mid=_ohlc([q.mid for q in group]),
            quotes=len(group), spread_open=spreads[0], spread_close=spreads[-1], spread_max=max(spreads),
            spread_mean=sum(spreads, Decimal("0")) / Decimal(len(spreads)),
            first_quote=group[0].timestamp, last_quote=group[-1].timestamp, kind=group[0].kind,
        ))
    return bars


def to_price_bars(bars: Sequence[BidAskBar], side: str = "mid") -> list[PriceBar]:
    """One side as the project's PriceBar. `volume` carries the number of
    quotes in the bar (spot FX has no traded volume here). A PriceBar has
    no `kind`: keep the BidAskBars, or the dataset manifest, next to it."""
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}")
    return [PriceBar(bar.timestamp, *getattr(bar, side), bar.quotes) for bar in bars]
