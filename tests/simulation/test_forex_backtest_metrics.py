"""Tests of simulation.forex_backtest_metrics on one small story whose
every figure is computed by hand below. Made-up bars; no historical data.

THE STORY (EUR/USD, base costs; see _forex_backtest_support.py for the levels)
  #1 LONG at T0      stopped inside its entry bar        32 000 u   -48.64   -1.0000 R   1 h
  #2 LONG at T0+1    target inside its entry bar         32 000 u  +145.92   +3.0000 R   1 h
     balance 10 097.28 -> floor(50.4864 / 0.00152 / 1000) = 33 -> 33 000 units, estimated loss 50.16
  #3 LONG at T0+2    stop AND target inside its bar      33 000 u   INDETERMINATE: -50.16 or +150.48; booked -50.16
  #4 LONG at T0+3    next bar opens 1.08205: gap         33 000 u  -100.32   -2.0000 R   1 h   (fill 1.08198)
     balance 9 946.80 -> 32 000 units again
  #5 LONG at T0+5    leaves after 72 bars                32 000 u    -4.48   -0.0921 R  72 h   (fill 1.08488)
     SHORT at T0+6   rejected for two reasons: POSICION_ABIERTA, and LIMITE_PERDIDA_DIARIA (the day has lost
                     48.64 - 145.92 + 50.16 + 100.32 = 53.20, so 96.80 of the 150.00 is left; the open risk 48.64
                     plus this trade's 48.64 = 97.28 does not fit)

  valid closed: n = 4 (#1 #2 #4 #5); 1 win, 3 losses
    net            -48.64 + 145.92 - 100.32 - 4.48          = -7.52
    win rate       1 / 4                                    = 0.2500
    profit factor  145.92 / (48.64 + 100.32 + 4.48)         = 145.92 / 153.44 = 0.9510
    average loss   -153.44 / 3                              = -51.15
    expectancy     (-1 + 3 - 2 - 0.0921) / 4                = -0.0230 R
    average loss R (-1 - 2 - 0.0921) / 3                    = -1.0307
    duration       (1 + 1 + 1 + 72) / 4                     = 18.75 h
    spread         (32 + 32 + 33 + 32) thousand x 0.00010   = 12.90
    entry slippage 129 000 x 0.00002                        = 2.58
    exit slippage  (32 + 33 + 32) thousand x 0.00002        = 1.94   (the target pays none)
  account: 10 000 - 7.52 - 50.16 = 9 942.32 (-0.58 %); optimistic reading of the same trades: -7.52 + 150.48 = +142.96
  balance: 10 000 -> 9 951.36 -> 10 097.28 -> 10 047.12 -> 9 946.80 -> 9 942.32
    drawdown: from 10 097.28 to 9 942.32 = 154.96 = 1.53 % of the maximum
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_committee.simulation.forex_backtest import COSTS_X2, DATA_HOLE, OPTIMISTIC, SAME_BAR, BacktestRun
from investment_committee.simulation.forex_backtest_metrics import (
    INSUFFICIENT_SAMPLE,
    SUFFICIENT_SAMPLE,
    canonical,
    closed_stats,
    common_start,
    content_sha256,
    dataset_sha256,
    drawdown,
    first_evaluable_at,
    record,
    render,
    summarize,
    wilson_interval,
    write_record,
)
from investment_committee.simulation.signals import Outcome

from ._forex_backtest_support import GBP_MID, HOUR, T0, D, play, sig, tape

STOPPED = ("1.08495", "1.08505", "1.08357", "1.08400")
WON = ("1.08495", "1.08963", "1.08485", "1.08900")
BOTH = ("1.08495", "1.08963", "1.08357", "1.08495")
GAPPED = ("1.08205", "1.08215", "1.08195", "1.08205")
WHEN = datetime(2026, 11, 12, 9, 30, tzinfo=timezone.utc)


def story(**options) -> BacktestRun:  # type: ignore[no-untyped-def]
    signals = [sig(0), sig(1), sig(2), sig(3), sig(5), sig(6, Outcome.SHORT)]
    return play(signals, tape({0: STOPPED, 1: WON, 2: BOTH, 4: GAPPED}), **options)


def test_the_story_itself_is_what_the_table_says() -> None:
    run = story()

    assert [(t.status.value, t.proposal.units, t.booked_pnl) for t in run.trades] == [
        ("CERRADA", 32000, D("-48.64")), ("CERRADA", 32000, D("145.92")), ("INDETERMINADA", 33000, D("-50.16")),
        ("CERRADA", 33000, D("-100.32")), ("CERRADA", 32000, D("-4.48"))]
    assert [t.result.r_multiple for t in run.trades if t.result] == [D("-1.0000"), D("3.0000"), D("-2.0000"), D("-0.0921")]
    assert run.final_balance == D("9942.32")


def test_closed_trade_metrics_use_only_valid_closed_trades() -> None:
    stats = summarize(story()).closed["TOTAL"]

    assert (stats.n, stats.sample, stats.wins, stats.losses, stats.flat) == (4, INSUFFICIENT_SAMPLE, 1, 3, 0)
    assert (stats.win_rate, stats.win_rate_interval_95) == (D("0.2500"), None)
    assert (stats.net_pnl, stats.gross_profit, stats.gross_loss) == (D("-7.52"), D("145.92"), D("153.44"))
    assert (stats.average_win, stats.average_loss) == (D("145.92"), D("-51.15"))
    assert (stats.average_win_r, stats.average_loss_r, stats.expectancy_r) == (D("3.0000"), D("-1.0307"), D("-0.0230"))
    assert stats.profit_factor == D("0.9510")
    assert stats.exits == {"OBJETIVO": 1, "STOP": 2, "TIEMPO": 1, "CIERRE_SEMANAL": 0} and stats.gaps == 1
    assert stats.average_hours == D("18.75")
    assert stats.r_distribution == {"< -1": 1, "[-1, 0)": 2, "[0, 1)": 0, "[1, 2)": 0, "[2, 3)": 0, ">= 3": 1}
    assert stats.costs == {"spread_informativo": D("12.90"), "slippage_entrada_informativo": D("2.58"), "slippage_salida_informativo": D("1.94"),
                           "comision": D("0.00"), "financiacion": D("0.00")}
    assert stats.concentration == {1: D("1.0000"), 3: D("1.0000"), 5: D("1.0000")}


def test_the_funnel_counts_signals_rejections_and_what_became_of_each_trade() -> None:
    summary = summarize(story())
    funnel = summary.funnel["TOTAL"]

    assert (funnel.signals, funnel.rejected, funnel.rejected_position_open, funnel.rejections) == (6, 1, 1, {"LIMITE_PERDIDA_DIARIA": 1, "POSICION_ABIERTA": 1})
    assert (funnel.opened, funnel.closed_valid, funnel.indeterminate, funnel.open_at_end) == (5, 4, 1, 0)
    assert summary.funnel["EURUSD"] == funnel and summary.closed["EURUSD"] == summary.closed["TOTAL"]
    assert summary.coverage == {"EURUSD": {"LONG": 5, "SHORT": 1}}


def test_indeterminate_trades_are_reported_apart_with_both_readings() -> None:
    summary = summarize(story())

    assert summary.indeterminate_by_kind == {SAME_BAR: 1}
    assert (summary.indeterminate_conservative, summary.indeterminate_optimistic) == (D("-50.16"), D("150.48"))
    assert (summary.conservative_pnl, summary.optimistic_pnl) == (D("-57.68"), D("142.96"))
    assert summary.final_balance - summary.initial_capital == summary.conservative_pnl  # the account is the conservative reading


def test_account_return_and_drawdown() -> None:
    summary = summarize(story())

    assert (summary.initial_capital, summary.final_balance, summary.return_fraction) == (D("10000"), D("9942.32"), D("-0.0058"))
    assert (summary.final_equity, summary.open_unrealized, summary.stale_valuations) == (D("9942.32"), D("0"), 0)
    worst = summary.drawdown_balance
    assert (worst.amount, worst.fraction, worst.peak) == (D("154.96"), D("0.0153"), D("10097.28"))
    assert (worst.peak_at, worst.trough_at) == (T0 + 2 * HOUR, T0 + 77 * HOUR)
    assert summary.drawdown_equity.fraction >= worst.fraction  # open positions only add falls here


def test_drawdown_is_the_largest_fall_from_a_previous_maximum() -> None:
    """100 -> 120 -> 90 (30 = 25 %) -> 130 -> 104 (26 = 20 %): the first one."""
    points = [(T0 + n * HOUR, D(v)) for n, v in enumerate(["100", "120", "90", "130", "104"])]
    worst = drawdown(points)

    assert (worst.fraction, worst.amount, worst.peak, worst.peak_at, worst.trough_at) == (D("0.2500"), D("30"), D("120"), T0 + HOUR, T0 + 2 * HOUR)
    assert drawdown([(T0, D("100")), (T0 + HOUR, D("110"))]).fraction == D("0.0000") and drawdown([]).amount == D("0")


def test_monthly_results_keep_valid_and_indeterminate_trades_in_separate_columns() -> None:
    month = summarize(story()).monthly

    assert list(month) == ["2026-12"]
    m = month["2026-12"]
    assert (m.closed_valid, m.wins, m.net_pnl, m.indeterminate, m.indeterminate_conservative, m.indeterminate_optimistic) == (
        4, 1, D("-7.52"), 1, D("-50.16"), D("150.48"))


def test_a_trade_is_counted_in_the_month_the_account_booked_it() -> None:
    late = datetime(2026, 11, 30, 14, 0, tzinfo=timezone.utc)  # a Monday; 72 bars end on Thursday 3 December
    run = play([sig(0, origin=late)], tape(origin=late), origin=late)

    assert list(summarize(run).monthly) == ["2026-12"]


def test_with_no_trades_nothing_is_divided_and_undefined_figures_are_none() -> None:
    summary = summarize(play([], tape(), last=10))
    stats = summary.closed["TOTAL"]

    assert (stats.n, stats.sample, stats.win_rate, stats.expectancy_r, stats.profit_factor, stats.average_win, stats.average_hours) == (
        0, INSUFFICIENT_SAMPLE, None, None, None, None, None)
    assert stats.concentration == {1: None, 3: None, 5: None} and summary.return_fraction == D("0.0000")
    assert "no definido" in render(summary)


def test_without_losses_the_profit_factor_is_undefined_not_infinite() -> None:
    stats = summarize(play([sig(0)], tape({0: WON}))).closed["TOTAL"]

    assert (stats.n, stats.wins, stats.profit_factor, stats.win_rate, stats.average_loss) == (1, 1, None, D("1.0000"), None)


def test_open_and_indeterminate_trades_never_enter_the_closed_statistics() -> None:
    hole = play([sig(0)], tape(missing=[2]), last=10)
    still_open = play([sig(0)], tape(), last=10)

    for run, kind in ((hole, {DATA_HOLE: 1}), (still_open, {})):
        summary = summarize(run)
        assert summary.closed["TOTAL"].n == 0 and summary.closed["TOTAL"].net_pnl == D("0") and summary.indeterminate_by_kind == kind
    assert summarize(still_open).funnel["TOTAL"].open_at_end == 1 and summarize(still_open).open_unrealized == D("-3.84")
    assert summarize(still_open).final_equity == D("9996.16") and summarize(still_open).final_balance == D("10000")


def test_stale_valuations_are_counted() -> None:
    assert summarize(play([sig(0)], tape(last=1), last=6)).stale_valuations == 4


# --- sample size ---


@pytest.mark.parametrize("wins, n", [(10, 40), (0, 30), (30, 30), (17, 53)])
def test_the_wilson_interval_matches_an_independent_calculation(wins: int, n: int) -> None:
    z, p = 1.959964, wins / n
    centre, half = p + z * z / (2 * n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    low, high = wilson_interval(wins, n)

    assert float(low) == pytest.approx((centre - half) / (1 + z * z / n), abs=6e-5)
    assert float(high) == pytest.approx((centre + half) / (1 + z * z / n), abs=6e-5)
    assert 0 <= low <= Decimal(wins) / n <= high <= 1


def test_ten_wins_in_forty_is_between_14_and_40_per_cent() -> None:
    assert wilson_interval(10, 40) == (D("0.1419"), D("0.4019"))


def test_from_thirty_closed_trades_on_the_win_rate_carries_its_interval() -> None:
    loser, winner = story().trades[0], story().trades[1]
    few, enough = closed_stats([loser] * 22 + [winner] * 7), closed_stats([loser] * 30 + [winner] * 10)

    assert (few.n, few.sample, few.win_rate_interval_95) == (29, INSUFFICIENT_SAMPLE, None) and few.win_rate == D("0.2414")
    assert (enough.n, enough.sample, enough.win_rate, enough.win_rate_interval_95) == (40, SUFFICIENT_SAMPLE, D("0.2500"), (D("0.1419"), D("0.4019")))
    assert enough.expectancy_r == D("0.0000") and enough.profit_factor == D("1.0000")  # 10 x 3 R against 30 x 1 R: a negative result is not hidden either
    assert closed_stats([loser] * 30).win_rate_interval_95 == (D("0.0000"), D("0.1135"))


def test_concentration_is_the_share_of_the_wins_that_comes_from_the_best_trades() -> None:
    """Wins 145.92 x 4 = 583.68: the best one is a quarter, the best three are three quarters."""
    stats = closed_stats([story().trades[1]] * 4 + [story().trades[0]])

    assert stats.concentration == {1: D("0.2500"), 3: D("0.7500"), 5: D("1.0000")}


# --- two instruments, report, comparison period ---


def test_each_instrument_has_its_own_statistics_and_the_total_adds_them() -> None:
    signals = [sig(0), sig(0, symbol="GBPUSD")]
    run = play(signals, {"EURUSD": tape({0: STOPPED}), "GBPUSD": tape(mid=GBP_MID)}, last=90)
    summary = summarize(run)

    assert list(summary.closed) == ["EURUSD", "GBPUSD", "TOTAL"] and list(summary.funnel) == ["EURUSD", "GBPUSD", "TOTAL"]
    assert (summary.closed["EURUSD"].n, summary.closed["GBPUSD"].n, summary.closed["TOTAL"].n) == (1, 1, 2)
    assert summary.closed["EURUSD"].exits["STOP"] == 1 and summary.closed["GBPUSD"].exits["TIEMPO"] == 1
    assert summary.closed["TOTAL"].net_pnl == summary.closed["EURUSD"].net_pnl + summary.closed["GBPUSD"].net_pnl


def test_the_report_always_shows_the_sample_the_warnings_and_both_readings() -> None:
    text = render(summarize(story()))

    for expected in ("n = 4 (MUESTRA_INSUFICIENTE)", "ESTIMADO", "ninguna estrategia ganadora".replace("ninguna ", "una "), "subestimar",
                     "porcentaje de aciertos 25.00 %", "profit factor 0.9510", "expectativa -0.0230 R", "-7.52 USD", "9942.32 USD (-0.58 %)",
                     "conservador -57.68 USD", "142.96 USD", "por posición abierta 1", "drawdown sobre saldo realizado 1.53 % (154.96 USD)",
                     "2026-12: válidas n = 4", "CONSERVADORA"):
        assert expected in text, expected
    assert "rentabilidad neta real" in text and text.endswith("\n")


def test_the_common_period_starts_where_the_slower_strategy_can_first_evaluate() -> None:
    def evaluations(first_good: int) -> dict:  # type: ignore[type-arg]
        return {"EURUSD": {T0 + n * HOUR: sig(n, Outcome.NO_CROSS if n >= first_good else Outcome.DATA_REJECTED) for n in range(10)}}

    a, b = evaluations(2), evaluations(6)

    assert (first_evaluable_at(a), first_evaluable_at(b)) == (T0 + 2 * HOUR, T0 + 6 * HOUR)
    assert common_start(a, b) == common_start(b, a) == T0 + 6 * HOUR
    assert first_evaluable_at(evaluations(99)) is None and common_start(a, evaluations(99)) is None


def test_restarting_a_strategy_at_the_common_start_gives_it_a_fresh_account() -> None:
    """From T0 + 3h on, the story has only #4 and #5 - and #4 is sized on a
    fresh 10 000 (32 000 units, gap -97.28), not on what came before."""
    restarted = story(first=3)

    assert [(t.entry_at, t.proposal.equity, t.proposal.units, t.booked_pnl) for t in restarted.trades] == [
        (T0 + 3 * HOUR, D("10000"), 32000, D("-97.28")), (T0 + 5 * HOUR, D("9902.72"), 32000, D("-4.48"))]
    assert summarize(restarted).funnel["TOTAL"].signals == 3 and story().final_balance == D("9942.32")


# --- reproducibility ---

STORY_SHA256 = "0c7cc93cb0e5b774ec49bcdc758692ff0974225738e6677b759e6517aced9c91"  # a literal: the same on Windows and macOS


def test_the_same_inputs_give_the_same_hash_and_any_difference_changes_it() -> None:
    assert content_sha256(story()) == content_sha256(story()) == STORY_SHA256
    assert len({content_sha256(story()), content_sha256(story(scenario=COSTS_X2)), content_sha256(story(scenario=OPTIMISTIC)),
                content_sha256(story(last=99))}) == 4


def test_the_canonical_form_spells_every_value_one_way() -> None:
    plain = canonical(story())

    assert json.loads(json.dumps(plain)) == plain
    assert plain["final_balance"] == "9942.32" and plain["start"] == "2026-12-01T10:00:00+00:00" and plain["trades"][0]["status"] == "CERRADA" and type(plain["trades"][0]["status"]) is str
    assert plain["scenario"]["cost_multiple"] == "1" and plain["trades"][2]["exit_at"] is None
    with pytest.raises(TypeError):
        canonical(object())


def test_the_dataset_hash_depends_on_the_bars_and_not_on_their_order() -> None:
    bars = tape()

    assert dataset_sha256({"EURUSD": bars}) == dataset_sha256({"EURUSD": bars[::-1]})
    assert dataset_sha256({"EURUSD": bars}) != dataset_sha256({"EURUSD": tape({0: STOPPED})})
    assert dataset_sha256({"EURUSD": bars}) != dataset_sha256({"GBPUSD": bars})


def entry(executed_at: datetime = WHEN, **options):  # type: ignore[no-untyped-def]
    return record(story(**options), commit="abc1234", dataset=dataset_sha256({"EURUSD": tape()}), dataset_description="serie sintética", executed_at=executed_at)


def test_the_record_separates_what_is_reproducible_from_when_and_where_it_ran() -> None:
    first, later = entry(), entry(datetime(2027, 1, 1, tzinfo=timezone.utc))
    r = first["reproducible"]

    assert first["reproducible"] == later["reproducible"] and first["reproducible_sha256"] == later["reproducible_sha256"]
    assert first["execution"]["executed_at"] != later["execution"]["executed_at"]
    assert set(first["execution"]) == {"executed_at", "operating_system", "python", "note"}
    assert (r["engine"], r["commit"], r["strategy_id"], r["results_sha256"]) == ("ema-forex-backtest@1.0.0", "abc1234", "prueba@0.0.0", STORY_SHA256)
    assert r["period"] == {"start": "2026-12-01T10:00:00+00:00", "end": "2026-12-05T18:00:00+00:00"}
    assert r["instrument_rules"]["EURUSD"]["assumed_spread"] == "0.00010" and r["execution_parameters"]["commission_per_100k"] == "0"
    assert r["risk_limits"]["capital"] == "10000" and r["scenario"]["financing"]["annual_rate"] is None
    assert r["uncertainty_policy"]["account_books"] == "CONSERVADORA" and r["uncertainty_policy"]["valid_for_metrics"] is False
    assert r["summary"]["closed"]["TOTAL"]["profit_factor"] == "0.9510"
    assert entry(scenario=COSTS_X2)["reproducible_sha256"] != first["reproducible_sha256"]
    with pytest.raises(ValueError):
        entry(datetime(2026, 1, 1))


def test_the_record_is_written_with_the_same_bytes_everywhere(tmp_path) -> None:  # type: ignore[no-untyped-def]
    run = story()
    written = write_record(tmp_path / "backtests" / "prueba", entry(), run)

    assert [p.name for p in written] == ["record.json", "run.json", "report.txt"]
    for path in written:
        data = path.read_bytes()
        assert data.endswith(b"\n") and b"\r" not in data
    assert json.loads(written[0].read_text(encoding="utf-8")) == entry()
    assert json.loads(written[1].read_text(encoding="utf-8")) == canonical(run)
    assert written[2].read_text(encoding="utf-8") == render(summarize(run))
    assert write_record(tmp_path / "otra", entry(datetime(2027, 1, 1, tzinfo=timezone.utc)), run)[1].read_bytes() == written[1].read_bytes()
