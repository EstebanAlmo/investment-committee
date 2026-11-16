"""Session rules of ema-rsi-atr-forex v0.1.0: when an entry is allowed, when
a position must be closed, and how many financing nights it crosses.

Separate from sessions.py's ForexSessionRules (the day-trading rules of the
frozen strategies, untouched). Every time is defined in New York local time
and converted with the time-zone database, so daylight saving is applied by
construction; New York offsets are whole hours, so every boundary falls on
an hourly bar boundary.

Entries are blocked
  - while the FX week is closed (Friday 17:00 to Sunday 17:00 New York);
  - on Fridays from 12:00 New York;
  - during the rollover window, 16:00 to 18:00 New York, every day.
Stops and targets stay live during the rollover window.

A position is closed at the market
  - at the weekly close: Friday 16:00 New York of the week it was opened in;
  - or when `max_bars` (72) hourly bars of open market have passed, counting
    the entry's own bar - whichever comes first (`horizon_end`).

Financing nights: a position open at 17:00 New York pays one night; the
Wednesday rollover counts three (the usual spot convention for the weekend).
That convention, like the financing rate, is an ASSUMPTION until a provider
is chosen (docs/ema-rsi-atr-proposals.md).

Holidays are not modelled: no special closure is assumed without evidence.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from .sessions import NEW_YORK, is_week_open

UTC = timezone.utc
HOUR = timedelta(hours=1)
_INSTANT = timedelta(microseconds=1)
FRIDAY, WEDNESDAY = 4, 2
NO_ENTRY_FRIDAY_FROM = time(12, 0)
ROLLOVER_BLOCK = (time(16, 0), time(18, 0))
WEEKLY_CLOSE = time(16, 0)
ROLLOVER = time(17, 0)
MARKET_CLOSED = "MERCADO_CERRADO"
BLOCKED_FRIDAY = "ENTRADA_BLOQUEADA_VIERNES"
BLOCKED_ROLLOVER = "ENTRADA_BLOQUEADA_ROLLOVER"


def entry_block(moment: datetime) -> str | None:
    """Why no entry may be taken at `moment`, or None."""
    if not is_week_open(moment):
        return MARKET_CLOSED
    local = moment.astimezone(NEW_YORK)
    if local.weekday() == FRIDAY and local.time() >= NO_ENTRY_FRIDAY_FROM:
        return BLOCKED_FRIDAY
    if ROLLOVER_BLOCK[0] <= local.time() < ROLLOVER_BLOCK[1]:
        return BLOCKED_ROLLOVER
    return None


def weekly_close(moment: datetime) -> datetime:
    """Friday 16:00 New York of the FX week `moment` belongs to, in UTC. A
    Sunday evening belongs to the week that follows it."""
    local = moment.astimezone(NEW_YORK)
    friday = local.date() + timedelta(days=(FRIDAY - local.weekday()) % 7)
    return datetime.combine(friday, WEEKLY_CLOSE, tzinfo=NEW_YORK).astimezone(UTC)


def time_exit_due(entry_time: datetime, max_bars: int) -> datetime:
    """The close of the `max_bars`-th hourly bar of open market, counting
    the bar the entry falls in as the first."""
    label = entry_time.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    counted = 0
    for _ in range(max_bars * 50):
        if is_week_open(label) and is_week_open(label + HOUR - _INSTANT):
            counted += 1
            if counted == max_bars:
                return label + HOUR
        label += HOUR
    raise ValueError("no open market ahead to count the holding period")


def horizon_end(entry_time: datetime, max_bars: int) -> datetime:
    """The latest moment the position can still be open."""
    return min(time_exit_due(entry_time, max_bars), weekly_close(entry_time))


def rollover_nights(start: datetime, end: datetime) -> int:
    """Financing nights of a position open from `start` to `end`: one per
    17:00 New York rollover with start < rollover <= end on Monday to
    Thursday, the Wednesday one counting three."""
    if end < start:
        raise ValueError("end must not be before start")
    day, last, nights = start.astimezone(NEW_YORK).date(), end.astimezone(NEW_YORK).date(), 0
    while day <= last:
        rollover = datetime.combine(day, ROLLOVER, tzinfo=NEW_YORK).astimezone(UTC)
        if start < rollover <= end and day.weekday() < FRIDAY:
            nights += 3 if day.weekday() == WEDNESDAY else 1
        day += timedelta(days=1)
    return nights
