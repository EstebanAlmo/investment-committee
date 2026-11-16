"""Two bar series of the same instrument and timeframe, side by side.

Two FX sources are not expected to agree exactly: spot FX has no single
exchange, each provider aggregates its own liquidity, and one may publish
bid, another mid. This module therefore only MEASURES - which bars exist in
one source and not the other, and how far apart open, high, low and close
are on the bars both have, in pips (other minus reference). It declares
neither source right and sets no pass/fail threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import median
from typing import Mapping, Sequence

from investment_committee.domain import PriceBar

from .quality import percentile
from .quotes import to_utc

FIELDS = ("open", "high", "low", "close")


@dataclass(frozen=True)
class FieldDifference:
    """Differences on the common bars, in pips."""

    mean_abs: Decimal
    median_abs: Decimal
    p95_abs: Decimal
    max_abs: Decimal
    mean_signed: Decimal  # other minus reference: a constant offset shows here


@dataclass(frozen=True)
class BarComparison:
    common: int
    only_in_reference: Sequence[datetime]
    only_in_other: Sequence[datetime]
    fields: Mapping[str, FieldDifference]  # empty when there is no common bar
    worst_close: tuple[datetime, Decimal] | None  # the bar with the largest close difference, signed pips


def compare_bars(reference: Sequence[PriceBar], other: Sequence[PriceBar], pip_size: Decimal) -> BarComparison:
    ref = {to_utc(b.timestamp): b for b in reference}
    oth = {to_utc(b.timestamp): b for b in other}
    common = sorted(ref.keys() & oth.keys())
    fields: dict[str, FieldDifference] = {}
    worst = None
    if common:
        for name in FIELDS:
            signed = [(getattr(oth[t], name) - getattr(ref[t], name)) / pip_size for t in common]
            absolute = [abs(d) for d in signed]
            count = Decimal(len(common))
            fields[name] = FieldDifference(
                mean_abs=sum(absolute, Decimal("0")) / count, median_abs=median(absolute),
                p95_abs=percentile(absolute, Decimal("0.95")), max_abs=max(absolute), mean_signed=sum(signed, Decimal("0")) / count,
            )
        at = max(common, key=lambda t: abs(oth[t].close - ref[t].close))
        worst = (at, (oth[at].close - ref[at].close) / pip_size)
    return BarComparison(
        common=len(common), only_in_reference=tuple(sorted(ref.keys() - oth.keys())), only_in_other=tuple(sorted(oth.keys() - ref.keys())),
        fields=fields, worst_close=worst,
    )
