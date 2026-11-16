"""Tests of simulation.forex_backtest_study: the fixed set of runs of the
A/B comparison and the files it writes. Made-up strategies and bars."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_committee.market_intelligence.quotes import QuoteKind
from investment_committee.simulation.forex_backtest import Strategy
from investment_committee.simulation.forex_backtest_metrics import canonical, content_sha256, dataset_sha256
from investment_committee.simulation.forex_backtest_study import COMMON, FULL, SCENARIOS, run_study, write_study, yahoo_week
from investment_committee.simulation.signals import Outcome

from ._forex_backtest_support import HOUR, T0, D, sig, tape

STOPPED = ("1.08495", "1.08505", "1.08357", "1.08400")
WON = ("1.08495", "1.08963", "1.08485", "1.08900")
WHEN = datetime(2026, 11, 12, 9, 30, tzinfo=timezone.utc)
CALLS: list[str] = []


def fake(name: str, warm_up: int, entries: dict[int, Outcome]) -> Strategy:
    """A strategy that rejects its data until T0 + warm_up hours and then
    signals at the given offsets."""

    def evaluate(bars, as_of, *, symbol, data_kind, data_source, is_open):  # type: ignore[no-untyped-def]
        CALLS.append(name)
        offset = int((as_of - T0) / HOUR)
        outcome = Outcome.DATA_REJECTED if offset < warm_up else entries.get(offset, Outcome.NO_CROSS)
        return sig(offset, outcome, symbol=symbol)

    return Strategy(f"{name}@0.0.0", name[0] * 64, evaluate, 5)


def study():  # type: ignore[no-untyped-def]
    a = fake("alfa", 0, {0: Outcome.LONG, 1: Outcome.LONG, 6: Outcome.LONG})
    b = fake("beta", 4, {6: Outcome.SHORT})
    bars = {"EURUSD": tape({0: STOPPED, 1: WON})}
    return bars, run_study(bars, strategies=(a, b), data_kind=QuoteKind.SYNTHETIC, data_source="serie sintética", calendar=lambda _m: True)


def test_the_study_is_every_strategy_in_both_periods_under_every_scenario() -> None:
    _, result = study()

    assert [s.name for s in SCENARIOS] == ["BASE", "COSTOS_X1_5", "COSTOS_X2", "FINANCIACION_ILUSTRATIVA_3_POR_CIENTO",
                                           "COMISION_HIPOTETICA_7_USD_POR_100K", "INDETERMINADAS_OPTIMISTAS"]
    assert set(result.runs) == {(s, p, c.name) for s in ("alfa@0.0.0", "beta@0.0.0") for p in (FULL, COMMON) for c in SCENARIOS}
    assert result.first_evaluable == {"alfa@0.0.0": T0, "beta@0.0.0": T0 + 4 * HOUR} and result.common_start == T0 + 4 * HOUR
    assert all(run.end == result.end == T0 + 111 * HOUR for run in result.runs.values())


def test_each_run_starts_where_its_period_says_with_a_fresh_account() -> None:
    _, result = study()
    full, common = result.runs[("alfa@0.0.0", FULL, "BASE")], result.runs[("alfa@0.0.0", COMMON, "BASE")]

    assert (full.start, common.start) == (T0, T0 + 4 * HOUR)
    assert [t.entry_at for t in full.trades] == [T0, T0 + HOUR, T0 + 6 * HOUR] and full.trades[2].proposal.equity == D("10097.28")
    assert [t.entry_at for t in common.trades] == [T0 + 6 * HOUR] and common.trades[0].proposal.equity == D("10000")
    assert content_sha256(result.runs[("beta@0.0.0", FULL, "BASE")]) == content_sha256(result.runs[("beta@0.0.0", COMMON, "BASE")])


def test_the_signals_are_computed_once_per_strategy_and_shared_by_every_scenario() -> None:
    CALLS.clear()
    _, result = study()

    assert CALLS.count("alfa") == CALLS.count("beta") == 116  # one evaluation per hour from the first bar's close to the last one's
    assert len(result.signals["alfa@0.0.0"]["EURUSD"]) == 116


def test_the_hypothetical_commission_is_seven_dollars_per_100k_for_the_whole_round_trip_charged_once() -> None:
    """32 000 units: 32 000 x 7 / 100 000 = 2.24 for entry and exit together. With it l = 0.00159 and the
    size is 31 000 units: 31 000 x 7 / 100 000 = 2.17."""
    _, result = study()
    trade = result.runs[("alfa@0.0.0", FULL, "COMISION_HIPOTETICA_7_USD_POR_100K")].trades[0]

    assert (trade.proposal.units, trade.result.commission_cost) == (31000, D("2.17"))
    assert trade.result.net_pnl == trade.result.gross_pnl - D("2.17")
    assert result.runs[("alfa@0.0.0", FULL, "BASE")].trades[0].result.commission_cost == D("0.00")


@pytest.mark.parametrize("moment, expected", [
    (datetime(2026, 7, 5, 21, 0, tzinfo=timezone.utc), False),  # Sunday 17:00 New York (summer): the week is open, Yahoo has no bar yet
    (datetime(2026, 7, 5, 22, 0, tzinfo=timezone.utc), False),
    (datetime(2026, 7, 5, 23, 0, tzinfo=timezone.utc), True),  # Sunday 19:00 New York
    (datetime(2026, 12, 6, 23, 0, tzinfo=timezone.utc), False),  # winter: Sunday 18:00 New York
    (datetime(2026, 12, 7, 0, 0, tzinfo=timezone.utc), True),
    (datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc), True),  # Friday 16:00 New York
    (datetime(2026, 7, 10, 21, 0, tzinfo=timezone.utc), False),  # Friday 17:00 New York: closed in both calendars
])
def test_yahoos_week_opens_two_hours_after_new_yorks_and_closes_with_it(moment: datetime, expected: bool) -> None:
    assert yahoo_week(moment) is expected


def rows(path) -> list[dict[str, str]]:  # type: ignore[no-untyped-def]
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))


def written(tmp_path):  # type: ignore[no-untyped-def]
    bars, result = study()
    manifest = write_study(tmp_path / "salida", result, commit="abc1234", dataset=dataset_sha256(bars), dataset_description="serie sintética",
                           calendar_description="siempre abierto", executed_at=WHEN)
    return result, manifest, tmp_path / "salida"


def test_every_run_is_written_with_its_record_trades_decisions_equity_and_report(tmp_path) -> None:  # type: ignore[no-untyped-def]
    result, manifest, out = written(tmp_path)

    for (strategy, period, scenario), run in result.runs.items():
        folder = out / strategy / period / scenario
        assert sorted(p.name for p in folder.iterdir()) == ["decisions.csv", "equity.csv", "record.json", "report.txt", "run.json", "trades.csv"]
        assert json.loads((folder / "run.json").read_text(encoding="utf-8")) == canonical(run)
        assert json.loads((folder / "record.json").read_text(encoding="utf-8"))["reproducible"]["results_sha256"] == content_sha256(run)
        assert len(rows(folder / "trades.csv")) == len(run.trades) and len(rows(folder / "decisions.csv")) == len(run.decisions)
        assert len(rows(folder / "equity.csv")) == len(run.equity)
    assert (out / "alfa@0.0.0" / "signals.csv").exists() and len(rows(out / "beta@0.0.0" / "signals.csv")) == 116
    assert json.loads((out / "manifest.json").read_text(encoding="utf-8")) == manifest


def test_a_trade_row_says_exactly_what_the_engine_recorded(tmp_path) -> None:  # type: ignore[no-untyped-def]
    result, _, out = written(tmp_path)
    run = result.runs[("alfa@0.0.0", FULL, "BASE")]
    table = rows(out / "alfa@0.0.0" / FULL / "BASE" / "trades.csv")

    for row, trade in zip(table, run.trades):
        p = trade.proposal
        assert (row["trade_id"], row["strategy_id"], row["scenario"], row["period"], row["symbol"]) == (p.proposal_id, "alfa@0.0.0", "BASE", FULL, "EURUSD")
        assert (row["direction"], row["status"], row["data_kind"]) == (trade.direction, trade.status.value, "SINTETICA")
        assert datetime.fromisoformat(row["entry_at"]) == trade.entry_at and datetime.fromisoformat(row["signal_candle_close_at"]) == trade.signal_candle_close_at
        assert (D(row["entry"]), D(row["stop"]), D(row["target"]), int(row["units"])) == (p.entry, p.stop, p.target, p.units)
        assert (D(row["bid"]), D(row["ask"]), D(row["equity_before"]), D(row["risk_budget"])) == (p.bid, p.ask, p.equity, p.risk_budget)
    first, second = table[0], table[1]
    assert (first["exit_reason"], first["exit_price"], first["net_pnl"], first["r_multiple"], first["booked_pnl"]) == ("STOP", "1.08350", "-48.64", "-1.0000", "-48.64")
    assert (first["exit_at"], first["gap"], first["stop_pips"], first["target_pips"], first["risk_fraction_of_equity"]) == ("2026-12-01T15:00:00+00:00", "False", "15.0", "45.6", "0.004864")
    assert (second["exit_reason"], second["net_pnl"], second["lots"], second["risk_fraction_of_equity"]) == ("OBJETIVO", "145.92", "0.32", "0.004888")  # 48.64 / 9 951.36
    assert table[2]["exit_reason"] == "TIEMPO" and table[2]["conservative_pnl"] == "" and table[2]["uncertainty_kind"] == ""


def test_the_manifest_names_everything_needed_to_repeat_the_study_and_nothing_private(tmp_path) -> None:  # type: ignore[no-untyped-def]
    result, manifest, out = written(tmp_path)

    assert (manifest["engine"], manifest["commit"], manifest["data_kind"]) == ("ema-forex-backtest@1.0.0", "abc1234", "SINTETICA")
    assert manifest["strategies"] == {"alfa@0.0.0": {"parameters_sha256": "a" * 64, "first_evaluable_at": "2026-12-01T14:00:00+00:00"},
                                      "beta@0.0.0": {"parameters_sha256": "b" * 64, "first_evaluable_at": "2026-12-01T18:00:00+00:00"}}
    assert manifest["common_start"] == "2026-12-01T18:00:00+00:00" and manifest["end"] == "2026-12-06T05:00:00+00:00"
    assert manifest["calendar"] == "siempre abierto" and manifest["commission_convention"].startswith("USD por 100 000 unidades, ida y vuelta")
    assert len(manifest["runs"]) == 24 and manifest["runs"]["alfa@0.0.0/COMPLETO/BASE"]["results_sha256"] == content_sha256(result.runs[("alfa@0.0.0", FULL, "BASE")])
    assert manifest["execution"]["executed_at"] == "2026-11-12T09:30:00+00:00"
    for path in out.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert b"\r" not in data and str(tmp_path).encode() not in data and str(tmp_path).replace("\\", "\\\\").encode() not in data


def test_the_same_study_written_twice_differs_only_in_when_it_ran(tmp_path) -> None:  # type: ignore[no-untyped-def]
    bars, result = study()
    options = dict(commit="abc1234", dataset=dataset_sha256(bars), dataset_description="serie sintética", calendar_description="siempre abierto")
    one = write_study(tmp_path / "uno", result, executed_at=WHEN, **options)  # type: ignore[arg-type]
    two = write_study(tmp_path / "dos", study()[1], executed_at=datetime(2027, 1, 1, tzinfo=timezone.utc), **options)  # type: ignore[arg-type]

    assert one["study_sha256"] == two["study_sha256"] and {k: v for k, v in one.items() if k != "execution"} == {k: v for k, v in two.items() if k != "execution"}
    for name in ("summary.csv", "alfa@0.0.0/COMPLETO/BASE/trades.csv", "beta@0.0.0/signals.csv", "alfa@0.0.0/COMUN/COSTOS_X2/run.json"):
        assert (tmp_path / "uno" / name).read_bytes() == (tmp_path / "dos" / name).read_bytes()


def test_the_summary_table_has_one_row_per_run_and_scope(tmp_path) -> None:  # type: ignore[no-untyped-def]
    _, _, out = written(tmp_path)
    table = rows(out / "summary.csv")

    assert len(table) == 24 * 2  # EURUSD and TOTAL
    row = next(r for r in table if (r["strategy_id"], r["period"], r["scenario"], r["scope"]) == ("alfa@0.0.0", FULL, "BASE", "TOTAL"))
    assert (row["signals"], row["opened"], row["closed_valid"], row["indeterminate"], row["open_at_end"]) == ("3", "3", "3", "0", "0")
    assert (row["n"], row["sample"], row["wins"], row["win_rate"], row["wilson_95_low"]) == ("3", "MUESTRA_INSUFICIENTE", "1", "0.3333", "")
    assert (row["net_pnl"], row["final_balance"], row["exits_STOP"], row["exits_OBJETIVO"], row["exits_TIEMPO"]) == ("92.66", "10092.66", "1", "1", "1")  # -48.64 + 145.92 - 4.62 (33 000 units after the win)


def test_a_study_refuses_an_existing_folder_rather_than_rewrite_results(tmp_path) -> None:  # type: ignore[no-untyped-def]
    _, _, out = written(tmp_path)
    with pytest.raises(FileExistsError):
        write_study(out, study()[1], commit="abc1234", dataset="x", dataset_description="y", calendar_description="z", executed_at=WHEN)


def test_a_run_without_trades_still_writes_the_full_header(tmp_path) -> None:  # type: ignore[no-untyped-def]
    _, _, out = written(tmp_path)
    text = (out / "beta@0.0.0" / FULL / "COSTOS_X2" / "trades.csv").read_text(encoding="utf-8")
    full = (out / "alfa@0.0.0" / FULL / "BASE" / "trades.csv").read_text(encoding="utf-8")

    assert text.splitlines()[0] == full.splitlines()[0] and "trade_id" in text
