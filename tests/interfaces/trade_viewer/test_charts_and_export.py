"""What is drawn and exported is what the engine recorded. Skipped when the
optional viewer dependencies are not installed."""

from __future__ import annotations

import csv
import io
from decimal import Decimal

import pytest

pytest.importorskip("plotly")

from investment_committee.interfaces.trade_viewer import charts, export  # noqa: E402
from investment_committee.interfaces.trade_viewer.study_store import RunKey, load_run, load_signals, load_study  # noqa: E402
from investment_committee.interfaces.trade_viewer.trade_view import SIMULATION_WARNING, build_trade_view, compare_interval, table_row  # noqa: E402

from tests.simulation._forex_backtest_support import HOUR, T0  # noqa: E402

D = Decimal
ALFA, BETA = "alfa@0.0.0", "beta@0.0.0"
BASE = RunKey(ALFA, "COMPLETO", "BASE")


def tid(offset: int, symbol: str = "EURUSD=X") -> str:
    return f"{ALFA}|{symbol}|{(T0 + offset * HOUR).isoformat()}"


@pytest.fixture(scope="module")
def loaded(study_paths):  # type: ignore[no-untyped-def]
    study = load_study(*study_paths)
    return study, load_run(study, BASE), load_signals(study, ALFA)


def view(loaded, offset: int, symbol: str = "EURUSD=X", zone: str = "Bogotá"):  # type: ignore[no-untyped-def]
    return build_trade_view(*loaded, tid(offset, symbol), zone)


def trace(figure, start: str):  # type: ignore[no-untyped-def]
    found = [t for t in figure.data if (t.name or "").startswith(start)]
    assert len(found) == 1, (start, [t.name for t in figure.data])
    return found[0]


# --- the trade chart ---


def test_the_candles_are_the_snapshots_bars_in_order(loaded) -> None:  # type: ignore[no-untyped-def]
    v = view(loaded, 0)
    candles = trace(charts.trade_figure(v), "Precio medio de Yahoo")

    assert list(candles.x) == list(range(len(v.bars)))
    for name in ("open", "high", "low", "close"):
        assert list(getattr(candles, name)) == [float(getattr(b, name)) for b in v.bars]
    assert candles.text[v.entry_index] == "2026-12-01 09:00 Bogotá (UTC-05:00)"


@pytest.mark.parametrize("offset", [0, 5, 10, 13, 36, 60, 66])
def test_entry_stop_and_target_are_drawn_at_the_recorded_levels(loaded, offset: int) -> None:  # type: ignore[no-untyped-def]
    v = view(loaded, offset)
    figure = charts.trade_figure(v)
    p = v.trade["proposal"]

    for start, level in (("Entrada 1", p["entry"]), ("Stop loss", p["stop"]), ("Take profit", p["target"])):
        line = trace(figure, start)
        assert list(line.y) == [float(level)] * 2 and str(D(level)) in line.name and "ESTIMAD" in line.name
        assert list(line.x) == [v.entry_index - 0.5, v.last_index + 0.5]  # from the entry bar, never before it
    assert list(trace(figure, "Stop sobre precio medio").y) == [float(v.stop_trigger_mid)] * 2
    assert "DERIVADO" in trace(figure, "Objetivo sobre precio medio").name
    entry = trace(figure, f"Entrada {v.direction} en la apertura")
    assert (list(entry.x), list(entry.y)) == ([v.entry_index], [float(p["entry"])])
    assert entry.marker.symbol == ("triangle-up" if v.direction == "LONG" else "triangle-down")
    signal = trace(figure, "Vela de señal")
    assert list(signal.x) == [v.entry_index - 1]  # the candle before the entry bar


def test_pips_and_r_are_written_on_the_levels(loaded) -> None:  # type: ignore[no-untyped-def]
    figure = charts.trade_figure(view(loaded, 0))

    assert trace(figure, "Stop loss").name == "Stop loss 1.08352 · 15.0 pips · 1 R (ESTIMADO)"
    assert trace(figure, "Take profit").name == "Take profit 1.08958 · 45.6 pips · 3.04 R (ESTIMADO)"
    zones = [s for s in figure.layout.shapes if s.type == "rect" and s.name in ("zona de riesgo", "zona de recompensa")]
    assert sorted((s.name, s.y0, s.y1) for s in zones) == [("zona de recompensa", 1.08502, 1.08958), ("zona de riesgo", 1.08502, 1.08352)]


