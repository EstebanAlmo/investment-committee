"""What the experimental Forex strategies share: the outcome of evaluating a
candle, the contract a signal offers to the proposal engine, and the data
gates applied BEFORE any indicator is computed.

Used by simulation/ema_cross.py (ema-rsi-atr-forex, 300 hours) and
simulation/ema_trend_cross.py (ema-3-9-20-50-120-forex, 720 hours). Each
strategy passes its own window and tolerances; nothing here knows either.

closed_window(bars, as_of, ...) looks at ONE candle: the candle that closed
at the last full timeframe step at or before `as_of`. Gates, in order; the
first that fails gives a rejection code:

  1. MERCADO_CERRADO        that candle's hour is not inside the calendar.
  2. Only bars fully closed at `as_of` are used (label + timeframe <=
     as_of); a forming bar is dropped and counted.
  3. DATOS_INCONSISTENTES   quality.audit_bars finds, from the start of the
     window on, two different bars for one instant, a label off the grid,
     or a non-positive price. Bars outside the calendar are dropped; exact
     duplicates collapse.
  4. Continuity. The window is the last `window_bars` hours the calendar
     EXPECTS, ending at that candle - not "the last N bars available". Two
     expected hours separated by a closed market (a weekend) are
     neighbours: a normal closure, counted in `market_closures`. An
     expected hour with no bar is an anomalous gap, counted in `missing`;
     the window never reaches further back to replace it.
       DATOS_INSUFICIENTES    the history does not reach the window's start;
       DATOS_DESACTUALIZADOS  the candle itself has no bar;
       DATOS_INCOMPLETOS      more than `max_missing` hours missing, or any
                              among the last `contiguous_tail`.
     So the candle and the one before it are always consecutive market
     hours: a cross is never read across a hole.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Protocol, Sequence

from investment_committee.domain import PriceBar
from investment_committee.market_intelligence.quality import Calendar, audit_bars
from investment_committee.market_intelligence.quotes import QuoteKind, to_utc

MARKET_CLOSED = "MERCADO_CERRADO"
INCONSISTENT_DATA = "DATOS_INCONSISTENTES"
INSUFFICIENT_DATA = "DATOS_INSUFICIENTES"
STALE_DATA = "DATOS_DESACTUALIZADOS"
INCOMPLETE_DATA = "DATOS_INCOMPLETOS"

_INSTANT = timedelta(microseconds=1)
_INCONSISTENT = frozenset({"DUPLICADO_CONTRADICTORIO", "FUERA_DE_REJILLA", "PRECIO_INVALIDO"})


class Outcome(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NO_CROSS = "SIN_CRUCE"
    NOT_CONFIRMED = "CRUCE_SIN_CONFIRMACION"
    DATA_REJECTED = "DATOS_RECHAZADOS"


class TradeSignal(Protocol):
    """What trade_proposal.build_proposal reads from a strategy's evaluation
    of a candle. `evaluation_key` names strategy, instrument and candle."""

    symbol: str
    outcome: Outcome
    data_kind: QuoteKind
    candle_close: datetime
    last_close: Decimal | None
    atr: Decimal | None

    @property
    def evaluation_key(self) -> str: ...

    @property
    def is_signal(self) -> bool: ...

    @property
    def signal_id(self) -> str | None: ...


@dataclass(frozen=True)
class StructuralStop:
    """A stop placed by the signal itself, at a price level, instead of at a
    fixed ATR multiple from the entry. A strategy's evaluation may carry one
    as `structural_stop`; build_proposal then uses it. Strategies that carry
    none (ema_cross, ema_trend_cross) are untouched.

    `reference` is the level the strategy wants (already with its buffer).
    The entry is only known when the proposal is built, so two limits travel
    with it, both in multiples of the signal's ATR and measured from the
    entry: nearer than `floor_atr` the stop is moved out to that distance;
    further than `cap_atr` there is no trade."""

    reference: Decimal
    floor_atr: Decimal
    cap_atr: Decimal


@dataclass(frozen=True)
class DataWindow:
    """The candle being evaluated and, unless `rejection` is set, the bars
    of its window, oldest first."""

    as_of: datetime
    candle_open: datetime
    candle_close: datetime
    bars_received: int
    forming_bars_excluded: int
    rejection: str | None = None
    detail: str = ""
    bars: Sequence[PriceBar] = ()
    window_start: datetime | None = None  # first expected hour of the window
    missing: int = 0  # anomalous gaps: expected hours without a bar
    market_closures: int = 0  # normal closures (e.g. weekends) between neighbouring hours


def _expected_hours(last: datetime, count: int, timeframe: timedelta, is_open: Calendar) -> list[datetime]:
    """The `count` most recent bar labels the calendar expects, ending at
    `last`, oldest first. Hours of a closed market are skipped: the labels on
    both sides of a weekend are neighbours."""
    labels, moment, steps = [], last, 0
    while len(labels) < count:
        if is_open(moment) and is_open(moment + timeframe - _INSTANT):
            labels.append(moment)
        moment -= timeframe
        steps += 1
        if steps > count * 50:
            raise ValueError("the calendar leaves too few open hours to build the window")
    return labels[::-1]


def closed_window(
    bars: Sequence[PriceBar],
    as_of: datetime,
    *,
    timeframe: timedelta,
    window_bars: int,
    max_missing: int,
    contiguous_tail: int,
    is_open: Calendar,
) -> DataWindow:
    """The window of the candle that closed at the last full step at or
    before `as_of`, from `bars` (any order, any UTC offset), or the reason
    the data cannot be used."""
    tf = timeframe
    now = to_utc(as_of, "as_of")
    step = int(tf.total_seconds())
    candle_close = datetime.fromtimestamp(int(now.timestamp()) // step * step, tz=now.tzinfo)
    candle_open = candle_close - tf
    closed = [bar for bar in bars if to_utc(bar.timestamp) + tf <= now]
    base = dict(as_of=now, candle_open=candle_open, candle_close=candle_close, bars_received=len(bars), forming_bars_excluded=len(bars) - len(closed))

    def rejected(code: str, detail: str) -> DataWindow:
        return DataWindow(rejection=code, detail=detail, **base)  # type: ignore[arg-type]

    if not (is_open(candle_open) and is_open(candle_close - _INSTANT)):
        return rejected(MARKET_CLOSED, f"la vela {candle_open.isoformat()} está fuera del calendario de mercado")
    if not closed:
        return rejected(INSUFFICIENT_DATA, "no hay ninguna vela cerrada")

    audit = audit_bars(closed, tf, is_open)
    usable = {bar.timestamp: bar for bar in audit.bars}
    slots = _expected_hours(candle_open, window_bars, tf, is_open)
    bad = [i for i in audit.issues if i.code in _INCONSISTENT and i.timestamp >= slots[0]]
    if bad:
        return rejected(INCONSISTENT_DATA, f"{len(bad)} velas inconsistentes en la ventana; la primera: {bad[0].code} en {bad[0].timestamp.isoformat()}")
    if not usable or min(usable) > slots[0]:
        return rejected(INSUFFICIENT_DATA, f"{len(usable)} velas utilizables; la ventana de {window_bars} horas empieza en {slots[0].isoformat()}")
    if candle_open not in usable:
        return rejected(STALE_DATA, f"la última vela cerrada es {max(usable).isoformat()}; se esperaba {candle_open.isoformat()}")
    missing = [slot for slot in slots if slot not in usable]
    if len(missing) > max_missing:
        return rejected(INCOMPLETE_DATA, f"faltan {len(missing)} velas en la ventana (máximo {max_missing})")
    recent = [slot for slot in missing if slot >= slots[-contiguous_tail]]
    if recent:
        return rejected(INCOMPLETE_DATA, f"falta la vela {recent[0].isoformat()} entre las últimas {contiguous_tail}")
    return DataWindow(
        bars=tuple(usable[slot] for slot in slots if slot in usable), window_start=slots[0], missing=len(missing),
        market_closures=sum(1 for earlier, later in zip(slots, slots[1:]) if later - earlier > tf), **base,  # type: ignore[arg-type]
    )
