"""Forex session calendar with daylight saving, for day-trading rules.

Convention (the usual retail-FX one, stated explicitly):
- The FX week opens Sunday 17:00 and closes Friday 17:00, New York time
  (America/New_York). New York's daylight saving moves both in UTC: 21:00
  UTC in summer (EDT), 22:00 UTC in winter (EST).
- A trading day ("session day") runs from 17:00 New York (the daily
  rollover) to 17:00 New York the next day, and is named by the date on
  which it ends. Sunday evening belongs to Monday's session.
- Day-trading rules (ForexSessionRules): no new entry decided at or after
  `last_entry` (default 15:00 New York) and every position is closed
  ("flat") by `flat_by` (default 16:00 New York), one hour before the
  rollover, when spreads widen.
Not modelled: bank holidays and the thin Christmas / New Year sessions;
data gaps on those days show up as missing bars, and an entry or exit then
waits for the next bar that exists.

Hourly Yahoo bars are labelled by their OPEN time (verified on 2026-10-13
against 5-minute bars of EUR/USD, GBP/USD and USD/JPY: each 1-hour bar
stamped t equals the 5-minute bars of [t, t + 1h)). New York offsets are
whole hours, so the session times above fall on hourly bar boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
ROLLOVER = time(17, 0)


def session_day(moment: datetime) -> date:
    """The FX trading day `moment` belongs to (named by its closing date)."""
    local = moment.astimezone(NEW_YORK)
    return (local + timedelta(days=1)).date() if local.time() >= ROLLOVER else local.date()


def is_week_open(moment: datetime) -> bool:
    """Sunday 17:00 to Friday 17:00 New York time."""
    local = moment.astimezone(NEW_YORK)
    weekday = local.weekday()  # Monday == 0
    if weekday == 5:
        return False
    if weekday == 6:
        return local.time() >= ROLLOVER
    if weekday == 4:
        return local.time() < ROLLOVER
    return True


def new_york_time(day: date, at: time) -> datetime:
    """`at` New York time on `day`, in UTC (daylight saving applied)."""
    return datetime.combine(day, at, tzinfo=NEW_YORK).astimezone(timezone.utc)


@dataclass(frozen=True)
class ForexSessionRules:
    last_entry: time = time(15, 0)
    flat_by: time = time(16, 0)

    def __post_init__(self) -> None:
        if not (self.last_entry < self.flat_by <= ROLLOVER):
            raise ValueError("last_entry must be before flat_by, and flat_by not after the 17:00 rollover")

    def flat_time(self, moment: datetime) -> datetime:
        """When a position opened at `moment` must be closed, in UTC."""
        return new_york_time(session_day(moment), self.flat_by)

    def may_enter(self, moment: datetime) -> bool:
        """A new entry may be decided at `moment`: the week is open and the
        session's last-entry time has not been reached."""
        return is_week_open(moment) and moment < new_york_time(session_day(moment), self.last_entry)
