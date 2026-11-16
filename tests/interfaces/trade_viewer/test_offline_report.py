"""The offline report (one HTML, one CSV per strategy): what it carries is
what the engine recorded, it needs nothing outside itself, it names no
machine, and it refuses a study it cannot reconcile. Standard library
only: these tests run without the optional viewer dependencies.

The page's behaviour in a real browser (filters, replay, reveal) is checked
by the two browser tests, which run only where Chrome or Edge is installed.
"""

from __future__ import annotations

import ast
import csv
import html as html_module
import io
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from investment_committee.interfaces.trade_viewer import offline_report
from investment_committee.interfaces.trade_viewer.offline_page import PAGE
from investment_committee.interfaces.trade_viewer.study_store import RunKey, StudyError, find_snapshot, load_run, load_signals, load_study

ALFA, BETA = "alfa@0.0.0", "beta@0.0.0"
ROOT = Path(__file__).resolve().parents[3]
REAL = ROOT / "data" / "reports" / "backtests" / "fx_1h_20260331_20260930_107957e7_e6cf138"
BROWSER_CHECKS = ("fullShowsResult", "blindHidesPnl", "blindStart", "blindStep", "blindStillHidden", "revealShows", "othersStillHidden", "drawn")


@pytest.fixture(scope="module")
def study(study_paths):  # type: ignore[no-untyped-def]
    return load_study(*study_paths)


@pytest.fixture(scope="module")
def payload(study):  # type: ignore[no-untyped-def]
    return offline_report.build_payload(study, period="COMPLETO")


def embedded(html: str) -> dict:  # type: ignore[type-arg]
    found = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)
    assert found is not None
    return json.loads(found.group(1))


def seconds(text: str) -> int:
    return int(datetime.fromisoformat(text).timestamp())


def test_every_trade_is_the_engines_record_and_keeps_its_identifier(study, payload) -> None:  # type: ignore[no-untyped-def]
    for strategy in payload["strategies"]:
        run = load_run(study, RunKey(strategy["id"], "COMPLETO", "BASE"))
        assert [t["id"] for t in strategy["trades"]] == [t["proposal"]["proposal_id"] for t in run.trades]
        for shown, record in zip(strategy["trades"], run.trades):
            p, result = record["proposal"], record["result"]
            assert (shown["entry"], shown["stop"], shown["target"], shown["units"], shown["dir"]) == (p["entry"], p["stop"], p["target"], p["units"], record["direction"])
            assert (shown["equity"], shown["budget"], shown["est_loss"]) == (p["equity"], p["risk_budget"], p["estimated_loss_at_stop"])
            if result:
                assert (shown["pnl"], shown["r"], shown["exit"], shown["reason"]) == (result["net_pnl"], result["r_multiple"], result["exit_price"], result["reason"])
            else:
                assert shown["pnl"] is None and shown["r"] is None and shown["exit"] is None


def test_only_valid_closed_trades_count_and_the_others_are_kept_apart(payload) -> None:  # type: ignore[no-untyped-def]
    alfa = next(s for s in payload["strategies"] if s["id"] == ALFA)
    valid, left_out = [t for t in alfa["trades"] if t["valid"]], [t for t in alfa["trades"] if not t["valid"]]
    assert len(valid) == alfa["summary"]["closed"]["TOTAL"]["n"] == alfa["summary"]["funnel"]["closed_valid"]
    assert {t["outcome"] for t in left_out} == {"INDETERMINADA"} and len(left_out) == 2
    assert all(t["pnl"] is None for t in left_out)
    assert sum(Decimal(t["pnl"]) for t in valid) == Decimal(alfa["summary"]["closed"]["TOTAL"]["net_pnl"])


def test_open_positions_are_left_out_of_the_count(short_study_paths) -> None:  # type: ignore[no-untyped-def]
    short = offline_report.build_payload(load_study(*short_study_paths), period="COMPLETO")
    alfa = next(s for s in short["strategies"] if s["id"] == ALFA)
    still_open = [t for t in alfa["trades"] if t["outcome"] == "ABIERTA_AL_FINAL"]
    assert still_open and not any(t["valid"] for t in still_open) and all(t["i_out"] is None and t["t_out"] is None for t in still_open)
    assert len([t for t in alfa["trades"] if t["valid"]]) == alfa["summary"]["closed"]["TOTAL"]["n"]