def test_an_exit_inside_a_bar_shades_the_bar_and_an_exit_at_an_open_is_a_point(loaded) -> None:  # type: ignore[no-untyped-def]
    inside, at_open = view(loaded, 0), view(loaded, 36)
    f_inside, f_open = charts.trade_figure(inside), charts.trade_figure(at_open)

    exit_inside = trace(f_inside, "Salida STOP")
    assert (list(exit_inside.x), list(exit_inside.y)) == ([inside.exit_index], [1.0835]) and "instante desconocido" in exit_inside.name
    assert any(s.type == "rect" and (s.x0, s.x1) == (inside.exit_index - 0.5, inside.exit_index + 0.5) and s.name is None for s in f_inside.layout.shapes)
    exit_open = trace(f_open, "Salida STOP con gap")
    assert (list(exit_open.x), list(exit_open.y)) == ([at_open.exit_index], [1.08198]) and "en la apertura" in exit_open.name
    assert not any(s.type == "rect" and s.name is None and s.x1 - s.x0 == 1 for s in f_open.layout.shapes)


def test_winners_losers_and_unknowns_have_their_own_colour(loaded) -> None:  # type: ignore[no-untyped-def]
    colours = {name: trace(charts.trade_figure(view(loaded, offset)), "Salida").marker.color for name, offset in (("perdedora", 0), ("ganadora", 5))}

    assert colours == {"perdedora": charts.COLORS["PERDEDORA"], "ganadora": charts.COLORS["GANADORA"]} and len(set(charts.COLORS.values())) >= 4


@pytest.mark.parametrize("offset", [10, 13])
def test_an_indeterminate_trade_shows_both_hypothetical_exits_and_no_exit(loaded, offset: int) -> None:  # type: ignore[no-untyped-def]
    v = view(loaded, offset)
    figure = charts.trade_figure(v)

    assert not [t for t in figure.data if (t.name or "").startswith("Salida")]
    stop_first, target_first = trace(figure, "Si el stop fue primero"), trace(figure, "Si el objetivo fue primero")
    assert "-50.16 USD (hipotético)" in stop_first.name and "150.48 USD (hipotético)" in target_first.name
    assert list(stop_first.y) == [float(v.conservative["exit_price"])] and list(target_first.y) == [float(v.optimistic["exit_price"])]
    assert any("ORDEN NO OBSERVABLE" in (a.text or "") for a in figure.layout.annotations)
    assert "INDETERMINADA" in figure.layout.title.text and "conservador -50.16 USD / optimista 150.48 USD" in figure.layout.title.text


def test_a_position_open_at_the_end_has_no_exit_mark(short_study_paths) -> None:  # type: ignore[no-untyped-def]
    study = load_study(*short_study_paths)
    figure = charts.trade_figure(build_trade_view(study, load_run(study, BASE), load_signals(study, ALFA), tid(1, "GBPUSD=X")))

    assert not [t for t in figure.data if (t.name or "").startswith(("Salida", "Si el"))] and "ABIERTA_AL_FINAL" in figure.layout.title.text


def test_indicator_lines_are_the_recorded_values_and_break_where_there_is_none(loaded) -> None:  # type: ignore[no-untyped-def]
    v = view(loaded, 36)
    figure = charts.trade_figure(v)
    fast = trace(figure, "ema_fast")

    assert list(fast.y) == [None if value is None else float(value) for value in v.indicators["ema_fast"]]
    assert None in fast.y and fast.connectgaps is False and "registrada" in fast.name
    assert list(trace(figure, "RSI14").y) == [None if value is None else float(value) for value in v.rsi]
    assert [y for y in trace(figure, "ATR14").y if y is not None][0] == 10.0  # 0.00100 is 10 pips


def test_holes_and_market_closures_are_marked_differently(loaded) -> None:  # type: ignore[no-untyped-def]
    texts = [a.text for a in charts.trade_figure(view(loaded, 13)).layout.annotations]

    assert "sin datos: 1 h" in texts and "mercado cerrado" not in texts
    panels = charts.trade_figure(view(loaded, 13), show_rsi=False, show_atr=False)
    assert not [t for t in panels.data if (t.name or "").startswith(("RSI14", "ATR14"))]


