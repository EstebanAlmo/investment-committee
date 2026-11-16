"""The local dashboard of the trade viewer (Streamlit). Needs the optional
`viewer` dependencies; started by scripts/trade_viewer.py and served on
127.0.0.1 only (.streamlit/config.toml).

A reading layer: it shows what a stored study recorded and writes only
review notes and exports, each in its own folder. It never runs a
backtest, never changes a study and never talks to a broker or the
network. Every screen names the run it shows (strategy, period, scenario),
so results of different scenarios are never mixed unlabelled.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import streamlit as st

from . import charts, export
from .notes import RATINGS, NoteKey, NoteStore
from .study_store import Run, RunKey, Signals, Study, StudyError, check_signals, find_snapshot, instant, load_run, load_signals, load_study
from .trade_view import DEFAULT_ZONE, SIMULATION_WARNING, ZONES, build_trade_view, compare_interval, show_time, table_row

LIMITATIONS = (
    "Precio indicativo único de Yahoo por hora: no hay bid/ask observado; el spread se asume constante.",
    "Entradas, stops, objetivos y salidas son ejecuciones ESTIMADAS por el modelo; ninguna fue ejecutable.",
    "El orden de los hechos dentro de una hora no se conoce: una salida dentro de una vela no tiene instante, y algunas operaciones quedan INDETERMINADAS.",
    "Spread, slippage, comisión y financiación son supuestos sin verificar; ningún resultado es una rentabilidad neta real.",
    "El drawdown usa marcas horarias y puede subestimar el riesgo.",
    "El período ya había sido inspeccionado: no es una validación independiente ni permite elegir una estrategia ganadora.",
    "Las notas de revisión son cualitativas; cualquier cambio de reglas sería una versión nueva de la estrategia, a probar con datos no vistos.",
)


@st.cache_resource(show_spinner="Verificando el estudio y el snapshot…")
def _study(directory: str, snapshot: str) -> Study:
    return load_study(Path(directory), Path(snapshot))


@st.cache_resource(show_spinner="Verificando la corrida…")
def _run(directory: str, snapshot: str, strategy: str, period: str, scenario: str) -> tuple[Run, Signals]:
    study = _study(directory, snapshot)
    run, signals = load_run(study, RunKey(strategy, period, scenario)), _signals(directory, snapshot, strategy)
    check_signals(run, signals)
    return run, signals


@st.cache_resource(show_spinner=False)
def _signals(directory: str, snapshot: str, strategy: str) -> Signals:
    return load_signals(_study(directory, snapshot), strategy)


def _percent(fraction: Any) -> str:
    return "no definido" if fraction is None else f"{(Decimal(str(fraction)) * 100).quantize(Decimal('0.01'))} %"


def summary_rows(summary: Mapping[str, Any], scope: str = "TOTAL") -> list[tuple[str, str]]:
    """The figures of a run for the overview and the shared report. Account
    figures belong to the whole run; trade statistics to `scope`."""
    stats, funnel = summary["closed"][scope], summary["funnel"][scope]
    interval = stats["win_rate_interval_95"]
    return [
        ("Estrategia", summary["strategy_id"]), ("Escenario", summary["scenario"]), ("Alcance de las estadísticas", scope),
        ("Capital inicial", f"{summary['initial_capital']} USD"), ("Saldo realizado final (cuenta completa)", f"{summary['final_balance']} USD"),
        ("Resultado de la cuenta", f"{Decimal(summary['final_balance']) - Decimal(summary['initial_capital'])} USD ({_percent(summary['return_fraction'])})"),
        ("Señales / rechazadas / abiertas", f"{funnel['signals']} / {funnel['rejected']} / {funnel['opened']}"),
        ("Cerradas válidas / indeterminadas / abiertas al final", f"{funnel['closed_valid']} / {funnel['indeterminate']} / {funnel['open_at_end']}"),
        ("Operaciones cerradas válidas (n)", f"{stats['n']} ({stats['sample']})"),
        ("Porcentaje de aciertos", _percent(stats["win_rate"]) + (f" (Wilson 95 %: {_percent(interval[0])} a {_percent(interval[1])})" if interval else "")),
        ("Expectativa", "no definido" if stats["expectancy_r"] is None else f"{stats['expectancy_r']} R"),
        ("Profit factor", "no definido" if stats["profit_factor"] is None else str(stats["profit_factor"])),
        ("Resultado de las cerradas válidas", f"{stats['net_pnl']} USD"),
        ("Drawdown sobre saldo realizado", _percent(summary["drawdown_balance"]["fraction"])),
        ("Drawdown sobre equity estimado", _percent(summary["drawdown_equity"]["fraction"])),
        ("Indeterminadas: lectura conservadora / optimista", f"{summary['indeterminate_conservative']} / {summary['indeterminate_optimistic']} USD"),
    ]


def _plain(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows for st.dataframe: decimals as floats so that columns sort as numbers."""
    return [{k: (float(v) if isinstance(v, Decimal) else v) for k, v in row.items()} for row in rows]


