"""Review notes (standard library) and the Streamlit dashboard driven by
Streamlit's own test harness on the synthetic study. The dashboard tests
skip themselves when the optional viewer dependencies are missing."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from investment_committee.interfaces.trade_viewer.notes import RATINGS, NoteKey, NoteStore

from tests.simulation._forex_backtest_support import HOUR, T0

ROOT = Path(__file__).resolve().parents[3]
ALFA = "alfa@0.0.0"
WHEN = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)


def tid(offset: int, symbol: str = "EURUSD=X") -> str:
    return f"{ALFA}|{symbol}|{(T0 + offset * HOUR).isoformat()}"


def digest(folder: Path) -> dict[str, str]:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.rglob("*")) if p.is_file()}


# --- notes ---


def test_a_note_is_kept_by_study_trade_scenario_and_period(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = NoteStore(tmp_path / "reviews" / "estudio" / "notes.jsonl")
    base, other_scenario, other_period, other_study = (NoteKey("h1", tid(0), "BASE", "COMUN"), NoteKey("h1", tid(0), "COSTOS_X2", "COMUN"),
                                                       NoteKey("h1", tid(0), "BASE", "COMPLETO"), NoteKey("h2", tid(0), "BASE", "COMUN"))
    store.add(base, rating="Mala entrada", comment=" contra tendencia ", problem="cruce débil", improvement="exigir pendiente", reviewer="Ana", reviewed_at=WHEN)

    note = store.current(base)
    assert (note.rating, note.comment, note.problem, note.improvement, note.reviewer) == ("Mala entrada", "contra tendencia", "cruce débil", "exigir pendiente", "Ana")
    assert note.reviewed_at == "2026-11-12T15:00:00+00:00" and note.key == base
    assert [store.current(k) for k in (other_scenario, other_period, other_study)] == [None, None, None]
    assert store.current_by_trade("h1", "BASE", "COMUN") == {tid(0): note} and store.current_by_trade("h1", "COSTOS_X2", "COMUN") == {}
    store.add(other_scenario, rating="Buena entrada", comment="con costos dobles", reviewed_at=WHEN)
    assert (store.current(base).rating, store.current(other_scenario).rating) == ("Mala entrada", "Buena entrada")  # each keeps its own
    assert len(store.history(base)) == len(store.history(other_scenario)) == 1 and store.current(other_period) is None


def test_saving_again_adds_to_the_history_and_never_rewrites_a_line(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "notes.jsonl"
    store, key = NoteStore(path), NoteKey("h1", tid(0), "BASE", "COMUN")
    store.add(key, rating="Pendiente", reviewed_at=WHEN)
    first_line = path.read_bytes()
    store.add(key, rating="Discutible", comment="segunda lectura", reviewed_at=WHEN + HOUR)
    store.add(NoteKey("h1", tid(5), "BASE", "COMUN"), rating="Buena entrada", reviewed_at=WHEN)

    assert path.read_bytes().startswith(first_line) and path.read_bytes().count(b"\n") == 3 and b"\r" not in path.read_bytes()
    assert [n.rating for n in store.history(key)] == ["Pendiente", "Discutible"] and store.current(key).comment == "segunda lectura"
    assert [json.loads(line)["trade_id"] for line in path.read_text(encoding="utf-8").splitlines()] == [tid(0), tid(0), tid(5)]
    assert len(store.all()) == 3 and NoteStore(tmp_path / "otra.jsonl").all() == []


def test_only_the_four_ratings_and_aware_dates_are_accepted(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store, key = NoteStore(tmp_path / "notes.jsonl"), NoteKey("h1", tid(0), "BASE", "COMUN")

    assert RATINGS == ("Buena entrada", "Mala entrada", "Discutible", "Pendiente")
    with pytest.raises(ValueError):
        store.add(key, rating="Excelente")
    with pytest.raises(ValueError):
        store.add(key, rating="Pendiente", reviewed_at=datetime(2026, 1, 1))
    assert not (tmp_path / "notes.jsonl").exists()


def test_notes_live_outside_the_study_and_leave_it_untouched(study_paths, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from investment_committee.interfaces.trade_viewer.study_store import load_study

    before = digest(study_paths[0])
    study = load_study(*study_paths)
    NoteStore(tmp_path / "reviews" / study.name / "notes.jsonl").add(NoteKey(study.study_sha256, tid(0), "BASE", "COMPLETO"), rating="Discutible")

    assert digest(study_paths[0]) == before and not list(study_paths[0].rglob("notes.jsonl"))


# --- project settings ---


def test_the_dashboard_is_configured_for_this_computer_only() -> None:
    config = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")

    assert 'address = "127.0.0.1"' in config and "gatherUsageStats = false" in config and "0.0.0.0" not in config


def test_the_viewer_dependencies_are_optional() -> None:
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    core, _, extras = project.partition("[project.optional-dependencies]")

    assert all(name in extras.partition("viewer = [")[2] for name in ("plotly", "kaleido", "streamlit"))
    assert not any(name in core for name in ("plotly", "kaleido", "streamlit"))
    assert "data/reviews/" in (ROOT / ".gitignore").read_text(encoding="utf-8")


# --- the dashboard ---


@pytest.fixture()
def app(study_paths, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    pytest.importorskip("streamlit")
    pytest.importorskip("plotly")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("TRADE_VIEWER_STUDIES", str(study_paths[0].parent))
    monkeypatch.setenv("TRADE_VIEWER_SNAPSHOTS", str(study_paths[1].parent))
    monkeypatch.setenv("TRADE_VIEWER_REVIEWS", str(tmp_path / "reviews"))
    monkeypatch.setenv("TRADE_VIEWER_EXPORTS", str(tmp_path / "exports"))
    return AppTest.from_file(str(ROOT / "scripts" / "trade_viewer.py"), default_timeout=120)


def texts(at) -> str:  # type: ignore[no-untyped-def]
    parts = [e.value for group in (at.title, at.subheader, at.markdown, at.caption, at.warning, at.error, at.info, at.success) for e in group]
    return "\n".join(str(p) for p in parts)


def test_the_dashboard_opens_on_the_first_run_in_spanish_and_bogota_time(app, study_paths) -> None:  # type: ignore[no-untyped-def]
    before = digest(study_paths[0])
    at = app.run()

    assert not at.exception and not at.error
    assert at.selectbox(key="study").value == "estudio_sintetico" and at.selectbox(key="zone").value == "Bogotá"
    assert at.selectbox(key="strategy").options == [ALFA, "beta@0.0.0"] and at.selectbox(key="period").options == ["COMPLETO", "COMUN"]
    shown = texts(at)
    for part in ("Visor de operaciones simuladas", "ESTIMADO", f"{ALFA} · período COMPLETO · escenario BASE · par Todos", "Revisión humana", "8 de 8 operaciones"):
        assert part in shown, part
    assert [m.label for m in at.metric][:4] == ["Capital inicial → final (cuenta)", "Resultado de la cuenta", "Aciertos · n = 6", "Expectativa por operación"]
    assert digest(study_paths[0]) == before  # looking changes nothing


def test_choosing_run_pair_and_trade_shows_that_trades_sheet(app) -> None:  # type: ignore[no-untyped-def]
    at = app.run()
    at.selectbox(key="scenario").select("COSTOS_X2").run()
    at.selectbox(key="pair").select("EURUSD").run()
    at.selectbox(key="zone").select("UTC").run()
    at.selectbox(key="trade").select(tid(5)).run()

    assert not at.exception and "escenario COSTOS_X2 · par EURUSD" in texts(at)
    cells = json.dumps([t.value.to_dict() for t in at.table], ensure_ascii=False, default=str)
    for part in (tid(5), "EURUSD SHORT", "2026-12-01 19:00 UTC (UTC+00:00)", "COSTOS_X2 / COMPLETO", "ESTIMADO", "DERIVADO", "OBSERVADO", "Lotes estándar"):
        assert part in cells, part
    assert tid(1, "GBPUSD=X") not in at.selectbox(key="trade").options


def test_a_review_is_saved_to_its_own_file_and_shown_in_the_history(app, study_paths, tmp_path) -> None:  # type: ignore[no-untyped-def]
    before = digest(study_paths[0])
    at = app.run()
    at.selectbox(key="trade").select(tid(0)).run()
    own = f"{tid(0)}|BASE|COMPLETO"
    at.radio(key=f"rating|{own}").set_value("Mala entrada")
    at.text_area(key=f"comment|{own}").input("stop demasiado corto")
    at.text_input(key=f"reviewer|{own}").input("Ana")
    [b for b in at.button if b.label == "Guardar revisión"][0].click().run()

    assert not at.exception and "Revisión guardada" in texts(at)
    saved = NoteStore(tmp_path / "reviews" / "estudio_sintetico" / "notes.jsonl").all()
    assert [(n.trade_id, n.scenario, n.period, n.rating, n.comment, n.reviewer) for n in saved] == [(tid(0), "BASE", "COMPLETO", "Mala entrada", "stop demasiado corto", "Ana")]
    assert digest(study_paths[0]) == before
    at.selectbox(key="scenario").select("COSTOS_X2").run()
    at.selectbox(key="trade").select(tid(0)).run()
    assert at.radio(key=f"rating|{tid(0)}|COSTOS_X2|COMPLETO").value == "Pendiente"  # the same signal under another scenario has no review yet
    assert at.text_area(key=f"comment|{tid(0)}|COSTOS_X2|COMPLETO").value == ""


def test_a_study_that_fails_verification_is_not_drawn(study_paths, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    pytest.importorskip("streamlit")
    pytest.importorskip("plotly")
    import shutil

    from streamlit.testing.v1 import AppTest

    copy = tmp_path / "backtests" / "estudio_sintetico"
    shutil.copytree(study_paths[0], copy)
    target = copy / ALFA / "COMPLETO" / "BASE" / "run.json"
    target.write_bytes(target.read_bytes().replace(b'"net_pnl": "-48.64"', b'"net_pnl": "948.64"', 1))
    for name, value in (("STUDIES", copy.parent), ("SNAPSHOTS", study_paths[1].parent), ("REVIEWS", tmp_path / "r"), ("EXPORTS", tmp_path / "e")):
        monkeypatch.setenv(f"TRADE_VIEWER_{name}", str(value))
    at = AppTest.from_file(str(ROOT / "scripts" / "trade_viewer.py"), default_timeout=120).run()

    assert not at.exception and "no pasó la verificación de integridad" in at.error[0].value and "run.json" in at.error[0].value
    assert not at.metric and not at.table


def test_the_shareable_report_is_built_from_the_chosen_trades_and_their_notes(app, study_paths, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from investment_committee.interfaces.trade_viewer.study_store import load_study

    study = load_study(*study_paths)
    NoteStore(tmp_path / "reviews" / study.name / "notes.jsonl").add(
        NoteKey(study.study_sha256, tid(5), "BASE", "COMPLETO"), rating="Buena entrada", comment="a favor de la tendencia", reviewer="Ana")
    before = digest(study_paths[0])
    at = app.run()

    assert at.multiselect(key="report_trades").value == [tid(5)]  # the reviewed trades are proposed
    at.multiselect(key="report_trades").set_value([tid(5), tid(10)]).run()
    [b for b in at.button if b.label == "Preparar reporte"][0].click().run()

    assert not at.exception and "Reporte guardado" in texts(at)
    written = list((tmp_path / "exports" / study.name).iterdir())
    assert [p.name for p in written] == ["reporte_alfa-0.0.0_COMPLETO_BASE.html"]
    page = written[0].read_text(encoding="utf-8")
    for part in (tid(5), tid(10), "Buena entrada", "a favor de la tendencia", "INDETERMINADA", study.study_sha256, "SINTETICA", "Limitaciones",
                 "Operaciones cerradas válidas (n)", "Simulación con modelo de ejecución ESTIMADO"):
        assert part in page, part
    assert tid(0) not in page and str(tmp_path) not in page and str(study_paths[0].parent) not in page
    assert digest(study_paths[0]) == before