def test_rejected_signals_appear_only_when_asked_and_with_the_engines_reasons(loaded) -> None:  # type: ignore[no-untyped-def]
    v = view(loaded, 5)
    hidden, shown = charts.trade_figure(v), charts.trade_figure(v, show_rejected=True)

    assert not [t for t in hidden.data if (t.name or "").startswith("Señal rechazada")]
    names = sorted(t.name for t in shown.data if (t.name or "").startswith("Señal rechazada"))
    assert names == ["Señal rechazada: LIMITE_PERDIDA_DIARIA", "Señal rechazada: POSICION_ABIERTA + LIMITE_PERDIDA_DIARIA"]


def test_the_title_identifies_the_trade_and_says_it_is_a_simulation(loaded) -> None:  # type: ignore[no-untyped-def]
    title = charts.trade_figure(view(loaded, 0, zone="UTC"), "UTC").layout.title.text

    for part in (ALFA, "EURUSD", "H1", "LONG", "PERDEDORA", "STOP", "-48.64 USD", "-1.0000 R", tid(0), "escenario BASE", "período COMPLETO", "fuente SINTETICA",
                 "2026-12-01 14:00 UTC (UTC+00:00)", SIMULATION_WARNING):
        assert part in title, part
    assert "Hora de apertura de cada vela (UTC)" in str(charts.trade_figure(view(loaded, 0), "UTC").layout)


# --- the run as a whole, and A against B ---


def test_the_capital_curves_are_the_runs_hourly_points(loaded) -> None:  # type: ignore[no-untyped-def]
    _, run, _ = loaded
    figure = charts.equity_figure(run, "UTC")

    assert list(trace(figure, "Saldo realizado").y) == [float(p["balance"]) for p in run.equity]
    assert list(trace(figure, "Equity estimado").y) == [float(p["equity"]) for p in run.equity]
    assert trace(figure, "Saldo realizado").x[0].isoformat() == run.equity[0]["at"][:19]
    assert charts.equity_figure(run, "Bogotá").data[0].x[0].hour == (int(run.equity[0]["at"][11:13]) - 5) % 24


def test_drawdown_is_the_fall_from_the_highest_value_so_far() -> None:
    assert charts.drawdown_series([D("100"), D("120"), D("90"), D("130"), D("104")]) == [D("0"), D("0"), D("-25"), D("0"), D("-20")]


def test_the_summary_charts_use_the_recorded_summary(loaded) -> None:  # type: ignore[no-untyped-def]
    _, run, _ = loaded
    summary = run.summary

    assert list(charts.distribution_figure(summary).data[0].y) == list(summary["closed"]["TOTAL"]["r_distribution"].values())
    assert dict(zip(charts.exits_figure(summary).data[0].x, charts.exits_figure(summary).data[0].y)) == summary["closed"]["TOTAL"]["exits"]
    monthly = charts.monthly_figure(summary)
    assert list(monthly.data[0].y) == [float(m["net_pnl"]) for m in summary["monthly"].values()] and monthly.data[0].text[0].startswith("n = ")
    assert f"n = {summary['closed']['TOTAL']['n']}" in charts.distribution_figure(summary).layout.title.text


def test_the_side_by_side_chart_keeps_each_strategys_marks_apart(study_paths) -> None:  # type: ignore[no-untyped-def]
    study = load_study(*study_paths)
    runs = [load_run(study, BASE), load_run(study, RunKey(BETA, "COMPLETO", "BASE"))]
    figure = charts.interval_figure(compare_interval(study, runs, "EURUSD", T0 + 30 * HOUR, T0 + 45 * HOUR))
    a, b = trace(figure, f"{ALFA} (BASE/COMPLETO): entradas"), trace(figure, f"{BETA} (BASE/COMPLETO): entradas")

    assert list(a.x) == list(b.x) == [6] and a.marker.color != b.marker.color  # both at T0+36, in different colours
    assert (list(a.y), list(b.y)) == ([1.08502], [1.08488]) and "LONG · 32000 unidades" in a.text[0] and "SHORT · 33000 unidades" in b.text[0]
    assert "TODAS_OPERARON" in figure.layout.title.text
    assert list(trace(figure, f"{ALFA} (BASE/COMPLETO): salidas").y) == [1.08198]  # alfa's gap; beta's short is still open in the interval


# --- exports ---