def test_candles_are_the_snapshots_bars_digit_for_digit(study, payload) -> None:  # type: ignore[no-untyped-def]
    for symbol, series in study.bars.items():
        shown = payload["bars"][symbol]
        assert len(shown["t"]) == len(series)
        for index, bar in enumerate(series):
            assert shown["t"][index] == int(bar.timestamp.timestamp())
            assert [Decimal(repr(shown[k][index])) for k in "ohlc"] == [bar.open, bar.high, bar.low, bar.close]


def test_a_price_that_cannot_be_written_exactly_is_refused() -> None:
    with pytest.raises(StudyError):
        offline_report._exact(Decimal("1.12345678901234567890123"))


def test_indicators_are_the_recorded_values_on_their_own_candle_and_break_where_nothing_was_recorded(study, payload) -> None:  # type: ignore[no-untyped-def]
    alfa = next(s for s in payload["strategies"] if s["id"] == ALFA)
    rows, bars = load_signals(study, ALFA)["EURUSD"], study.bars["EURUSD"]
    lines = alfa["indicators"]["EURUSD"]
    assert set(lines) == {"ema_fast", "ema_slow", "RSI14", "ATR14"} and all(len(values) == len(bars) for values in lines.values())
    seen_value = seen_break = False
    for index, bar in enumerate(bars):
        row = rows.get(bar.timestamp + offline_report.HOUR)  # the evaluation of the candle that closed one hour after its label
        recorded = None if row is None or row["ema_fast"] == "" else float(Decimal(row["ema_fast"]).quantize(Decimal("0.000001")))
        assert lines["ema_fast"][index] == recorded
        seen_value, seen_break = seen_value or recorded is not None, seen_break or (recorded is None and index > 10)
    assert seen_value and seen_break


def test_each_trade_points_at_its_own_candles(payload) -> None:  # type: ignore[no-untyped-def]
    for strategy in payload["strategies"]:
        for trade in strategy["trades"]:
            bars = payload["bars"][trade["sym"]]
            assert bars["t"][trade["i_in"]] == seconds(trade["t_in"])
            assert bars["t"][trade["i_sig"]] + 3600 == seconds(trade["sig"])
            assert Decimal(repr(bars["o"][trade["i_in"]])) == (Decimal(trade["bid"]) + Decimal(trade["ask"])) / 2
            if trade["i_out"] is not None:
                assert trade["i_in"] <= trade["i_out"] <= trade["i_last"]


def test_the_html_is_one_file_that_needs_nothing_else(payload) -> None:  # type: ignore[no-untyped-def]
    html = offline_report.report_html(payload)
    assert html.startswith("<!DOCTYPE html>") and embedded(html) == json.loads(json.dumps(payload))
    assert not re.search(r"https?://|//cdn|<link\b|<script[^>]+\bsrc=|<img\b|<iframe\b|@import|url\(", html)
    assert not re.search(r"\bfetch\(|XMLHttpRequest|WebSocket|sendBeacon|EventSource|localStorage|document\.cookie|\beval\(|new Function", html)


def test_the_html_names_no_machine_and_states_what_the_prices_are(study, payload) -> None:  # type: ignore[no-untyped-def]
    html = offline_report.report_html(payload)
    home = Path.home()
    for private in (str(study.directory), str(study.directory.parent), str(home), os.environ.get("USERNAME") or home.name, "operating_system", "\\Users\\", "/Users/"):
        assert private not in html, private
    assert "@" not in re.sub(r"[\w.-]+@\d+\.\d+\.\d+", "", html.replace("@media", ""))  # no address: only strategy@version identifiers
    assert "execution" not in payload["meta"] and payload["meta"]["study_sha256"] == study.study_sha256
    assert "Yahoo" in PAGE and "ESTIMADO" in html and "No es una validación independiente" in html
    assert study.manifest["dataset_sha256"] in html and all(s["results_sha256"] in html for s in payload["strategies"])


