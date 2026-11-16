"""Tests for simulation.signals: what the two experimental Forex strategies
share. The gates themselves are exercised through each strategy
(test_ema_cross.py, test_ema_trend_cross.py); here, that the shared part is
really shared and knows neither strategy."""

from __future__ import annotations

from investment_committee.simulation import ema_cross, ema_trend_cross, signals, trade_proposal
from investment_committee.simulation.signals import INCOMPLETE_DATA, INCONSISTENT_DATA, INSUFFICIENT_DATA, MARKET_CLOSED, STALE_DATA, Outcome, closed_window

from ._ema_cross_support import HOUR, always_open, walk


def test_both_strategies_and_the_proposal_engine_use_the_same_outcome() -> None:
    assert ema_cross.Outcome is Outcome and ema_trend_cross.Outcome is Outcome and trade_proposal.Outcome is Outcome
    assert [o.value for o in Outcome] == ["LONG", "SHORT", "SIN_CRUCE", "CRUCE_SIN_CONFIRMACION", "DATOS_RECHAZADOS"]


def test_every_rejection_code_is_a_reason_of_both_strategies() -> None:
    for code in (MARKET_CLOSED, INCONSISTENT_DATA, INSUFFICIENT_DATA, STALE_DATA, INCOMPLETE_DATA):
        assert ema_cross.Reason(code).value == ema_trend_cross.Reason(code).value == code


def test_the_shared_part_knows_neither_strategy() -> None:
    imports = [line for line in open(signals.__file__, encoding="utf-8").read().splitlines() if line.startswith(("from ", "import "))]

    assert imports and not [line for line in imports if "ema_" in line or "trade_proposal" in line or "wilder" in line]


def test_the_time_zone_database_is_a_declared_dependency_with_the_same_answers_everywhere() -> None:
    """Windows has no time-zone database of its own: zoneinfo reads the
    `tzdata` package, declared in pyproject.toml. The New York offsets the
    sessions rely on are literal here, for Windows and macOS alike."""
    import importlib.metadata
    from datetime import datetime, timezone
    from pathlib import Path
    from zoneinfo import ZoneInfo

    assert '"tzdata>=' in (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    assert importlib.metadata.version("tzdata")
    new_york = ZoneInfo("America/New_York")
    offsets = {day: datetime(*day, 12, 0, tzinfo=timezone.utc).astimezone(new_york).utcoffset().total_seconds() / 3600  # type: ignore[union-attr]
               for day in ((2026, 10, 31), (2026, 11, 2), (2026, 12, 15), (2027, 3, 13), (2027, 3, 15))}
    assert offsets == {(2026, 10, 31): -4, (2026, 11, 2): -5, (2026, 12, 15): -5, (2027, 3, 13): -5, (2027, 3, 15): -4}


def test_the_window_and_its_tolerances_are_the_callers() -> None:
    bars = walk(60)
    as_of = bars[-1].timestamp + HOUR
    holed = bars[:20] + bars[22:]

    def window(data, **kwargs):  # type: ignore[no-untyped-def]
        options = dict(timeframe=HOUR, window_bars=40, max_missing=1, contiguous_tail=10, is_open=always_open)
        return closed_window(data, as_of, **{**options, **kwargs})

    whole = window(bars)
    assert (whole.rejection, len(whole.bars), whole.window_start, whole.missing, whole.market_closures) == (None, 40, bars[20].timestamp, 0, 0)
    assert (whole.candle_open, whole.candle_close, whole.bars_received, whole.forming_bars_excluded) == (bars[-1].timestamp, as_of, 60, 0)
    assert window(holed).rejection == INCOMPLETE_DATA  # two holes, one allowed
    assert (window(holed, max_missing=2).rejection, window(holed, max_missing=2).missing, len(window(holed, max_missing=2).bars)) == (None, 2, 38)
    assert window(holed, max_missing=2, contiguous_tail=40).rejection == INCOMPLETE_DATA  # the holes are now in the tail
    assert window(bars, window_bars=61).rejection == INSUFFICIENT_DATA
