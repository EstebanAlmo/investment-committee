"""Phase 0 - temporal integrity: 1-hour bars are labelled by their OPEN
time; only bars fully closed at the decision moment may be used. Plus the
Forex session calendar (New York 17:00 rollover, daylight saving)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from investment_committee.domain import PriceBar
from investment_committee.simulation import closed_bars
from investment_committee.simulation.opportunities import is_aligned, prepare_bars
from investment_committee.simulation.sessions import ForexSessionRules, is_week_open, new_york_time, session_day

HOUR = timedelta(hours=1)
UTC = timezone.utc
LONDON = ZoneInfo("Europe/London")


def bar(at: datetime, price: str = "1.1000") -> PriceBar:
    p = Decimal(price)
    return PriceBar(at, p, p + Decimal("0.0005"), p - Decimal("0.0005"), p, 0)


def hourly(start: datetime, count: int) -> list[PriceBar]:
    return [bar(start + i * HOUR) for i in range(count)]


def test_the_report_case_bar_opened_22_00_is_still_forming_at_22_19() -> None:
    bars = hourly(datetime(2026, 10, 13, 19, tzinfo=UTC), 4)  # opens 19, 20, 21, 22
    now = datetime(2026, 10, 13, 22, 19, tzinfo=UTC)

    closed, data = prepare_bars(bars, HOUR, now, "test")

    assert closed[-1].timestamp == datetime(2026, 10, 13, 21, tzinfo=UTC)  # opened 21:00
    assert data.last_closed_bar_close == datetime(2026, 10, 13, 22, tzinfo=UTC)  # closed 22:00
    assert data.forming_bars_excluded == 1  # the bar opened at 22:00


def test_a_bar_closes_exactly_at_open_plus_one_hour() -> None:
    bars = hourly(datetime(2026, 10, 13, 21, tzinfo=UTC), 1)
    close = datetime(2026, 10, 13, 22, tzinfo=UTC)

    assert closed_bars(bars, HOUR, close - timedelta(microseconds=1)) == []
    assert closed_bars(bars, HOUR, close) == bars


def test_day_change_bar_opened_23_00_closes_at_midnight() -> None:
    bars = hourly(datetime(2026, 10, 13, 22, tzinfo=UTC), 2)  # 22:00 and 23:00

    closed, _ = prepare_bars(bars, HOUR, datetime(2026, 10, 13, 23, 59, tzinfo=UTC), "test")
    assert [b.timestamp.hour for b in closed] == [22]
    closed, _ = prepare_bars(bars, HOUR, datetime(2026, 10, 14, 0, 0, tzinfo=UTC), "test")
    assert [b.timestamp.hour for b in closed] == [22, 23]


def test_london_labelled_bars_across_the_dst_change_are_compared_in_absolute_time() -> None:
    # Yahoo FX labels carry Europe/London offsets; on 2026-10-25 BST ends.
    before = datetime(2026, 10, 25, 0, 0, tzinfo=LONDON)  # 23:00 UTC on the 24th (BST)
    after = datetime(2026, 10, 25, 2, 0, tzinfo=LONDON)  # 02:00 UTC (GMT)
    bars = [bar(before), bar(after)]

    closed, data = prepare_bars(bars, HOUR, datetime(2026, 10, 25, 2, 30, tzinfo=UTC), "test")

    assert closed == [bars[0]] and data.forming_bars_excluded == 1 and data.irregular_bars_dropped == 0


def test_an_unaligned_live_row_is_dropped_not_treated_as_a_closed_bar() -> None:
    bars = [*hourly(datetime(2026, 10, 13, 20, tzinfo=UTC), 2), bar(datetime(2026, 10, 13, 22, 27, 26, tzinfo=UTC))]

    closed, data = prepare_bars(bars, HOUR, datetime(2026, 10, 14, 1, tzinfo=UTC), "test")

    assert data.irregular_bars_dropped == 1 and [b.timestamp.hour for b in closed] == [20, 21]
    assert not is_aligned(datetime(2026, 10, 13, 22, 0, 0, 500, tzinfo=UTC), HOUR)
    assert is_aligned(datetime(2026, 10, 13, 23, 0, tzinfo=LONDON), HOUR)


def test_future_stamped_bars_are_dropped_and_counted() -> None:
    now = datetime(2026, 10, 13, 22, 19, tzinfo=UTC)
    bars = [*hourly(datetime(2026, 10, 13, 20, tzinfo=UTC), 2), bar(datetime(2026, 10, 14, 1, tzinfo=UTC))]

    closed, data = prepare_bars(bars, HOUR, now, "test")

    assert data.future_bars_dropped == 1 and all(b.timestamp + HOUR <= now for b in closed)


# --- session calendar ---


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 10, 13, 20, 59, tzinfo=UTC), date(2026, 10, 13)),  # 16:59 EDT
        (datetime(2026, 10, 13, 21, 0, tzinfo=UTC), date(2026, 10, 14)),  # 17:00 EDT rollover
        (datetime(2026, 11, 13, 21, 0, tzinfo=UTC), date(2026, 11, 13)),  # 16:00 EST: winter rollover is 22:00 UTC
        (datetime(2026, 11, 13, 22, 0, tzinfo=UTC), date(2026, 11, 14)),
    ],
)
def test_session_day_follows_the_new_york_rollover_with_dst(moment: datetime, expected: date) -> None:
    assert session_day(moment) == expected


def test_week_opens_sunday_and_closes_friday_17_00_new_york() -> None:
    assert not is_week_open(datetime(2026, 10, 11, 20, 59, tzinfo=UTC))  # Sunday 16:59 EDT
    assert is_week_open(datetime(2026, 10, 11, 21, 0, tzinfo=UTC))  # Sunday 17:00 EDT
    assert is_week_open(datetime(2026, 10, 16, 20, 59, tzinfo=UTC))  # Friday 16:59 EDT
    assert not is_week_open(datetime(2026, 10, 16, 21, 0, tzinfo=UTC))
    assert is_week_open(datetime(2026, 11, 13, 21, 30, tzinfo=UTC))  # Friday 16:30 EST
    assert not is_week_open(datetime(2026, 11, 14, 12, 0, tzinfo=UTC))  # Saturday


def test_flat_and_last_entry_times_move_with_dst() -> None:
    rules = ForexSessionRules()

    assert rules.flat_time(datetime(2026, 10, 13, 12, tzinfo=UTC)) == datetime(2026, 10, 13, 20, tzinfo=UTC)  # 16:00 EDT
    assert rules.flat_time(datetime(2026, 11, 13, 12, tzinfo=UTC)) == datetime(2026, 11, 13, 21, tzinfo=UTC)  # 16:00 EST
    assert rules.flat_time(datetime(2026, 10, 13, 22, tzinfo=UTC)) == datetime(2026, 10, 14, 20, tzinfo=UTC)  # after rollover
    assert rules.may_enter(datetime(2026, 10, 13, 18, tzinfo=UTC))  # 14:00 EDT
    assert not rules.may_enter(datetime(2026, 10, 13, 19, tzinfo=UTC))  # 15:00 EDT: too late in the session
    assert rules.may_enter(datetime(2026, 10, 13, 21, tzinfo=UTC))  # 17:00 EDT: next session
    assert new_york_time(date(2026, 3, 9), time(16)) == datetime(2026, 3, 9, 20, tzinfo=UTC)  # first EDT Monday


def test_session_rules_are_validated() -> None:
    with pytest.raises(ValueError):
        ForexSessionRules(last_entry=time(16), flat_by=time(15))