def test_data_cannot_break_out_of_its_script_block(payload) -> None:  # type: ignore[no-untyped-def]
    hostile = json.loads(json.dumps(payload))
    hostile["meta"]["data_source"] = "</script><script>alert(1)</script>"
    html = offline_report.report_html(hostile)
    assert html.count("</script>") == PAGE.count("</script>") and embedded(html)["meta"]["data_source"] == hostile["meta"]["data_source"]


def test_the_csv_of_a_strategy_holds_its_valid_closed_trades_with_their_identifiers(study, payload) -> None:  # type: ignore[no-untyped-def]
    files = offline_report.build_files(study, period="COMPLETO")
    assert set(files) == {"investment_committee_trades.html", "strategy_A_trades.csv", "strategy_B_trades.csv", "excluded_trades.csv"}
    for strategy in payload["strategies"]:
        rows = list(csv.DictReader(io.StringIO(files[f"strategy_{strategy['label']}_trades.csv"])))
        valid = [t for t in strategy["trades"] if t["valid"]]
        assert [r["ID"] for r in rows] == [t["id"] for t in valid]
        assert [(r["Resultado (USD)"], r["Resultado (R)"], r["Entrada"], r["Unidades"]) for r in rows] == [(t["pnl"], t["r"], t["entry"], str(t["units"])) for t in valid]
        assert all(r["Hash del estudio"] == study.study_sha256 and "ESTIMADO" in r["Advertencia"] for r in rows)
    left_out = list(csv.DictReader(io.StringIO(files["excluded_trades.csv"])))
    assert {r["Resultado"] for r in left_out} == {"INDETERMINADA"} and all(r["Resultado (USD)"] == "" for r in left_out)


def test_a_study_whose_trades_do_not_add_up_to_its_summary_is_refused(study, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    real_load = offline_report.load_run

    def altered(s, key):  # type: ignore[no-untyped-def]
        run = real_load(s, key)
        summary = json.loads(json.dumps(run.summary))
        summary["closed"]["TOTAL"]["net_pnl"] = "0.01"
        return type(run)(run.key, run.data, summary, run.record)

    monkeypatch.setattr(offline_report, "load_run", altered)
    with pytest.raises(StudyError, match="no coinciden con el resumen"):
        offline_report.build_payload(study, period="COMPLETO")


def test_the_report_modules_need_no_optional_dependency() -> None:
    for name in ("offline_report.py", "offline_page.py"):
        tree = ast.parse((ROOT / "src" / "investment_committee" / "interfaces" / "trade_viewer" / name).read_text(encoding="utf-8"))
        imported = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        imported |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not imported & {"plotly", "streamlit", "kaleido", "pandas", "numpy", "requests", "httpx", "urllib", "socket"}, name


def _browser() -> str | None:
    candidates = [shutil.which(name) for name in ("chrome", "google-chrome", "chromium", "msedge")]
    candidates += [r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    return next((c for c in candidates if c and Path(c).exists()), None)


def run_selftest(html: str, folder: Path) -> dict:  # type: ignore[type-arg]
    """Open the page from file:// in a headless Chrome or Edge with name resolution cut off, and read the outcome of its own self-check."""
    browser = _browser()
    if browser is None:
        pytest.skip("no hay Chrome ni Edge en esta máquina")
    page = folder / "reporte.html"
    page.write_text(html, encoding="utf-8")
    command = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
               "--host-resolver-rules=MAP * ~NOTFOUND", f"--user-data-dir={folder / 'perfil'}", "--window-size=1400,1000", "--virtual-time-budget=8000",
               "--dump-dom", page.as_uri() + "#selftest"]
    try:
        dom = subprocess.run(command, capture_output=True, timeout=120).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("el navegador no se pudo ejecutar sin interfaz")
    if "data-ready" not in dom:
        pytest.skip("el navegador no devolvió la página")
    found = re.search(r'<pre id="selftest"[^>]*>(.*?)</pre>', dom, re.S)
    assert found and found.group(1).strip(), "la página no completó su autocomprobación"
    return json.loads(html_module.unescape(found.group(1)))


def test_the_page_works_in_a_real_browser(payload, tmp_path) -> None:  # type: ignore[no-untyped-def]
    outcome = run_selftest(offline_report.report_html(payload), tmp_path)
    assert outcome["check"] == "ok" and len(outcome["strategies"]) == 2
    for strategy in outcome["strategies"]:
        assert strategy["rows"] == strategy["valid"] and strategy["csvLines"] == strategy["valid"] + 1
        assert strategy["long"] + strategy["short"] == strategy["valid"]
        for name in BROWSER_CHECKS:
            assert strategy[name] is True, (strategy["label"], name)


# --- the real B4.3 study, when it is on this machine (read-only) ---

real = pytest.mark.skipif(not (REAL / "manifest.json").exists(), reason="el estudio B4.3 no está en esta máquina")


@pytest.fixture(scope="module")
def real_files():  # type: ignore[no-untyped-def]
    snapshot = find_snapshot(REAL, ROOT / "data" / "raw" / "snapshots")
    if snapshot is None:
        pytest.skip("el snapshot del estudio no está en esta máquina")
    before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in REAL.rglob("*") if p.is_file()}
    files = offline_report.build_files(load_study(REAL, snapshot))
    assert before == {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in REAL.rglob("*") if p.is_file()}  # the study was only read
    return files


