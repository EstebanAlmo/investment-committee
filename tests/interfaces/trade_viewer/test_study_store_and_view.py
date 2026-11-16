"""The viewer's read-only store and its picture of a trade, against what the
engine recorded. Synthetic study (conftest.py); standard library only."""

from __future__ import annotations

import ast
import hashlib
import shutil
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from investment_committee.interfaces.trade_viewer import study_store, trade_view
from investment_committee.interfaces.trade_viewer.study_store import RunKey, StudyError, check_signals, find_snapshot, load_run, load_signals, load_study
from investment_committee.interfaces.trade_viewer.trade_view import (
    AT_OPEN,
    DERIVED,
    ESTIMATED,
    INSIDE_BAR,
    OBSERVED,
    build_trade_view,
    compare_interval,
    lots,
    pips,
    show_time,
    table_row,
)

from tests.simulation._forex_backtest_support import HOUR, MID, T0

D = Decimal
ALFA, BETA = "alfa@0.0.0", "beta@0.0.0"
BASE = RunKey(ALFA, "COMPLETO", "BASE")


def tid(offset: int, symbol: str = "EURUSD=X", strategy: str = ALFA) -> str:
    return f"{strategy}|{symbol}|{(T0 + offset * HOUR).isoformat()}"


@pytest.fixture(scope="module")
def loaded(study_paths):  # type: ignore[no-untyped-def]
    study = load_study(*study_paths)
    run, signals = load_run(study, BASE), load_signals(study, ALFA)
    check_signals(run, signals)
    return study, run, signals


def view(loaded, offset: int, symbol: str = "EURUSD=X"):  # type: ignore[no-untyped-def]
    return build_trade_view(*loaded, tid(offset, symbol))


def digest(folder: Path) -> dict[str, str]:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.rglob("*")) if p.is_file()}


# --- loading and integrity ---


def test_a_study_loads_with_its_runs_and_its_bars_in_utc(loaded) -> None:  # type: ignore[no-untyped-def]
    study, run, _ = loaded

    assert study.name == "estudio_sintetico" and study.strategies == [ALFA, BETA] and len(study.keys) == 24
    assert study.options("period") == ["COMPLETO", "COMUN"] and "COMISION_HIPOTETICA_7_USD_POR_100K" in study.options("scenario")
    assert set(study.bars) == {"EURUSD", "GBPUSD"} and all(b.timestamp.tzinfo is timezone.utc for b in study.bars["EURUSD"])
    assert len(run.trades) == 8 and len(run.decisions) == 10 and run.summary["final_balance"] == run.data["final_balance"]


def test_loading_and_viewing_change_no_byte_of_the_study_or_the_snapshot(study_paths) -> None:  # type: ignore[no-untyped-def]
    before = [digest(folder) for folder in study_paths]
    study = load_study(*study_paths)
    for key in study.keys:
        run, signals = load_run(study, key), load_signals(study, key.strategy_id)
        check_signals(run, signals)
        for trade in run.trades:
            build_trade_view(study, run, signals, trade["proposal"]["proposal_id"])

    assert [digest(folder) for folder in study_paths] == before


def tampered(study_paths, tmp_path, relative: str, old: str, new: str):  # type: ignore[no-untyped-def]
    copy, snapshot = tmp_path / "copia", tmp_path / "snap"
    shutil.copytree(study_paths[0], copy)
    shutil.copytree(study_paths[1], snapshot)
    target = (copy if not relative.startswith("SNAP/") else snapshot).joinpath(*relative.removeprefix("SNAP/").split("/"))
    text = target.read_text(encoding="utf-8")
    assert old in text
    target.write_bytes(text.replace(old, new, 1).encode("utf-8"))
    return copy, snapshot


