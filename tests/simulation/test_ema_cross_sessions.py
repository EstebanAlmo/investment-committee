"""Tests for simulation.ema_cross_sessions: entry blocks, forced closes and
financing nights, in New York time, across daylight-saving changes.

2026: New York is on summer time (UTC-4) until 1 November, then winter time
(UTC-5). 2027: summer time starts on 14 March."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from investment_committee.simulation.ema_cross_sessions import entry_block, horizon_end, rollover_nights, time_exit_due, weekly_close

UTC = timezone.utc
HOUR = timedelta(hours=1)


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        ("2026-12-01 14:00", None),  # Tuesday 09:00 New York
        ("2026-12-05 12:00", "MERCADO_CERRADO"),  # Saturday
        ("2026-12-06 21:59", "MERCADO_CERRADO"),  # Sunday 16:59 New York
        ("2026-12-06 22:00", "ENTRADA_BLOQUEADA_ROLLOVER"),  # Sunday 17:00: the week opens inside the rollover window
        ("2026-12-06 23:00", None),  # Sunday 18:00
        ("2026-12-02 20:59", None),  # Wednesday 15:59
        ("2026-12-02 21:00", "ENTRADA_BLOQUEADA_ROLLOVER"),  # 16:00
        ("2026-12-02 22:59", "ENTRADA_BLOQUEADA_ROLLOVER"),  # 17:59
        ("2026-12-02 23:00", None),  # 18:00
        ("2026-12-04 16:59", None),  # Friday 11:59
        ("2026-12-04 17:00", "ENTRADA_BLOQUEADA_VIERNES"),  # Friday 12:00
        ("2026-12-04 21:30", "ENTRADA_BLOQUEADA_VIERNES"),  # Friday 16:30
        ("2026-12-04 22:00", "MERCADO_CERRADO"),  # Friday 17:00
        # the same New York hours one hour earlier in UTC while on summer time
        ("2026-10-30 15:59", None),  # Friday 11:59
        ("2026-10-30 16:00", "ENTRADA_BLOQUEADA_VIERNES"),  # Friday 12:00
        ("2026-10-28 20:00", "ENTRADA_BLOQUEADA_ROLLOVER"),  # Wednesday 16:00
        ("2026-10-28 22:00", None),  # Wednesday 18:00
    ],
)
def test_entry_blocks(moment: str, expected: str | None) -> None:
    assert entry_block(utc(moment)) == expected


def test_the_weekly_close_is_friday_16_new_york() -> None:
    assert weekly_close(utc("2026-12-01 14:00")) == utc("2026-12-04 21:00")  # winter: 16:00 is 21:00 UTC
    assert weekly_close(utc("2026-10-27 14:00")) == utc("2026-10-30 20:00")  # summer: 20:00 UTC
    assert weekly_close(utc("2026-12-06 23:00")) == utc("2026-12-11 21:00")  # Sunday evening belongs to the next week
    assert weekly_close(utc("2026-12-04 21:00")) == utc("2026-12-04 21:00")
    assert weekly_close(utc("2027-03-11 14:00")) == utc("2027-03-12 21:00")  # the Friday before the clocks change
    assert weekly_close(utc("2027-03-15 14:00")) == utc("2027-03-19 20:00")  # the Friday after


def test_the_time_exit_counts_72_bars_of_open_market_from_the_entry_bar() -> None:
    assert time_exit_due(utc("2026-12-01 14:00"), 72) == utc("2026-12-04 14:00")
    assert time_exit_due(utc("2026-12-01 14:03"), 72) == utc("2026-12-04 14:00")  # same bar, same due time
    assert time_exit_due(utc("2026-12-01 14:00"), 1) == utc("2026-12-01 15:00")
    # across a weekend: Thursday 14:00 + 32 bars reaches Friday 22:00 UTC; the other 40 start Sunday 22:00 UTC
    assert time_exit_due(utc("2026-12-03 14:00"), 72) == utc("2026-12-08 14:00")


def test_the_horizon_is_the_earlier_of_the_time_exit_and_the_weekly_close() -> None:
    assert horizon_end(utc("2026-12-01 14:00"), 72) == utc("2026-12-04 14:00")  # Tuesday: 72 bars end before Friday 16:00
    assert horizon_end(utc("2026-12-03 13:00"), 72) == utc("2026-12-04 21:00")  # Thursday: the weekly close comes first
    assert horizon_end(utc("2026-12-06 23:00"), 72) == utc("2026-12-09 23:00")  # Sunday evening


@pytest.mark.parametrize(
    ("start", "end", "nights"),
    [
        ("2026-12-01 14:00", "2026-12-01 21:59", 0),  # closed before Tuesday's rollover
        ("2026-12-01 14:00", "2026-12-01 22:00", 1),  # open at 17:00 New York
        ("2026-12-01 14:00", "2026-12-02 21:59", 1),
        ("2026-12-01 14:00", "2026-12-02 22:00", 4),  # Wednesday counts three
        ("2026-12-01 14:00", "2026-12-04 14:00", 5),  # Tuesday + Wednesday (3) + Thursday
        ("2026-12-03 13:00", "2026-12-04 21:00", 1),  # Thursday only: flat before Friday's rollover
        ("2026-12-06 23:00", "2026-12-09 23:00", 5),  # Monday + Tuesday + Wednesday (3)
        ("2026-12-07 02:00", "2026-12-08 02:00", 1),  # Monday
        ("2026-12-02 23:00", "2026-12-03 21:00", 0),  # entered after Wednesday's rollover, out before Thursday's
        # summer time: the rollover is at 21:00 UTC
        ("2026-10-27 14:00", "2026-10-27 21:00", 1),
        ("2026-10-27 14:00", "2026-10-27 20:59", 0),
        # the week the clocks go back (Sunday 1 November 2026): rollovers at 22:00 UTC from Monday on
        ("2026-11-02 14:00", "2026-11-02 21:30", 0),
        ("2026-11-02 14:00", "2026-11-02 22:00", 1),
        # the week the clocks go forward (Sunday 14 March 2027): 22:00 UTC before, 21:00 UTC after
        ("2027-03-11 14:00", "2027-03-11 22:00", 1),
        ("2027-03-15 14:00", "2027-03-15 21:00", 1),
        ("2027-03-15 14:00", "2027-03-15 20:59", 0),
    ],
)
def test_financing_nights(start: str, end: str, nights: int) -> None:
    assert rollover_nights(utc(start), utc(end)) == nights


def test_financing_nights_need_a_forward_interval() -> None:
    assert rollover_nights(utc("2026-12-01 14:00"), utc("2026-12-01 14:00")) == 0
    with pytest.raises(ValueError):
        rollover_nights(utc("2026-12-01 14:00"), utc("2026-12-01 13:00"))