@real
def test_the_real_report_holds_60_and_111_valid_closed_trades(real_files) -> None:  # type: ignore[no-untyped-def]
    data = embedded(real_files["investment_committee_trades.html"])
    a, b = data["strategies"]
    assert (a["label"], a["id"], b["label"], b["id"]) == ("A", "ema-rsi-atr-forex@0.1.0", "B", "ema-3-9-20-50-120-forex@0.1.0")
    assert (len([t for t in a["trades"] if t["valid"]]), len(a["trades"])) == (60, 60)
    assert (len([t for t in b["trades"] if t["valid"]]), len(b["trades"])) == (111, 112)
    assert [t["outcome"] for t in b["trades"] if not t["valid"]] == ["ABIERTA_AL_FINAL"]
    assert (a["summary"]["closed"]["TOTAL"]["net_pnl"], b["summary"]["closed"]["TOTAL"]["net_pnl"]) == ("-1143.77", "-855.98")
    assert sum(Decimal(t["pnl"]) for t in a["trades"]) == Decimal("-1143.77")
    assert sum(Decimal(t["pnl"]) for t in b["trades"] if t["valid"]) == Decimal("-855.98")
    meta = data["meta"]
    assert meta["study_sha256"].startswith("a34b6843") and meta["data_kind"] == "ESTIMADA" and (meta["period"], meta["scenario"]) == ("COMUN", "BASE")
    assert set(a["indicators"]["EURUSD"]) == {"EMA20", "EMA50", "RSI14", "ATR14"}
    assert set(b["indicators"]["GBPUSD"]) == {"EMA3", "EMA9", "EMA20", "EMA50", "EMA120", "RSI14", "ATR14"}
    assert len(list(csv.DictReader(io.StringIO(real_files["strategy_A_trades.csv"])))) == 60
    assert len(list(csv.DictReader(io.StringIO(real_files["strategy_B_trades.csv"])))) == 111
    assert len(list(csv.DictReader(io.StringIO(real_files["excluded_trades.csv"])))) == 1


@real
def test_the_real_report_works_in_a_real_browser(real_files, tmp_path) -> None:  # type: ignore[no-untyped-def]
    outcome = run_selftest(real_files["investment_committee_trades.html"], tmp_path)
    a, b = outcome["strategies"]
    assert outcome["check"] == "ok" and (a["rows"], a["csvLines"], b["rows"], b["csvLines"], b["total"]) == (60, 61, 111, 112, 112)
    assert all(s[name] is True for s in (a, b) for name in BROWSER_CHECKS)