def main(studies: Path, snapshots: Path, reviews: Path, exports: Path) -> None:
    st.set_page_config(page_title="Visor de operaciones simuladas", layout="wide")
    st.title("Visor de operaciones simuladas · Forex H1")
    st.warning(SIMULATION_WARNING + " No es una validación independiente ni una recomendación.")

    available = sorted(p.name for p in studies.iterdir() if (p / "manifest.json").is_file()) if studies.is_dir() else []
    if not available:
        st.error("No hay ningún estudio en la carpeta de estudios (data/reports/backtests). Ejecute primero el backtest.")
        st.stop()
    side = st.sidebar
    side.header("Corrida")
    name = side.selectbox("Estudio", available, key="study")
    directory = studies / name
    try:
        found = find_snapshot(directory, snapshots)
        if found is None:
            side.error("No se encuentra el snapshot que nombra el manifiesto en la carpeta de snapshots (data/raw/snapshots).")
            st.stop()
        study = _study(str(directory), str(found))
        strategy = side.selectbox("Estrategia", study.strategies, key="strategy")
        period = side.selectbox("Período", study.options("period"), key="period", help="COMPLETO: desde la primera hora evaluable de la estrategia. "
                                "COMUN: desde el inicio común de todas, con una cuenta nueva.")
        scenario = side.selectbox("Escenario de costos", study.options("scenario"), key="scenario")
        run, signals = _run(str(directory), str(found), strategy, period, scenario)
    except StudyError as error:
        st.error(f"El estudio no pasó la verificación de integridad y no se dibuja nada: {error}")
        st.stop()
    pair = side.selectbox("Par", ["Todos", *study.bars], key="pair")
    zone = side.selectbox("Zona horaria (solo presentación)", list(ZONES), index=list(ZONES).index(DEFAULT_ZONE), key="zone")
    key = run.key
    side.caption(f"Datos: {study.manifest['data_kind']} · estudio {study.study_sha256[:12]} · commit {str(study.manifest['commit'])[:7]} · "
                 f"motor {study.manifest['engine']}")
    side.caption(str(study.manifest["calendar"]))
    st.subheader(f"{strategy} · período {period} · escenario {scenario} · par {pair}")

    scope = "TOTAL" if pair == "Todos" else pair
    rows = [table_row(t, key, zone) for t in run.trades if pair == "Todos" or t["symbol"] == pair]
    store = NoteStore(reviews / study.name / "notes.jsonl")
    overview, explorer, single, versus, sharing = st.tabs(["Resumen", "Operaciones", "Operación", "Comparación A/B", "Revisión y exportación"])

    with overview:
        summary = run.summary
        stats = summary["closed"][scope]
        a, b, c, d = st.columns(4)
        a.metric("Capital inicial → final (cuenta)", f"{summary['final_balance']} USD", f"{Decimal(summary['final_balance']) - Decimal(summary['initial_capital'])} USD")
        b.metric("Resultado de la cuenta", _percent(summary["return_fraction"]))
        c.metric(f"Aciertos · n = {stats['n']}", _percent(stats["win_rate"]), stats["sample"], delta_color="off")
        d.metric("Expectativa por operación", "no definido" if stats["expectancy_r"] is None else f"{stats['expectancy_r']} R")
        a, b, c, d = st.columns(4)
        a.metric("Profit factor", "no definido" if stats["profit_factor"] is None else str(stats["profit_factor"]))
        b.metric("Drawdown saldo / equity", f"{_percent(summary['drawdown_balance']['fraction'])} / {_percent(summary['drawdown_equity']['fraction'])}")
        c.metric("Cerradas válidas / indeterminadas / abiertas", f"{summary['funnel'][scope]['closed_valid']} / {summary['funnel'][scope]['indeterminate']} / "
                 f"{summary['funnel'][scope]['open_at_end']}")
        d.metric("Señales rechazadas", f"{summary['funnel'][scope]['rejected']} de {summary['funnel'][scope]['signals']}")
        if scope != "TOTAL":
            st.caption("Capital, resultado de la cuenta, drawdown y curvas son de la cuenta completa (ambos pares); las estadísticas de operaciones son del par elegido.")
        st.table([{"Métrica": label, "Valor": value} for label, value in summary_rows(summary, scope)])
        st.plotly_chart(charts.equity_figure(run, zone), width="stretch")
        st.plotly_chart(charts.drawdown_figure(run, zone), width="stretch")
        left, right = st.columns(2)
        left.plotly_chart(charts.monthly_figure(summary), width="stretch")
        right.plotly_chart(charts.distribution_figure(summary, scope), width="stretch")
        left.plotly_chart(charts.exits_figure(summary, scope), width="stretch")
        right.markdown("**Motivos de rechazo (del motor; una señal puede tener varios)**")
        right.table([{"Motivo": reason, "Señales": count} for reason, count in summary["funnel"][scope]["rejections"].items()])
        st.markdown(f"**Todas las estrategias · período {period} · escenario {scenario}** (misma ventana y mismos supuestos; no elige una ganadora)")
        st.table([dict(summary_rows(_run(str(directory), str(found), other, period, scenario)[0].summary, scope)) for other in study.strategies])

    with explorer:
        columns = st.columns(4)
        directions = columns[0].multiselect("Dirección", ["LONG", "SHORT"], key="f_direction")
        outcomes = columns[1].multiselect("Resultado", sorted({r["Resultado"] for r in rows}), key="f_outcome")
        reasons = columns[2].multiselect("Motivo de salida", sorted({r["Motivo de salida"] for r in rows}), key="f_reason")
        days = sorted({instant(r["Entrada (UTC)"]).astimezone(ZoneInfo(ZONES[zone])).date() for r in rows})  # type: ignore[union-attr]
        span = columns[3].date_input("Entradas entre", (days[0], days[-1]) if days else (), key="f_dates") if days else ()
        shown = [r for r in rows
                 if (not directions or r["Dirección"] in directions) and (not outcomes or r["Resultado"] in outcomes)
                 and (not reasons or r["Motivo de salida"] in reasons)
                 and (len(span) != 2 or span[0] <= instant(r["Entrada (UTC)"]).astimezone(ZoneInfo(ZONES[zone])).date() <= span[1])]  # type: ignore[union-attr,index,misc]
        st.caption(f"{len(shown)} de {len(rows)} operaciones · {strategy} · {period} · {scenario}. Seleccione una fila para abrirla en la pestaña «Operación».")
        event = st.dataframe(_plain(shown), width="stretch", hide_index=True, on_select="rerun", selection_mode="single-row", key="table")
        picked = event["selection"]["rows"] if event and "selection" in event else []
        if picked:
            st.session_state["trade"] = shown[picked[0]]["ID"]
        st.download_button("Descargar CSV de las operaciones filtradas", export.trades_csv(shown, study.study_sha256, study.manifest["data_kind"]),
                           file_name=f"operaciones_{strategy}_{period}_{scenario}.csv".replace("@", "-"), mime="text/csv")

    identifiers = [r["ID"] for r in rows]
    with single:
        if not identifiers:
            st.info("Esta corrida no tiene operaciones para el par elegido.")
        else:
            if st.session_state.get("trade") not in identifiers:
                st.session_state["trade"] = identifiers[0]
            back, forward, _ = st.columns([1, 1, 6])
            position = identifiers.index(st.session_state["trade"])
            if back.button("← Anterior", disabled=position == 0):
                st.session_state["trade"] = identifiers[position - 1]
            if forward.button("Siguiente →", disabled=position == len(identifiers) - 1):
                st.session_state["trade"] = identifiers[position + 1]
            trade_id = st.selectbox("Operación", identifiers, key="trade")
            try:
                view = build_trade_view(study, run, signals, trade_id, zone)
            except StudyError as error:
                st.error(f"No se puede reconstruir esta operación con fidelidad: {error}")
                st.stop()
            options = st.columns(3)
            rejected = options[0].toggle("Mostrar señales rechazadas", key="show_rejected")
            rsi = options[1].toggle("Panel RSI14", value=True, key="show_rsi")
            atr = options[2].toggle("Panel ATR14", value=True, key="show_atr")
            st.plotly_chart(charts.trade_figure(view, zone, show_rejected=rejected, show_rsi=rsi, show_atr=atr), width="stretch")
            st.caption("Velas: precio medio de Yahoo. Niveles: ejecución ESTIMADA. Líneas punteadas finas: los mismos niveles llevados al precio medio (DERIVADO). "
                       "Una salida dentro de una vela sombrea la vela entera: el instante dentro de la hora no se conoce.")
            if view.holes:
                st.warning("Horas esperadas sin vela en la ventana: " + "; ".join(f"{len(m)} h desde {show_time(m[0], zone)}" for _, m in view.holes))
            if rejected and view.rejected:
                st.table([{"Hora": show_time(r.at, zone), "Dirección": r.direction, "Motivos (del motor)": " + ".join(r.reasons), "Detalle": r.detail}
                          for r in view.rejected])
            for section in dict.fromkeys(f.section for f in view.sheet):
                st.markdown(f"**{section}**")
                st.table([{"Campo": f.label, "Valor": f.value, "Tipo de dato": f.kind} for f in view.sheet if f.section == section])

            note_key = NoteKey(study.study_sha256, trade_id, scenario, period)
            current = store.current(note_key)
            st.markdown("### Revisión humana")
            st.caption("Cualitativa. Se guarda en un archivo aparte; no modifica la operación, la señal ni los parámetros.")
            own = f"{trade_id}|{scenario}|{period}"  # each trade of each run has its own form state: nothing carries over
            with st.form(f"review|{own}"):
                rating = st.radio("Calificación", RATINGS, index=RATINGS.index(current.rating) if current else 3, horizontal=True, key=f"rating|{own}")
                comment = st.text_area("Comentario", current.comment if current else "", key=f"comment|{own}")
                problem = st.text_input("Problema identificado", current.problem if current else "", key=f"problem|{own}")
                improvement = st.text_input("Mejora sugerida (hipótesis para una versión futura; no cambia esta)", current.improvement if current else "",
                                            key=f"improvement|{own}")
                reviewer = st.text_input("Revisor", current.reviewer if current else "", key=f"reviewer|{own}")
                if st.form_submit_button("Guardar revisión"):
                    store.add(note_key, rating=rating, comment=comment, problem=problem, improvement=improvement, reviewer=reviewer)
                    st.success("Revisión guardada (se añadió al historial).")
            history = store.history(note_key)
            if history:
                st.table([{"Fecha": show_time(instant(n.reviewed_at), zone), "Calificación": n.rating, "Comentario": n.comment, "Problema": n.problem,
                           "Mejora sugerida": n.improvement, "Revisor": n.reviewer} for n in reversed(history)])
            latest = store.current(note_key)
            note = None if latest is None else {**latest.__dict__, "reviewed_at": show_time(instant(latest.reviewed_at), zone)}
            st.download_button("Descargar HTML interactivo de esta operación", export.trade_html(view, zone, note=note, show_rejected=rejected),
                               file_name=export.safe_name(view) + ".html", mime="text/html")
            if st.button("Generar PNG de esta operación"):
                try:
                    st.download_button("Descargar PNG", export.trade_png(view, zone, show_rejected=rejected), file_name=export.safe_name(view) + ".png", mime="image/png")
                except export.PngUnavailable as error:
                    st.warning(str(error))

    with versus:
        st.caption(f"Todas las estrategias del estudio en el mismo período ({period}) y escenario ({scenario}). Cada cuenta es independiente: "
                   "las operaciones no son equivalentes aunque coincidan en el tiempo.")
        symbols = list(study.bars)
        symbol = st.selectbox("Par", symbols, index=symbols.index(pair) if pair in symbols else 0, key="v_pair")
        first, last = study.bars[symbol][0].timestamp.astimezone(ZoneInfo(ZONES[zone])).date(), study.bars[symbol][-1].timestamp.astimezone(ZoneInfo(ZONES[zone])).date()
        selected = st.session_state.get("trade")
        default = instant(run.trade(selected)["entry_at"]).astimezone(ZoneInfo(ZONES[zone])).date() if selected in identifiers else first  # type: ignore[union-attr]
        day = st.date_input(f"Desde el día ({zone})", min(max(default, first), last), min_value=first, max_value=last, key="v_day")
        hours = st.slider("Horas a mostrar", 24, 240, 72, step=24, key="v_hours")
        start = datetime.combine(day, time(0, 0), tzinfo=ZoneInfo(ZONES[zone]))
        try:
            others = [_run(str(directory), str(found), other, period, scenario)[0] for other in study.strategies]
            interval = compare_interval(study, others, symbol, start, start + timedelta(hours=hours))
        except StudyError as error:
            st.error(str(error))
            st.stop()
        st.markdown(f"**Resultado:** {interval.verdict} · mismas condiciones: {'sí' if interval.same_conditions else 'NO'}")
        for line in interval.notes:
            st.write("• " + line)
        if interval.bars:
            st.plotly_chart(charts.interval_figure(interval, zone), width="stretch")
        else:
            st.info("No hay velas en ese intervalo.")
        for activity in interval.activity:
            st.markdown(f"**{activity.key.strategy_id}** · {len(activity.trades)} operaciones en el intervalo · {len(activity.rejected)} señales rechazadas")
            if activity.trades:
                st.dataframe(_plain([table_row(t, activity.key, zone) for t in activity.trades]), width="stretch", hide_index=True)
            if activity.rejected:
                st.table([{"Hora": show_time(instant(d["at"]), zone), "Dirección": d["direction"], "Motivos (del motor)": " + ".join(d["proposal"]["rejections"])}
                          for d in activity.rejected])

    with sharing:
        reviewed = store.current_by_trade(study.study_sha256, scenario, period)
        st.markdown(f"**Operaciones revisadas en esta corrida:** {len(reviewed)}")
        if reviewed:
            st.table([{"ID": n.trade_id, "Calificación": n.rating, "Comentario": n.comment, "Problema": n.problem, "Mejora sugerida": n.improvement,
                       "Revisor": n.reviewer, "Fecha": show_time(instant(n.reviewed_at), zone)} for n in reviewed.values()])
        chosen = st.multiselect("Operaciones para el reporte compartible", identifiers, default=[i for i in identifiers if i in reviewed][:20], key="report_trades")
        st.caption("El reporte es un único HTML con métricas, limitaciones y cada operación elegida (gráfico, ficha y nota). No contiene rutas ni datos de este equipo.")
        if st.button("Preparar reporte", disabled=not chosen):
            views = [build_trade_view(study, run, signals, trade_id, zone) for trade_id in chosen]
            notes = {i: {**n.__dict__, "reviewed_at": show_time(instant(n.reviewed_at), zone)} for i, n in reviewed.items()}
            page = export.report_html(f"Revisión de operaciones simuladas · {strategy} · {period} · {scenario}", views,
                                      summary_rows=summary_rows(run.summary, scope), limitations=LIMITATIONS, zone=zone, notes=notes)
            file_name = f"reporte_{strategy}_{period}_{scenario}.html".replace("@", "-")
            export.write_export(exports / study.name, file_name, page)
            st.success(f"Reporte guardado en la carpeta de exportaciones del estudio como {file_name}.")
            st.download_button("Descargar reporte HTML", page, file_name=file_name, mime="text/html")
