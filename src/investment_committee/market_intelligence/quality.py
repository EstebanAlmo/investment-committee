"""Data-quality checks for quotes and bars, independent of any provider.

Nothing here repairs data. A row is accepted, flagged, or rejected, and
every flag and rejection is counted:

Quotes (audit_quotes)
  PRECIO_INVALIDO           bid or ask not positive                -> rejected
  BID_MAYOR_QUE_ASK         crossed quote (bid == ask is allowed)  -> rejected
  DUPLICADO                 same instant, same prices              -> one kept
  DUPLICADO_CONTRADICTORIO  same instant, different prices         -> all rejected
                            (which one is right is unknown)
  SPREAD_AMPLIO             spread above `max_spread`              -> kept, flagged
                            (a wide spread is information, e.g. the rollover)
  FUERA_DE_ORDEN            arrived before an earlier-stamped row  -> kept, sorted

Bars (audit_bars), timestamps compared as instants after UTC normalization
  FUERA_DE_REJILLA          label not on the timeframe's UTC grid  -> rejected
  DUPLICADO / DUPLICADO_CONTRADICTORIO                              as above
  PRECIO_INVALIDO           any of O/H/L/C not positive            -> rejected
  MERCADO_CERRADO           the bar's period is not inside the     -> rejected
                            market calendar given by the caller
  VELA_PLANA                open = high = low = close              -> kept, flagged

Market gaps: a grid point is EXPECTED when the calendar says the market is
open for the bar's whole period. The calendar is a function supplied by the
caller (e.g. simulation.sessions.is_week_open), so this module assumes no
trading hours of its own. A closed market is never a gap.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from math import ceil
from statistics import median
from typing import Callable, Iterable, Mapping, Sequence

from investment_committee.domain import PriceBar

from .quotes import UTC, Quote, QuoteKind, to_utc

Calendar = Callable[[datetime], bool]
_INSTANT = timedelta(microseconds=1)


@dataclass(frozen=True)
class Issue:
    code: str
    timestamp: datetime
    detail: str


@dataclass(frozen=True)
class Gap:
    """Consecutive expected bars that are missing: [start, end)."""

    start: datetime
    end: datetime
    missing: int


@dataclass(frozen=True)
class Silence:
    """A stretch between two consecutive quotes with more open-market time
    than allowed."""

    start: datetime
    end: datetime
    open_time: timedelta


def percentile(values: Sequence[Decimal], fraction: Decimal) -> Decimal:
    """Nearest-rank percentile of a non-empty sequence."""
    ordered = sorted(values)
    return ordered[max(0, ceil(fraction * len(ordered)) - 1)]


def _counts(issues: Iterable[Issue]) -> dict[str, int]:
    return dict(Counter(issue.code for issue in issues))


# --- quotes ---


@dataclass(frozen=True)
class QuoteAudit:
    symbol: str
    kind: QuoteKind
    received: int
    accepted: Sequence[Quote]  # valid, unique, in time order
    issues: Sequence[Issue]  # in time order
    spread_min: Decimal | None
    spread_median: Decimal | None
    spread_p95: Decimal | None
    spread_max: Decimal | None

    @property
    def counts(self) -> dict[str, int]:
        return _counts(self.issues)

    @property
    def first(self) -> datetime | None:
        return self.accepted[0].timestamp if self.accepted else None

    @property
    def last(self) -> datetime | None:
        return self.accepted[-1].timestamp if self.accepted else None


def _single(quotes: Sequence[Quote]) -> tuple[str, QuoteKind]:
    kinds, symbols = {q.kind for q in quotes}, {q.symbol for q in quotes}
    if len(kinds) != 1:
        raise ValueError("cotizaciones de varias clases (observadas, estimadas, sintéticas) no se mezclan")
    if len(symbols) != 1:
        raise ValueError("cotizaciones de varios instrumentos en una misma serie")
    return symbols.pop(), kinds.pop()


def audit_quotes(quotes: Sequence[Quote], *, max_spread: Decimal) -> QuoteAudit:
    if not quotes:
        raise ValueError("no quotes to audit")
    symbol, kind = _single(quotes)
    issues: list[Issue] = []

    for earlier, later in zip(quotes, quotes[1:]):
        if later.timestamp < earlier.timestamp:
            issues.append(Issue("FUERA_DE_ORDEN", later.timestamp, f"llegó después de {earlier.timestamp.isoformat()}"))

    by_instant: dict[datetime, list[Quote]] = {}
    for q in quotes:
        if q.bid <= 0 or q.ask <= 0:
            issues.append(Issue("PRECIO_INVALIDO", q.timestamp, f"bid {q.bid}, ask {q.ask}"))
        elif q.bid > q.ask:
            issues.append(Issue("BID_MAYOR_QUE_ASK", q.timestamp, f"bid {q.bid} > ask {q.ask}"))
        else:
            by_instant.setdefault(q.timestamp, []).append(q)

    accepted: list[Quote] = []
    for instant in sorted(by_instant):
        group = by_instant[instant]
        if len({(q.bid, q.ask) for q in group}) > 1:
            issues.extend(Issue("DUPLICADO_CONTRADICTORIO", instant, f"bid {q.bid}, ask {q.ask}") for q in group)
            continue
        issues.extend(Issue("DUPLICADO", instant, "misma cotización repetida") for _ in group[1:])
        q = group[0]
        if q.spread > max_spread:
            issues.append(Issue("SPREAD_AMPLIO", instant, f"spread {q.spread} > {max_spread}"))
        accepted.append(q)

    spreads = [q.spread for q in accepted]
    return QuoteAudit(
        symbol=symbol, kind=kind, received=len(quotes), accepted=tuple(accepted),
        issues=tuple(sorted(issues, key=lambda i: i.timestamp)),
        spread_min=min(spreads) if spreads else None,
        spread_median=median(spreads) if spreads else None,
        spread_p95=percentile(spreads, Decimal("0.95")) if spreads else None,
        spread_max=max(spreads) if spreads else None,
    )


# --- market gaps ---


def _aligned(moment: datetime, timeframe: timedelta) -> bool:
    return moment.microsecond == 0 and int(moment.timestamp()) % int(timeframe.total_seconds()) == 0


def _expected(moment: datetime, timeframe: timedelta, is_open: Calendar) -> bool:
    return is_open(moment) and is_open(moment + timeframe - _INSTANT)


def expected_grid(timeframe: timedelta, start: datetime, end: datetime, is_open: Calendar) -> list[datetime]:
    """Bar labels in [start, end) for which the market is open the whole bar."""
    step = int(timeframe.total_seconds())
    first = int(to_utc(start, "start").timestamp())
    first += (-first) % step
    last = int(to_utc(end, "end").timestamp())
    return [t for t in (datetime.fromtimestamp(s, tz=UTC) for s in range(first, last, step)) if _expected(t, timeframe, is_open)]


def missing_bars(present: Iterable[datetime], timeframe: timedelta, start: datetime, end: datetime, is_open: Calendar) -> list[Gap]:
    have = {to_utc(t) for t in present}
    gaps: list[Gap] = []
    for t in expected_grid(timeframe, start, end, is_open):
        if t in have:
            continue
        if gaps and gaps[-1].end == t:
            gaps[-1] = Gap(gaps[-1].start, t + timeframe, gaps[-1].missing + 1)
        else:
            gaps.append(Gap(t, t + timeframe, 1))
    return gaps


def quote_silences(quotes: Sequence[Quote], max_silence: timedelta, is_open: Calendar, step: timedelta = timedelta(minutes=1)) -> list[Silence]:
    """Stretches between consecutive quotes whose OPEN-market time (counted
    in `step` slices) exceeds `max_silence`. A weekend is not a silence."""
    silences = []
    for earlier, later in zip(quotes, quotes[1:]):
        if later.timestamp - earlier.timestamp <= max_silence:
            continue
        open_time, t = timedelta(0), earlier.timestamp
        while t < later.timestamp:
            if is_open(t):
                open_time += min(step, later.timestamp - t)
            t += step
        if open_time > max_silence:
            silences.append(Silence(earlier.timestamp, later.timestamp, open_time))
    return silences


# --- bars ---


@dataclass(frozen=True)
class BarAudit:
    received: int
    unique: int  # distinct instants received
    bars: Sequence[PriceBar]  # trusted bars, UTC, in time order
    problems: Mapping[str, int]
    issues: Sequence[Issue]
    utc_offsets: Sequence[str]  # offsets the labels arrived with, e.g. ("+00:00", "+01:00")
    expected: int  # open-market grid points in the audited range
    present: int  # of those, how many have a trusted bar
    coverage: Decimal  # present / expected, 4 decimals
    gaps: Sequence[Gap]
    first: datetime | None
    last: datetime | None


def _offset(moment: datetime) -> str:
    text = moment.isoformat()
    return text[-6:] if text[-6] in "+-" else "+00:00"


def audit_bars(bars: Sequence[PriceBar], timeframe: timedelta, is_open: Calendar, start: datetime | None = None, end: datetime | None = None) -> BarAudit:
    """`start`/`end` bound the coverage and gap figures (default: from the
    first to the last trusted bar); every bar received is classified."""
    if not bars:
        raise ValueError("no bars to audit")
    issues: list[Issue] = []
    by_instant: dict[datetime, list[PriceBar]] = {}
    for bar in bars:
        instant = to_utc(bar.timestamp)
        by_instant.setdefault(instant, []).append(bar)

    trusted: list[PriceBar] = []
    for instant in sorted(by_instant):
        group = by_instant[instant]
        if not _aligned(instant, timeframe):
            issues.extend(Issue("FUERA_DE_REJILLA", instant, "la etiqueta no está en la rejilla del marco temporal") for _ in group)
            continue
        if len({(b.open, b.high, b.low, b.close) for b in group}) > 1:
            issues.extend(Issue("DUPLICADO_CONTRADICTORIO", instant, f"O {b.open} H {b.high} L {b.low} C {b.close}") for b in group)
            continue
        issues.extend(Issue("DUPLICADO", instant, "misma vela repetida") for _ in group[1:])
        b = group[0]
        if min(b.open, b.high, b.low, b.close) <= 0:
            issues.append(Issue("PRECIO_INVALIDO", instant, f"O {b.open} H {b.high} L {b.low} C {b.close}"))
            continue
        if not _expected(instant, timeframe, is_open):
            issues.append(Issue("MERCADO_CERRADO", instant, "vela fuera del calendario de mercado"))
            continue
        if b.high == b.low:
            issues.append(Issue("VELA_PLANA", instant, f"sin rango: {b.close}"))
        trusted.append(PriceBar(instant, b.open, b.high, b.low, b.close, b.volume))

    first = trusted[0].timestamp if trusted else None
    last = trusted[-1].timestamp if trusted else None
    range_start = start if start is not None else first
    range_end = end if end is not None else (last + timeframe if last is not None else None)
    expected: list[datetime] = []
    gaps: list[Gap] = []
    if range_start is not None and range_end is not None:
        expected = expected_grid(timeframe, range_start, range_end, is_open)
        gaps = missing_bars((b.timestamp for b in trusted), timeframe, range_start, range_end, is_open)
    have = {b.timestamp for b in trusted}
    present = sum(1 for t in expected if t in have)
    return BarAudit(
        received=len(bars), unique=len(by_instant), bars=tuple(trusted), problems=_counts(issues),
        issues=tuple(sorted(issues, key=lambda i: i.timestamp)), utc_offsets=tuple(sorted({_offset(b.timestamp) for b in bars})),
        expected=len(expected), present=present,
        coverage=(Decimal(present) / Decimal(len(expected))).quantize(Decimal("0.0001")) if expected else Decimal("0"),
        gaps=tuple(gaps), first=first, last=last,
    )
