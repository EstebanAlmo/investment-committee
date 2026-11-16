"""The viewer against the real B4.3 study, when it is on this machine
(data/reports is git-ignored, so elsewhere these tests skip themselves).
Read-only: nothing is run again and no file of the study is touched."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from investment_committee.interfaces.trade_viewer.study_store import RunKey, check_signals, find_snapshot, load_run, load_signals, load_study
from investment_committee.interfaces.trade_viewer.trade_view import INSIDE_BAR, build_trade_view
from investment_committee.market_intelligence.quotes import QuoteKind
from investment_committee.simulation import ema_cross, ema_trend_cross
from investment_committee.simulation.forex_backtest_study import yahoo_week

ROOT = Path(__file__).resolve().parents[3]
STUDY = ROOT / "data" / "reports" / "backtests" / "fx_1h_20260331_20260930_107957e7_e6cf138"
HOUR = timedelta(hours=1)
A, B = ema_cross.STRATEGY_ID, ema_trend_cross.STRATEGY_ID

pytestmark = pytest.mark.skipif(not (STUDY / "manifest.json").exists(), reason="el estudio B4.3 no está en esta máquina")


@pytest.fixture(scope="module")
def study():  # type: ignore[no-untyped-def]
    snapshot = find_snapshot(STUDY, ROOT / "data" / "raw" / "snapshots")
    if snapshot is None:
        pytest.skip("el snapshot del estudio no está en esta máquina")
    return load_study(STUDY, snapshot)


def test_the_real_study_checks_out_and_holds_the_reported_results(study) -> None:  # type: ignore[no-untyped-def]
    a, b = load_run(study, RunKey(A, "COMUN", "BASE")), load_run(study, RunKey(B, "COMUN", "BASE"))

    assert study.study_sha256.startswith("a34b6843") and study.manifest["data_kind"] == "ESTIMADA" and len(study.keys) == 24
    assert (len(a.trades), a.data["final_balance"], len(b.trades), b.data["final_balance"]) == (60, "8856.23", 112, "9144.02")


@pytest.mark.parametrize("strategy", [A, B])
def test_every_real_trade_can_be_drawn_from_the_records_alone(study, strategy) -> None:  # type: ignore[no-untyped-def]
    signals = load_signals(study, strategy)
    for period in ("COMPLETO", "COMUN"):
        run = load_run(study, RunKey(strategy, period, "BASE"))
        check_signals(run, signals)
        for trade in run.trades:
            p, v = trade["proposal"], build_trade_view(study, run, signals, trade["proposal"]["proposal_id"])
            assert (str(v.entry), str(v.stop), str(v.target), v.units, v.direction) == (p["entry"], p["stop"], p["target"], p["units"], trade["direction"])
            assert v.bars[v.entry_index].timestamp == v.entry_at and v.bars[v.entry_index].open == (Decimal(p["bid"]) + Decimal(p["ask"])) / 2
            assert v.signal_index is not None and v.bars[v.signal_index].close == Decimal(v.signal_row["last_close"])  # the signal candle's own close
            assert v.entry_index >= 50 and len(v.bars) - 1 - v.last_index <= 20
            assert all(len(values) == len(v.bars) for values in v.indicators.values()) and v.indicators
            assert all(values[v.signal_index] is not None for values in v.indicators.values())
            if v.exit_reason in ("STOP", "OBJETIVO") and v.exit_timing == INSIDE_BAR:
                bar, level = v.bars[v.exit_index], v.stop_trigger_mid if v.exit_reason == "STOP" else v.target_trigger_mid
                assert bar.low <= level <= bar.high  # the candle drawn as the exit really reaches the level drawn on the mid price


@pytest.mark.parametrize("strategy, evaluate, hours", [(A, ema_cross.evaluate, 600), (B, ema_trend_cross.evaluate, 1440)])
def test_the_recorded_indicators_are_what_the_strategy_computes_from_the_snapshot(study, strategy, evaluate, hours) -> None:  # type: ignore[no-untyped-def]
    """signals.csv has no hash in the manifest; this recomputes, with the
    strategy's own function and only bars closed by then, the evaluations
    behind a few trades and compares every recorded number."""
    signals, run = load_signals(study, strategy), load_run(study, RunKey(strategy, "COMUN", "BASE"))
    for trade in list(run.trades)[::25]:
        moment, symbol = build_trade_view(study, run, signals, trade["proposal"]["proposal_id"]).signal_candle_close_at, trade["symbol"]
        bars = [b for b in study.bars[symbol] if moment - hours * HOUR <= b.timestamp < moment]
        again = evaluate(bars, moment, symbol=symbol, data_kind=QuoteKind.ESTIMATED, data_source="x", is_open=yahoo_week)
        row = signals[symbol][moment]
        assert again.outcome.value == row["outcome"] == trade["direction"]
        for column in [c for c in row if c.startswith(("ema_", "previous_ema_")) or c in ("rsi", "atr", "last_close")]:
            assert str(getattr(again, column)) == row[column], column
