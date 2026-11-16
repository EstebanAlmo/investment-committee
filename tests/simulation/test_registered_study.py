"""run_from: one strategy from a given start, with the bars before it used
only as history. Made-up bars and a scripted strategy."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from investment_committee.market_intelligence.quotes import QuoteKind
from investment_committee.simulation.forex_backtest import Strategy
from investment_committee.simulation.forex_backtest_study import FULL, SCENARIOS, write_study, yahoo_week
from investment_committee.simulation.registered_study import run_from
from investment_committee.simulation.signals import Outcome

from ._forex_backtest_support import HOUR, T0, Scripted, tape

START = T0 + 20 * HOUR  # nothing before this instant belongs to the test


def scripted(offsets: dict[int, Outcome], seen: list[tuple[datetime, int]]) -> Strategy:
    def evaluate(bars, as_of, *, symbol, data_kind, data_source, is_open):  # type: ignore[no-untyped-def]
        seen.append((as_of, len(bars)))
        offset = int((as_of - T0) / HOUR)
        return Scripted(symbol, offsets.get(offset, Outcome.NO_CROSS), as_of, bars[-1].close if bars else None, Decimal("0.00100"),
                        strategy_id="guion@0.0.0")

    return Strategy("guion@0.0.0", "9" * 64, evaluate, 30)


def study(offsets: dict[int, Outcome], seen: list[tuple[datetime, int]] | None = None):  # type: ignore[no-untyped-def]
    return run_from({"EURUSD": tape(last=60)}, scripted(offsets, [] if seen is None else seen), start=START, data_kind=QuoteKind.SYNTHETIC,
                    data_source="serie de prueba", calendar=yahoo_week)


def test_nothing_before_the_start_is_evaluated_opened_or_settled() -> None:
    seen: list[tuple[datetime, int]] = []
    result = study({10: Outcome.LONG, 25: Outcome.LONG}, seen)  # the signal at +10 is before the start: it must not exist
    assert min(moment for moment, _ in seen) == START and result.common_start == START == result.first_evaluable["guion@0.0.0"]
    assert seen[0][1] == 25  # ... yet the first evaluation was handed the 25 bars closed before it (labels -5 .. +19)
    run = result.runs[("guion@0.0.0", FULL, "BASE")]
    assert [t.signal_candle_close_at for t in run.trades] == [T0 + 25 * HOUR] and run.start == START
    assert all(d.at >= START for d in run.decisions) and all(point.at >= START for point in run.equity)


def test_one_account_per_scenario_and_a_study_the_writer_accepts(tmp_path) -> None:  # type: ignore[no-untyped-def]
    result = study({25: Outcome.LONG})
    assert [key for key in result.runs] == [("guion@0.0.0", FULL, scenario.name) for scenario in SCENARIOS]
    manifest = write_study(tmp_path / "estudio", result, commit="abc", dataset="d" * 64, dataset_description="prueba", calendar_description="c",
                           executed_at=T0)
    assert len(manifest["runs"]) == len(SCENARIOS) and (tmp_path / "estudio" / "guion@0.0.0" / FULL / "COSTOS_X1_5" / "run.json").is_file()


def test_a_start_outside_the_bars_is_refused() -> None:
    with pytest.raises(ValueError):
        run_from({"EURUSD": tape(last=60)}, scripted({}, []), start=T0 - 100 * HOUR, data_kind=QuoteKind.SYNTHETIC, data_source="x", calendar=yahoo_week)
    with pytest.raises(ValueError):
        run_from({}, scripted({}, []), start=START, data_kind=QuoteKind.SYNTHETIC, data_source="x", calendar=yahoo_week)
