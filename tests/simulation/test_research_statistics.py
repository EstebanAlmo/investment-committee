"""The protocol's statistics against values known from tables or worked by hand."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from investment_committee.simulation.research_statistics import (
    binomial_upper_tail,
    bootstrap_interval_95,
    null_win_chance,
    research_summary,
    student_t_cdf,
    student_t_quantile,
    t_interval_95,
)

UTC = timezone.utc
START = datetime(2025, 7, 14, tzinfo=UTC)


@pytest.mark.parametrize("df, expected", [(1, 12.7062), (2, 4.3027), (10, 2.2281), (30, 2.0423), (120, 1.9799), (1000, 1.9623)])
def test_the_t_quantile_matches_the_printed_tables(df: int, expected: float) -> None:
    assert student_t_quantile(0.975, df) == pytest.approx(expected, abs=5e-4)


def test_the_t_distribution_is_symmetric_and_becomes_normal() -> None:
    assert student_t_cdf(0.0, 7) == pytest.approx(0.5)
    assert student_t_cdf(-1.3, 7) == pytest.approx(1 - student_t_cdf(1.3, 7))
    assert student_t_cdf(1.959964, 10**6) == pytest.approx(0.975, abs=1e-5)


def test_the_t_interval_of_a_small_sample_worked_by_hand() -> None:
    centre, low, high = t_interval_95([1.0, 2.0, 3.0, 4.0, 5.0])  # type: ignore[misc]
    half = 2.7764 * math.sqrt(2.5) / math.sqrt(5)  # t(0.975, 4) x s / sqrt(n)
    assert (centre, low, high) == pytest.approx((3.0, 3.0 - half, 3.0 + half), abs=1e-3)
    assert t_interval_95([1.0]) is None and bootstrap_interval_95([1.0], seed=1) is None


def test_the_bootstrap_is_repeatable_and_brackets_the_mean() -> None:
    values = [-1.0] * 60 + [2.0] * 40
    first, again = bootstrap_interval_95(values, seed=20261113), bootstrap_interval_95(values, seed=20261113)
    assert first == again
    low, high = first  # type: ignore[misc]
    t = t_interval_95(values)
    assert t is not None and low < 0.2 < high and (low, high) == pytest.approx((t[1], t[2]), abs=0.05)
    assert bootstrap_interval_95([0.5] * 30, seed=3) == (0.5, 0.5)


def test_the_null_chance_is_the_protocols_formula() -> None:
    # stop 15 pips, target 30 pips, spread 1 pip, slippage 0.2 pip: down 13.8, up 31.2
    assert null_win_chance(0.0015, 0.0030, 0.0001, 0.00002) == pytest.approx(13.8 / 45.0)
    assert null_win_chance(0.001, 0.001, 0.0, 0.0) == pytest.approx(0.5)
    with pytest.raises(ValueError):
        null_win_chance(0.0001, 0.003, 0.0001, 0.00002)


def test_the_exact_binomial_tail() -> None:
    assert binomial_upper_tail(0, 10, 0.3) == pytest.approx(1.0)
    assert binomial_upper_tail(10, 10, 0.5) == pytest.approx(0.5**10)
    assert binomial_upper_tail(8, 10, 0.5) == pytest.approx((45 + 10 + 1) / 1024)
    with pytest.raises(ValueError):
        binomial_upper_tail(11, 10, 0.5)


def trade(hours: int, r: str, reason: str, symbol: str = "EURUSD", direction: str = "LONG", status: str = "CERRADA") -> dict:  # type: ignore[type-arg]
    closed = status == "CERRADA"
    return {"status": status, "symbol": symbol, "direction": direction, "entry_at": (START + timedelta(hours=hours)).isoformat(),
            "indeterminate": None if closed or status == "ABIERTA_AL_FINAL" else {"x": 1},
            "result": {"r_multiple": r, "reason": reason} if closed else None,
            "proposal": {"entry": "1.10000", "stop": "1.09850", "target": "1.10300", "spread": "0.00010", "slippage": "0.00002"}}


def test_the_summary_counts_only_valid_closed_trades_and_splits_them() -> None:
    trades = [trade(1, "2.0000", "OBJETIVO"), trade(2, "-1.0000", "STOP", "GBPUSD", "SHORT"), trade(3, "-1.0000", "STOP"),
              trade(900, "0.4000", "TIEMPO", "GBPUSD"), trade(950, "-1.0000", "STOP", direction="SHORT"),
              trade(5, "0", "", status="INDETERMINADA"), trade(990, "0", "", status="ABIERTA_AL_FINAL")]
    s = research_summary(trades, start=START, end=START + timedelta(hours=1000), seed=20261113)
    scopes = s["scopes"]
    assert {name: scopes[name]["n"] for name in scopes} == {"TOTAL": 5, "EURUSD": 3, "GBPUSD": 2, "LONG": 3, "SHORT": 2, "PRIMERA_MITAD": 3, "SEGUNDA_MITAD": 2}
    total = scopes["TOTAL"]
    assert (total["wins"], total["losses"]) == (2, 3) and total["expectancy_r"] == pytest.approx(-0.12)
    assert total["t_interval_95"][0] < -0.12 < total["t_interval_95"][1]
    decided = total["stop_or_target"]
    assert (decided["n"], decided["targets"]) == (4, 1) and decided["null_rate"] == pytest.approx(13.8 / 45.0)
    assert decided["p_value_above_null"] == pytest.approx(binomial_upper_tail(1, 4, 13.8 / 45.0))
    assert s["middle"] == (START + timedelta(hours=500)).isoformat()