def test_strategy_c_keeps_its_letter_and_brings_its_own_rules_when_shown_alone() -> None:
    from investment_committee.simulation import ema_cross, ema_pullback, ema_trend_cross

    a, b, c = ema_cross.STRATEGY_ID, ema_trend_cross.STRATEGY_ID, ema_pullback.STRATEGY_ID
    assert offline_report._labels([b, a]) == {a: "A", b: "B"} and offline_report._labels([c]) == {c: "C"}
    assert offline_report._labels([c, "otra@1.0.0"]) == {c: "C", "otra@1.0.0": "A"}
    shared, limitations = offline_report._notes([c])
    assert any("2 R netos" in line for line in shared) and not any("3 R netos" in line for line in shared)
    assert any("NO CONCLUYENTE" in line for line in limitations) and any("D2" in line for line in limitations)
    assert offline_report._notes([a, b]) == (list(offline_report.SHARED_RULES), list(offline_report.LIMITATIONS))
    assert offline_report._notes(["otra@1.0.0"]) == (list(offline_report.SHARED_RULES), list(offline_report.LIMITATIONS))


# --- the registered D2 study of strategy C, when it is on this machine (read-only) ---

REAL_C = ROOT / "data" / "reports" / "backtests" / "d2_ema-pullback-forex-0.1.0_ad7ef187_41706d5"
REAL_C_BARS = ROOT / "data" / "raw" / "ingest" / "yahoo_fx_1h_20241114_20260323" / "normalizado"
real_c = pytest.mark.skipif(not (REAL_C / "manifest.json").exists() or not REAL_C_BARS.is_dir(), reason="el estudio D2 de C no está en esta máquina")


@pytest.fixture(scope="module")
def real_c_files():  # type: ignore[no-untyped-def]
    before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in REAL_C.rglob("*") if p.is_file()}
    study = load_study(REAL_C, REAL_C_BARS)
    files = {scenario: offline_report.build_files(study, period="COMPLETO", scenario=scenario) for scenario in ("BASE", "COSTOS_X1_5")}
    assert before == {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in REAL_C.rglob("*") if p.is_file()}  # the study was only read
    return files


@real_c
def test_the_report_of_c_holds_the_trades_of_the_registered_run(real_c_files) -> None:  # type: ignore[no-untyped-def]
    for scenario, valid, total, net in (("BASE", 90, 92, "-193.18"), ("COSTOS_X1_5", 88, 90, "-529.34")):
        files = real_c_files[scenario]
        assert set(files) == {"investment_committee_trades.html", "strategy_C_trades.csv", "excluded_trades.csv"}
        data = embedded(files["investment_committee_trades.html"])
        (c,) = data["strategies"]
        assert (c["label"], c["id"], data["meta"]["scenario"], data["meta"]["title"].endswith("la estrategia C")) == ("C", "ema-pullback-forex@0.1.0", scenario, True)
        assert (len([t for t in c["trades"] if t["valid"]]), len(c["trades"]), c["summary"]["closed"]["TOTAL"]["net_pnl"]) == (valid, total, net)
        assert {t["outcome"] for t in c["trades"] if not t["valid"]} == {"INDETERMINADA"}
        assert set(c["indicators"]["EURUSD"]) == {"EMA9", "EMA20", "EMA50", "EMA120", "ATR14"}
        assert all(seconds(t["t_in"]) > seconds("2025-07-14T00:00:00+00:00") and [x[0] for x in t["extra"]][:1] == ["Velas del retroceso"] for t in c["trades"])
        assert any("2 R netos" in line for line in data["shared_rules"]) and any("NO CONCLUYENTE" in line for line in data["limitations"])
        assert len(list(csv.DictReader(io.StringIO(files["strategy_C_trades.csv"])))) == valid


@real_c
def test_the_report_of_c_works_in_a_real_browser(real_c_files, tmp_path) -> None:  # type: ignore[no-untyped-def]
    (c,) = run_selftest(real_c_files["COSTOS_X1_5"]["investment_committee_trades.html"], tmp_path)["strategies"]
    assert (c["label"], c["rows"], c["csvLines"], c["total"]) == ("C", 88, 89, 90) and all(c[name] is True for name in BROWSER_CHECKS)