def test_the_html_of_a_trade_stands_alone_and_identifies_it(loaded, tmp_path) -> None:  # type: ignore[no-untyped-def]
    v = view(loaded, 0)
    page = export.trade_html(v, note={"rating": "Discutible", "comment": "entrada tardía", "reviewer": "Ana"})

    for part in (ALFA, "EURUSD", "H1", "BASE", "COMPLETO", tid(0), "SINTETICA", v.study_sha256, "Simulación con modelo de ejecución ESTIMADO",
                 "1.08352", "32000", "Discutible", "entrada tardía", "Plotly.newPlot", "<!DOCTYPE html>"):
        assert part in page, part
    assert len(page) > 1_000_000  # the library is embedded, so the page opens without internet
    path = export.write_export(tmp_path / "exportados", export.safe_name(v) + ".html", page)
    assert path.name == "alfa-0.0.0_EURUSD_20261201T1400Z_LONG_BASE_COMPLETO.html" and path.read_text(encoding="utf-8") == page


def test_exports_carry_no_path_of_this_machine(loaded, study_paths, tmp_path) -> None:  # type: ignore[no-untyped-def]
    study, run, _ = loaded
    v = view(loaded, 13)
    rows = [table_row(t, BASE) for t in run.trades]
    produced = [export.trade_html(v), export.trades_csv(rows, study.study_sha256, "SINTETICA"),
                export.report_html("Revisión", [v, view(loaded, 0)], summary_rows=[("n", "8")], limitations=["precio indicativo"])]
    private = [str(study_paths[0]), str(study_paths[0].parent.parent), str(tmp_path.parent), str(study_paths[0]).replace("\\", "/"), str(study_paths[0]).replace("\\", "\\\\")]

    for text in produced:
        assert not [p for p in private if p in text]


def test_the_csv_is_the_filtered_table_with_hash_kind_and_warning_on_every_row(loaded) -> None:  # type: ignore[no-untyped-def]
    study, run, _ = loaded
    rows = [table_row(t, BASE) for t in run.trades if t["direction"] == "SHORT"]
    table = list(csv.DictReader(io.StringIO(export.trades_csv(rows, study.study_sha256, "SINTETICA"))))

    assert [r["ID"] for r in table] == [tid(5), tid(66)] and "\r" not in export.trades_csv(rows, study.study_sha256, "SINTETICA")
    assert table[0]["Take profit"] == "1.08032" and table[0]["Resultado (USD)"] == "145.92" and table[0]["Lotes"] == "0.32"
    assert all(r["Hash del estudio"] == study.study_sha256 and r["Fuente de datos"] == "SINTETICA" and r["Advertencia"] == SIMULATION_WARNING for r in table)
    assert export.trades_csv([], study.study_sha256, "SINTETICA").startswith("Hash del estudio")


def test_the_review_report_holds_metrics_limitations_and_every_selected_trade_once(loaded) -> None:  # type: ignore[no-untyped-def]
    views = [view(loaded, 0), view(loaded, 5), view(loaded, 10)]
    page = export.report_html("Revisión de operaciones · alfa", views, summary_rows=[("Operaciones cerradas válidas", "6"), ("Resultado", "-7.52 USD")],
                              limitations=["Precio indicativo único de Yahoo."], notes={tid(5): {"rating": "Buena entrada", "comment": "limpia"}})

    assert page.count("Plotly.newPlot") == 3 and all(v.trade_id in page for v in views)
    assert page.count("<!DOCTYPE html>") == 1  # one page, and the library embedded once:
    assert len(page) < 2 * len(export.trade_html(views[0]))
    for part in ("Operaciones cerradas válidas", "-7.52 USD", "Precio indicativo único de Yahoo.", "Buena entrada", "limpia", "INDETERMINADA", SIMULATION_WARNING[:40]):
        assert part in page, part


def test_a_png_is_produced_or_the_failure_is_explained(loaded) -> None:  # type: ignore[no-untyped-def]
    pytest.importorskip("kaleido")
    try:
        image = export.trade_png(view(loaded, 0))
    except export.PngUnavailable as error:
        assert "Chrome" in str(error) and "HTML" in str(error)
        pytest.skip("no hay Chrome o Chromium para Kaleido en este equipo")
    assert image[:8] == b"\x89PNG\r\n\x1a\n" and len(image) > 50_000
    width, height = int.from_bytes(image[16:20], "big"), int.from_bytes(image[20:24], "big")
    assert (width, height) == (3200, 2000)  # 1600 x 1000 at double resolution