def test_a_changed_manifest_is_refused(study_paths, tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(StudyError, match="manifest"):
        load_study(*tampered(study_paths, tmp_path, "manifest.json", '"commit": "abc1234"', '"commit": "abc9999"'))


def test_a_snapshot_that_is_not_the_studys_dataset_is_refused(study_paths, tmp_path, short_study_paths) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(StudyError, match="dataset"):
        load_study(study_paths[0], short_study_paths[1])  # a valid snapshot, but another one
    with pytest.raises(StudyError, match="snapshot"):
        load_study(*tampered(study_paths, tmp_path, "SNAP/EURUSD_X_1h.csv", "1.08357", "1.08358"))  # its own hash no longer matches
    with pytest.raises(StudyError):
        load_study(study_paths[0], tmp_path / "no_existe")


def test_a_changed_result_is_refused(study_paths, tmp_path) -> None:  # type: ignore[no-untyped-def]
    copy, snapshot = tampered(study_paths, tmp_path, f"{ALFA}/COMPLETO/BASE/run.json", '"net_pnl": "-48.64"', '"net_pnl": "48.64"')
    study = load_study(copy, snapshot)

    with pytest.raises(StudyError, match="run.json"):
        load_run(study, BASE)
    assert load_run(study, RunKey(ALFA, "COMUN", "BASE")).trades  # the other runs are still good


def test_signals_that_disagree_with_the_run_are_refused(study_paths, tmp_path, loaded) -> None:  # type: ignore[no-untyped-def]
    copy, snapshot = tampered(study_paths, tmp_path, f"{ALFA}/signals.csv", "2026-12-01T14:00:00+00:00,EURUSD=X,0.00100", "2026-12-01T14:00:00+00:00,EURUSD=X,0.00200")
    study = load_study(copy, snapshot)

    with pytest.raises(StudyError, match="signals.csv"):
        check_signals(load_run(study, BASE), load_signals(study, ALFA))
    with pytest.raises(StudyError):
        build_trade_view(study, load_run(study, BASE), load_signals(study, ALFA), tid(0))
    with pytest.raises(StudyError):
        loaded[1].trade("no-existe")


def test_the_snapshot_is_found_by_the_name_the_manifest_gives(study_paths, tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert find_snapshot(study_paths[0], study_paths[1].parent) == study_paths[1]
    assert find_snapshot(study_paths[0], tmp_path) is None


# --- the picture of a trade is the engine's record ---


def test_every_trade_of_every_run_is_drawn_with_exactly_the_numbers_recorded(study_paths) -> None:  # type: ignore[no-untyped-def]
    study = load_study(*study_paths)
    seen = 0
    for key in study.keys:
        run, signals = load_run(study, key), load_signals(study, key.strategy_id)
        for trade in run.trades:
            p, v = trade["proposal"], build_trade_view(study, run, signals, trade["proposal"]["proposal_id"])
            assert (v.direction, v.status, v.units, v.symbol) == (trade["direction"], trade["status"], p["units"], trade["symbol"])
            assert (str(v.entry), str(v.stop), str(v.target)) == (p["entry"], p["stop"], p["target"])
            assert (v.entry_at.isoformat(), v.signal_candle_close_at.isoformat()) == (trade["entry_at"], trade["signal_candle_close_at"])
            assert v.bars[v.entry_index].timestamp == v.entry_at  # the entry is drawn on the bar whose open it is
            assert v.signal_index is None or v.bars[v.signal_index].timestamp + HOUR == v.signal_candle_close_at
            assert (v.bars[v.entry_index].open - (D(p["bid"]) + D(p["ask"])) / 2) == 0  # the quote was that bar's open
            if trade["result"]:
                assert (str(v.exit_price), str(v.net_pnl), str(v.r_multiple)) == (trade["result"]["exit_price"], trade["result"]["net_pnl"], trade["result"]["r_multiple"])
                assert v.exit_at.isoformat() == trade["exit_at"] and v.exit_reason == trade["result"]["reason"]
            else:
                assert v.exit_at is None and v.exit_price is None and v.net_pnl is None and v.exit_index is None
            seen += 1
    assert seen > 60


def test_a_long_stopped_inside_a_bar(loaded) -> None:  # type: ignore[no-untyped-def]
    v = view(loaded, 0)

    assert (v.direction, v.outcome, v.exit_reason, v.exit_timing) == ("LONG", "PERDEDORA", "STOP", INSIDE_BAR)
    assert (v.entry, v.stop, v.target, v.exit_price, v.units, v.net_pnl) == (D("1.08502"), D("1.08352"), D("1.08958"), D("1.08350"), 32000, D("-48.64"))
    assert (v.stop_pips, v.target_pips, v.reward_risk_gross) == (D("15.0"), D("45.6"), D("3.04"))
    assert (v.stop_trigger_mid, v.target_trigger_mid) == (D("1.08357"), D("1.08963"))  # on the candles: half a spread above the levels
    assert v.bars[v.exit_index].timestamp == T0 + 3 * HOUR and v.bars[v.exit_index].low == v.stop_trigger_mid  # the bar that touched it
    assert v.exit_at == T0 + 4 * HOUR  # the engine dates it at that bar's close; the instant inside is unknown


def test_a_short_that_reached_its_target(loaded) -> None:  # type: ignore[no-untyped-def]
    v = view(loaded, 5)

    assert (v.direction, v.outcome, v.exit_reason, v.exit_timing, v.net_pnl) == ("SHORT", "GANADORA", "OBJETIVO", INSIDE_BAR, D("145.92"))
    assert (v.entry, v.stop, v.target) == (D("1.08488"), D("1.08638"), D("1.08032"))
    assert (v.stop_trigger_mid, v.target_trigger_mid) == (D("1.08633"), D("1.08027"))  # a short's levels sit half a spread below on the candles
    assert v.bars[v.exit_index].low == D("1.08027")


def test_exits_at_an_opening_are_marked_as_such(loaded) -> None:  # type: ignore[no-untyped-def]
    gap, target_gap, timed, weekly = view(loaded, 36), view(loaded, 60), view(loaded, 1, "GBPUSD=X"), view(loaded, 66)

    assert (gap.exit_reason, gap.exit_timing, gap.net_pnl, gap.trade["result"]["gap"]) == ("STOP", AT_OPEN, D("-97.28"), True)
    assert gap.bars[gap.exit_index].timestamp == gap.exit_at == T0 + 38 * HOUR and gap.bars[gap.exit_index].open == D("1.08205")
    assert (target_gap.exit_reason, target_gap.exit_timing, target_gap.exit_price) == ("OBJETIVO", AT_OPEN, D("1.08958"))
    assert target_gap.bars[target_gap.exit_index].timestamp == T0 + 62 * HOUR
    assert (timed.exit_reason, timed.exit_timing, timed.exit_at) == ("TIEMPO", AT_OPEN, T0 + 73 * HOUR)
    assert (weekly.exit_reason, weekly.exit_timing, weekly.exit_at) == ("CIERRE_SEMANAL", AT_OPEN, datetime(2026, 12, 4, 21, 0, tzinfo=timezone.utc))


def test_an_indeterminate_trade_has_no_exit_and_carries_both_readings(loaded) -> None:  # type: ignore[no-untyped-def]
    same_bar, hole = view(loaded, 10), view(loaded, 13)

    for v in (same_bar, hole):
        assert (v.outcome, v.exit_at, v.exit_price, v.exit_index, v.exit_timing, v.net_pnl) == ("INDETERMINADA", None, None, None, None, None)
        assert (v.conservative["net_pnl"], v.optimistic["net_pnl"]) == ("-50.16", "150.48") and v.trade["booked_pnl"] == "-50.16"
    assert same_bar.uncertainty["kind"] == "STOP_Y_OBJETIVO_EN_LA_MISMA_VELA"
    assert same_bar.uncertainty_indexes == (same_bar.entry_index + 1, same_bar.entry_index + 1)  # the one bar that held both levels
    assert hole.uncertainty["kind"] == "HUECO_DE_DATOS" and hole.uncertainty["unobserved_hours"] == [(T0 + 15 * HOUR).isoformat()]
    low, high = hole.uncertainty_indexes
    assert (hole.bars[low].timestamp, hole.bars[high].timestamp) == (T0 + 14 * HOUR, T0 + 16 * HOUR)  # the bars on both sides of the hole
    assert any("INDETERMINADO" in f.value for f in hole.sheet) and any("conservadora" in f.label for f in hole.sheet)


def test_a_position_open_at_the_end_has_no_result(short_study_paths) -> None:  # type: ignore[no-untyped-def]
    study = load_study(*short_study_paths)
    run, signals = load_run(study, BASE), load_signals(study, ALFA)
    v = build_trade_view(study, run, signals, tid(1, "GBPUSD=X"))

    assert (v.status, v.outcome, v.exit_at, v.net_pnl, v.exit_index) == ("ABIERTA_AL_FINAL", "ABIERTA_AL_FINAL", None, None, None)
    assert v.last_index == len(v.bars) - 1 and any("abierta al terminar" in f.value for f in v.sheet)


# --- window, holes, indicators ---


def test_the_window_is_50_bars_before_every_bar_of_the_trade_and_20_after_when_they_exist(loaded) -> None:  # type: ignore[no-untyped-def]
    early, late = view(loaded, 0), view(loaded, 60)

    assert early.entry_index == 5 and early.bars[0].timestamp == T0 - 5 * HOUR  # only 5 bars exist before the first entry
    assert len(early.bars) - 1 - early.last_index == 20
    assert late.entry_index == 50 and late.last_index == 52 and len(late.bars) == 50 + 3 + 18  # the tape ends 18 bars after that exit
    assert [b.timestamp for b in late.bars] == sorted(b.timestamp for b in late.bars)


def test_an_expected_hour_without_a_bar_is_listed_as_a_hole_and_a_closed_market_is_not(loaded, study_paths) -> None:  # type: ignore[no-untyped-def]
    v = view(loaded, 13)

    assert [(v.bars[i].timestamp, missing) for i, missing in v.holes] == [(T0 + 14 * HOUR, (T0 + 15 * HOUR,))]
    assert view(loaded, 1, "GBPUSD=X").holes == []


def test_indicators_are_the_recorded_values_on_the_candle_they_were_computed_at(loaded) -> None:  # type: ignore[no-untyped-def]
    study, _, signals = loaded
    v = view(loaded, 36)

    assert list(v.indicators) == ["ema_fast", "ema_slow"] and len(v.rsi) == len(v.atr) == len(v.bars)
    for index, bar in enumerate(v.bars):
        row = signals["EURUSD"].get(bar.timestamp + HOUR)  # the evaluation of the candle that closed one hour after this label
        for column in ("ema_fast", "ema_slow"):
            expected = None if row is None or row[column] == "" else D(row[column])
            assert v.indicators[column][index] == expected
    closes = {b.timestamp: b.close for b in study.bars["EURUSD"]}
    at = v.entry_index - 1  # the signal candle: its value uses its own close and the one before, nothing later
    assert v.indicators["ema_fast"][at] == (closes[v.bars[at].timestamp] + closes[v.bars[at - 1].timestamp]) / 2


def test_where_the_strategy_did_not_evaluate_the_line_breaks(loaded) -> None:  # type: ignore[no-untyped-def]
    v = view(loaded, 36)
    index = {b.timestamp: i for i, b in enumerate(v.bars)}

    assert v.indicators["ema_fast"][index[T0 + 29 * HOUR]] is None and v.rsi[index[T0 + 29 * HOUR]] is None  # the evaluation at T0+30 was rejected
    assert v.indicators["ema_fast"][index[T0 + 28 * HOUR]] is not None and v.indicators["ema_fast"][index[T0 + 30 * HOUR]] is not None


def test_the_real_strategies_name_their_averages_only_when_the_fingerprint_matches() -> None:
    from investment_committee.simulation import ema_cross, ema_trend_cross

    a = ["ema_fast", "ema_slow", "rsi"]
    b = ["ema_cross_fast", "ema_cross_slow", "ema_trend_long", "ema_trend_mid", "ema_trend_short"]
    assert trade_view.indicator_columns(ema_cross.STRATEGY_ID, ema_cross.fingerprint(), a) == {"ema_fast": "EMA20", "ema_slow": "EMA50"}
    assert list(trade_view.indicator_columns(ema_trend_cross.STRATEGY_ID, ema_trend_cross.fingerprint(), b).values()) == ["EMA3", "EMA9", "EMA20", "EMA50", "EMA120"]
    assert trade_view.indicator_columns(ema_cross.STRATEGY_ID, "otra huella", a) == {"ema_fast": "ema_fast", "ema_slow": "ema_slow"}
    from investment_committee.simulation import ema_pullback

    c = ["ema_fast", "ema_trend_long", "ema_trend_mid", "ema_trend_short", "atr"]
    assert list(trade_view.indicator_columns(ema_pullback.STRATEGY_ID, ema_pullback.fingerprint(), c).values()) == ["EMA9", "EMA20", "EMA50", "EMA120"]


# --- rejected signals, sheet, conversions, time zones ---


def test_rejected_signals_keep_the_engines_own_reasons(loaded) -> None:  # type: ignore[no-untyped-def]
    v = view(loaded, 5)
    by_time = {r.at: r for r in v.rejected}

    assert by_time[T0 + 6 * HOUR].reasons == ("POSICION_ABIERTA", "LIMITE_PERDIDA_DIARIA") and by_time[T0 + 6 * HOUR].direction == "LONG"
    assert by_time[T0 + 18 * HOUR].reasons == ("LIMITE_PERDIDA_DIARIA",)
    assert v.bars[by_time[T0 + 6 * HOUR].index].timestamp == T0 + 6 * HOUR
    assert view(loaded, 1, "GBPUSD=X").rejected == []  # another pair's rejections do not appear


def test_pips_lots_and_risk_are_plain_conversions() -> None:
    assert pips("EURUSD", D("0.00150")) == D("15.0") and pips("GBPUSD=X", D("0.00585")) == D("58.5")
    assert lots(25000) == D("0.25") and lots(100000) == D("1") and lots(1000) == D("0.01")


def test_the_sheet_states_the_engines_figures_and_what_kind_each_is(loaded) -> None:  # type: ignore[no-untyped-def]
    v = view(loaded, 0)
    sheet = {f.label: f for f in v.sheet}

    assert sheet["ID de operación"].value == tid(0) and sheet["Estrategia y versión"].value == ALFA
    assert sheet["Precio de referencia (apertura de Yahoo)"].value == "1.08495" and sheet["Precio de referencia (apertura de Yahoo)"].kind == OBSERVED
    assert sheet["Bid / ask estimados"].value == "1.08490 / 1.08500" and sheet["Bid / ask estimados"].kind == ESTIMATED
    assert sheet["Stop loss"].value == "1.08352 · 15.0 pips · 1 R" and sheet["Take profit"].value == "1.08958 · 45.6 pips · 3.04 R bruto"
    assert sheet["Stop sobre el precio medio del gráfico"].value == "1.08357" and sheet["Stop sobre el precio medio del gráfico"].kind == DERIVED
    assert sheet["Unidades de moneda base"].value == "32000" and sheet["Lotes estándar (100 000 unidades)"].value == "0.32"
    assert sheet["Capital antes de la operación"].value == "10000 USD" and sheet["Capital después (antes + resultado contabilizado)"].value == "9951.36 USD"
    assert sheet["Pérdida estimada si se alcanza el stop"].value == "48.64 USD (0.4864 % del capital)"
    assert sheet["Ganancia estimada si se alcanza el objetivo"].value == "145.92 USD" and sheet["Riesgo presupuestado"].value == "50.00 USD"
    assert sheet["Resultado"].value == "-48.64 USD · -1.0000 R · -0.4864 % del capital" and sheet["Motivo de salida"].value == "STOP"
    assert sheet["Momento de la salida"].value.startswith("dentro de la vela") and sheet["Condiciones que confirmaron la señal"].value == "CRUCE_ALCISTA RSI_CONFIRMA"
    assert "no verificada" in sheet["Comisión"].value and {f.kind for f in v.sheet} == {OBSERVED, ESTIMATED, DERIVED}
    assert [s for s in dict.fromkeys(f.section for f in v.sheet)] == ["Identificación", "Indicadores en la señal", "Entrada y niveles", "Tamaño y capital", "Costos", "Resultado"]


def test_time_zones_only_change_how_an_instant_is_shown() -> None:
    summer, winter = datetime(2026, 7, 1, 14, 0, tzinfo=timezone.utc), datetime(2026, 12, 1, 14, 0, tzinfo=timezone.utc)

    assert show_time(winter) == "2026-12-01 09:00 Bogotá (UTC-05:00)"  # the default; Bogotá has no daylight saving
    assert show_time(summer, "Bogotá") == "2026-07-01 09:00 Bogotá (UTC-05:00)"
    assert (show_time(summer, "Nueva York"), show_time(winter, "Nueva York")) == ("2026-07-01 10:00 Nueva York (UTC-04:00)", "2026-12-01 09:00 Nueva York (UTC-05:00)")
    assert show_time(winter, "UTC") == "2026-12-01 14:00 UTC (UTC+00:00)" and show_time(None) == "—"


def test_the_view_keeps_utc_whatever_zone_is_shown(loaded) -> None:  # type: ignore[no-untyped-def]
    study, run, signals = loaded
    bogota, utc = build_trade_view(study, run, signals, tid(0), "Bogotá"), build_trade_view(study, run, signals, tid(0), "UTC")

    assert bogota.entry_at == utc.entry_at == T0 and bogota.bars == utc.bars and bogota.trade == utc.trade
    assert {f.label: f.value for f in bogota.sheet}["Entrada (apertura de la vela siguiente)"] == "2026-12-01 09:00 Bogotá (UTC-05:00)"
    assert {f.label: f.value for f in utc.sheet}["Entrada (apertura de la vela siguiente)"] == "2026-12-01 14:00 UTC (UTC+00:00)"


def test_the_explorer_row_is_the_trade_with_its_conversions(loaded) -> None:  # type: ignore[no-untyped-def]
    _, run, _ = loaded
    row = table_row(run.trade(tid(0)), BASE)
    unknown = table_row(run.trade(tid(10)), BASE)

    assert (row["ID"], row["Par"], row["Dirección"], row["Resultado"], row["Motivo de salida"]) == (tid(0), "EURUSD", "LONG", "PERDEDORA", "STOP")
    assert (row["Entrada"], row["Stop loss"], row["Take profit"], row["Unidades"], row["Lotes"]) == (D("1.08502"), D("1.08352"), D("1.08958"), 32000, D("0.32"))
    assert (row["Riesgo (USD)"], row["Riesgo (% capital)"], row["Resultado (USD)"], row["Resultado (R)"]) == (D("48.64"), D("0.4864"), D("-48.64"), D("-1.0000"))
    assert (row["Entrada (hora)"], row["Entrada (UTC)"], row["Escenario"], row["Fuente"]) == ("2026-12-01 09:00 Bogotá (UTC-05:00)", "2026-12-01T14:00:00+00:00", "BASE", "SINTETICA")
    assert (unknown["Resultado"], unknown["Resultado (USD)"], unknown["Conservador (USD)"], unknown["Optimista (USD)"]) == ("INDETERMINADA", None, D("-50.16"), D("150.48"))
    assert table_row(run.trade(tid(36)), BASE)["Motivo de salida"] == "STOP (gap)"


# --- A and B side by side ---


def both(study_paths, scenario_b: str = "BASE"):  # type: ignore[no-untyped-def]
    study = load_study(*study_paths)
    return study, [load_run(study, RunKey(ALFA, "COMPLETO", "BASE")), load_run(study, RunKey(BETA, "COMPLETO", scenario_b))]


def test_when_both_strategies_traded_the_comparison_says_so_and_shows_each_entry(study_paths) -> None:  # type: ignore[no-untyped-def]
    study, runs = both(study_paths)
    c = compare_interval(study, runs, "EURUSD", T0 + 5 * HOUR, T0 + 9 * HOUR)

    assert c.verdict == "TODAS_OPERARON" and c.same_conditions and len(c.bars) == 4
    assert [[t["entry_at"] for t in a.trades] for a in c.activity] == [[(T0 + 5 * HOUR).isoformat()]] * 2
    assert any("coinciden en el instante" in n for n in c.notes)
    assert any("rechazada" in n and "POSICION_ABIERTA" in n for n in c.notes)  # alfa's second signal, inside the interval


def test_when_only_one_traded_or_none_did_the_comparison_says_which(study_paths) -> None:  # type: ignore[no-untyped-def]
    study, runs = both(study_paths)

    assert compare_interval(study, runs, "EURUSD", T0 + 59 * HOUR, T0 + 61 * HOUR).verdict == "TODAS_OPERARON"  # beta's short from T0+36 is still open
    assert compare_interval(study, runs, "EURUSD", T0 + 10 * HOUR, T0 + 12 * HOUR).verdict == f"SOLO_OPERO: {ALFA}"
    assert compare_interval(study, runs, "EURUSD", T0 + 20 * HOUR, T0 + 30 * HOUR).verdict == "NINGUNA_OPERO"
    assert compare_interval(study, runs, "GBPUSD", T0 + 20 * HOUR, T0 + 30 * HOUR).verdict == f"SOLO_OPERO: {ALFA}"
    with pytest.raises(StudyError):
        compare_interval(study, runs, "EURUSD", T0, T0)


def test_runs_under_different_conditions_are_never_shown_as_equivalent(study_paths) -> None:  # type: ignore[no-untyped-def]
    study, runs = both(study_paths, "COSTOS_X2")
    c = compare_interval(study, runs, "EURUSD", T0 + 35 * HOUR, T0 + 40 * HOUR)

    assert not c.same_conditions and any("NO comparten" in n for n in c.notes)
    assert c.verdict == "TODAS_OPERARON" and any("tamaños difieren" in n or "coinciden en el instante" in n for n in c.notes)


# --- isolation ---


def test_the_store_the_view_and_the_notes_need_no_viewer_dependency() -> None:
    folder = Path(study_store.__file__).parent
    for name in ("study_store.py", "trade_view.py", "notes.py", "__init__.py"):
        if not (folder / name).exists():
            continue
        tree = ast.parse((folder / name).read_text(encoding="utf-8"))
        imported = {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module and n.level == 0}
        imported |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not imported & {"plotly", "streamlit", "kaleido", "pandas", "numpy"}, name


def test_the_simulation_never_imports_the_viewer() -> None:
    simulation = Path(study_store.__file__).parents[2] / "simulation"
    offenders = [p.name for p in simulation.glob("*.py") if any(word in p.read_text(encoding="utf-8") for word in ("trade_viewer", "plotly", "streamlit", "kaleido"))]

    assert offenders == []
