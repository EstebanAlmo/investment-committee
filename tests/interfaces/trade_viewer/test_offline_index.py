"""The distribution folder: one report per strategy and scenario, an index
that links them, and the proof that a copy holds the same trades as an
earlier export. Standard library only."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from investment_committee.interfaces.trade_viewer import offline_index, offline_report
from investment_committee.interfaces.trade_viewer.offline_index import ReportSpec, build_distribution, embedded, same_trades
from investment_committee.interfaces.trade_viewer.study_store import StudyError, find_snapshot, load_study

from .test_offline_report import BROWSER_CHECKS, REAL, REAL_C, REAL_C_BARS, ROOT, _browser, run_selftest

ALFA, BETA = "alfa@0.0.0", "beta@0.0.0"
EXTERNAL = r"https?://|//cdn|<link\b|<script[^>]+\bsrc=|<img\b|<iframe\b|@import|url\(|\bfetch\(|XMLHttpRequest|WebSocket|sendBeacon|localStorage|\beval\("


@pytest.fixture(scope="module")
def study(study_paths):  # type: ignore[no-untyped-def]
    return load_study(*study_paths)


@pytest.fixture(scope="module")
def built(study):  # type: ignore[no-untyped-def]
    specs = [ReportSpec("Estrategia_Alfa.html", study, "COMPLETO", "BASE", ALFA), ReportSpec("Estrategia_Beta.html", study, "COMPLETO", "BASE", BETA),
             ReportSpec("Estrategia_Alfa_Costos_Altos.html", study, "COMPLETO", "COSTOS_X1_5", ALFA)]
    return build_distribution(specs)


def test_each_report_holds_one_strategy_with_its_letter_and_a_spanish_headline(built) -> None:  # type: ignore[no-untyped-def]
    files, entries, payloads = built
    assert list(files) == ["Estrategia_Alfa.html", "Estrategia_Beta.html", "Estrategia_Alfa_Costos_Altos.html", "Indice_Reportes.html"]
    assert [[s["id"] for s in p["strategies"]] for p in payloads] == [[ALFA], [BETA], [ALFA]]
    assert [p["strategies"][0]["label"] for p in payloads] == ["A", "B", "A"]  # the letter each has in the whole study
    for payload, entry in zip(payloads, entries):
        meta, n = payload["meta"], payload["strategies"][0]["summary"]["closed"]["TOTAL"]["n"]
        assert f"{n} operaciones cerradas válidas" in meta["subhead"] and entry["n"] == n and meta["index_href"] == "Indice_Reportes.html"
        assert ("escenario base" if meta["scenario"] == "BASE" else "costos altos (× 1,5)") in meta["subhead"]
        assert re.search(r"\d{1,2} (ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic) \d{4} a \d{1,2} \w{3} \d{4}", meta["subhead"])
    assert offline_report.day("2026-05-06T06:00:00+00:00") == "6 may 2026"


def test_a_strategy_the_study_does_not_have_is_refused(study) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(StudyError):
        offline_report.build_payload(study, period="COMPLETO", only=["otra@1.0.0"])
    with pytest.raises(StudyError):
        build_distribution([ReportSpec("x.html", study, "COMPLETO", "BASE", ALFA), ReportSpec("x.html", study, "COMPLETO", "BASE", BETA)])


def test_the_account_is_reconciled_with_the_valid_trades_and_the_indeterminate_ones(built) -> None:  # type: ignore[no-untyped-def]
    _, entries, payloads = built
    summary, entry = payloads[0]["strategies"][0]["summary"], entries[0]
    assert entry["indeterminate"] == 2 and Decimal(entry["indeterminate_conservative"]) < 0
    booked = Decimal(summary["closed"]["TOTAL"]["net_pnl"]) + Decimal(summary["indeterminate_conservative"])
    assert Decimal(summary["initial_capital"]) + booked == Decimal(summary["final_balance"])  # valid closed + indeterminate (conservative) = the balance
    assert Decimal(summary["return_fraction"]) == (booked / Decimal(summary["initial_capital"])).quantize(Decimal("0.0001"))
    assert booked != Decimal(summary["closed"]["TOTAL"]["net_pnl"])  # which is why the two figures differ


def test_the_index_links_every_report_and_nothing_outside_the_folder(built, tmp_path) -> None:  # type: ignore[no-untyped-def]
    files, entries, _ = built
    for name, text in files.items():
        (tmp_path / name).write_bytes(text.encode("utf-8"))  # as the script writes them: no newline translation
    index = files["Indice_Reportes.html"]
    data = embedded(index)
    assert [r["file"] for r in data["reports"]] == [e["file"] for e in entries] and all((tmp_path / r["file"]).is_file() for r in data["reports"])
    assert not re.search(EXTERNAL, index) and all(not re.search(EXTERNAL, text) for text in files.values())
    assert 'href="\' + encodeURI(r.file)' in index  # links are the reports' relative names
    for report in data["reports"]:
        raw = (tmp_path / report["file"]).read_bytes()
        assert report["bytes"] == len(raw) and report["sha256"] == hashlib.sha256(raw).hexdigest()
        assert report["study_sha256"] and report["results_sha256"] and report["dataset_sha256"] and report["id"]
        assert 'href="\' + esc(m.index_href)' in files[report["file"]] and embedded(files[report["file"]])["meta"]["index_href"] == "Indice_Reportes.html"
    assert any("no elige una ganadora" in w for w in data["warnings"]) and any("Resultado neto" in w for w in data["warnings"])
    home = Path.home()
    assert str(home) not in index and "\\Users\\" not in index


def test_a_copy_is_proved_against_the_earlier_export_and_any_difference_is_caught(study, built) -> None:  # type: ignore[no-untyped-def]
    _, _, payloads = built
    original = offline_report.report_html(offline_report.build_payload(study, period="COMPLETO"))  # the export with both strategies
    assert same_trades(payloads[0], original) == len(payloads[0]["strategies"][0]["trades"])
    assert same_trades(payloads[1], original) == len(payloads[1]["strategies"][0]["trades"])
    with pytest.raises(StudyError):  # another scenario is not the same run
        same_trades(payloads[2], original)
    altered = json.loads(json.dumps(payloads[0]))
    altered["strategies"][0]["trades"][0]["pnl"] = "999.99"
    with pytest.raises(StudyError, match="no coinciden"):
        same_trades(altered, original)
    with pytest.raises(StudyError):
        same_trades(payloads[0], "<html></html>")


def test_the_index_opens_in_a_real_browser_and_a_single_strategy_report_keeps_its_controls(built, tmp_path) -> None:  # type: ignore[no-untyped-def]
    files, _, _ = built
    outcome = run_selftest(files["Estrategia_Alfa.html"], tmp_path)
    (alfa,) = outcome["strategies"]
    assert outcome["check"] == "ok" and all(alfa[name] is True for name in BROWSER_CHECKS)
    dom = _dump(files["Indice_Reportes.html"], tmp_path / "indice")
    assert 'data-ready="1"' in dom and 'data-reports="3"' in dom and dom.count('<a class="open" href="Estrategia_') == 3
    assert 'href="Estrategia_Alfa.html"' in dom and 'href="Estrategia_Alfa_Costos_Altos.html"' in dom


def _dump(html: str, folder: Path) -> str:
    browser = _browser()
    if browser is None:
        pytest.skip("no hay Chrome ni Edge en esta máquina")
    folder.mkdir(parents=True, exist_ok=True)
    page = folder / "Indice_Reportes.html"
    page.write_text(html, encoding="utf-8")
    try:
        dom = subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions", "--host-resolver-rules=MAP * ~NOTFOUND",
                              f"--user-data-dir={folder / 'perfil'}", "--virtual-time-budget=5000", "--dump-dom", page.as_uri()],
                             capture_output=True, timeout=120).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("el navegador no se pudo ejecutar sin interfaz")
    if "<main>" not in dom:
        pytest.skip("el navegador no devolvió la página")
    return dom


# --- the real studies, when they are on this machine (read-only) ---

DISTRIBUTION = ROOT / "data" / "reports" / "Reportes_Trading"
real = pytest.mark.skipif(not (REAL / "manifest.json").exists() or not (REAL_C / "manifest.json").exists() or not REAL_C_BARS.is_dir(),
                          reason="los estudios reales no están en esta máquina")


@real
def test_the_real_distribution_holds_the_four_runs_with_their_counts() -> None:
    from investment_committee.simulation import ema_cross, ema_pullback, ema_trend_cross

    snapshot = find_snapshot(REAL, ROOT / "data" / "raw" / "snapshots")
    if snapshot is None:
        pytest.skip("el snapshot del estudio no está en esta máquina")
    ab, c = load_study(REAL, snapshot), load_study(REAL_C, REAL_C_BARS)
    files, entries, payloads = build_distribution([
        ReportSpec("Estrategia_A_Cruce_EMA_2026.html", ab, "COMUN", "BASE", ema_cross.STRATEGY_ID),
        ReportSpec("Estrategia_B_Cruce_Rapido_2026.html", ab, "COMUN", "BASE", ema_trend_cross.STRATEGY_ID),
        ReportSpec("Estrategia_C_Retrocesos_2025-2026_Base.html", c, "COMPLETO", "BASE", ema_pullback.STRATEGY_ID),
        ReportSpec("Estrategia_C_Retrocesos_2025-2026_Costos_Altos.html", c, "COMPLETO", "COSTOS_X1_5", ema_pullback.STRATEGY_ID)])
    assert [(e["label"], e["n"], e["net_pnl"], e["return_fraction"], e["final_balance"]) for e in entries] == [
        ("A", 60, "-1143.77", "-0.1144", "8856.23"), ("B", 111, "-855.98", "-0.0856", "9144.02"),
        ("C", 90, "-193.18", "-0.0290", "9710.46"), ("C", 88, "-529.34", "-0.0626", "9374.13")]
    # C: the return is the account's, which also booked the two indeterminate trades at their conservative result
    assert [(e["indeterminate"], e["indeterminate_conservative"]) for e in entries[2:]] == [(2, "-96.36"), (2, "-96.53")]
    for e in entries:
        assert Decimal(e["initial_capital"]) + Decimal(e["net_pnl"]) + Decimal(e["indeterminate_conservative"]) == Decimal(e["final_balance"])
    assert "60 operaciones cerradas válidas" in payloads[0]["meta"]["subhead"] and "6 may 2026 a 30 sep 2026" in payloads[0]["meta"]["subhead"]
    assert "88 operaciones cerradas válidas" in payloads[3]["meta"]["subhead"] and "costos altos" in payloads[3]["meta"]["subhead"]
    assert len(files) == 5 and all(len(text.encode("utf-8")) == e["bytes"] for text, e in zip(files.values(), entries))
    for name, entry in zip(list(files)[:4], entries):  # what is on disk, if the folder was written, is exactly this
        if (DISTRIBUTION / name).is_file():
            assert hashlib.sha256((DISTRIBUTION / name).read_bytes()).hexdigest() == entry["sha256"]
