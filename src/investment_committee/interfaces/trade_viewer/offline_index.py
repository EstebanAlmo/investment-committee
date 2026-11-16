"""A folder of offline reports to hand over together: one HTML per strategy
and scenario (offline_report.py) and an index page that links them, with
what each strategy is, its main figures and the warnings that go with
them. Standard library only; every page works from file://, without
internet, and links to the others by relative name.

Nothing is recomputed and no original is touched: each report is built
again from its verified study (study_store checks every hash), under a
name a reader can tell apart. same_trades() then proves that a
distribution copy holds exactly the trades of a report exported earlier.

The index states, for every report, both figures that are easy to mix up:
the net result of the VALID CLOSED trades, and the account's return, which
also books the indeterminate trades (at their conservative result) and
leaves an open position out.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from investment_committee.simulation import ema_cross, ema_pullback, ema_trend_cross

from .offline_report import SCENARIO_NAMES, build_payload, day, report_html
from .study_store import Study, StudyError
from .trade_view import SIMULATION_WARNING

INDEX_NAME = "Indice_Reportes.html"
COMPARED = ("id", "valid", "outcome", "sym", "dir", "t_in", "t_out", "entry", "stop", "target", "exit", "units", "pnl", "r", "reason")

# What the index says about each strategy's evidence. Statements of record, not of merit.
STANDING: Mapping[str, str] = {
    ema_cross.STRATEGY_ID: "Diagnóstico sobre un período ya inspeccionado muchas veces: no es una validación. Pierde en todos los escenarios de costos del estudio.",
    ema_trend_cross.STRATEGY_ID: "Diagnóstico sobre un período ya inspeccionado muchas veces: no es una validación. Pierde en todos los escenarios de costos del "
                                 "estudio; sus entradas no se distinguieron de entradas al azar.",
    ema_pullback.STRATEGY_ID: "Prueba retrospectiva registrada de antemano, ejecutada una sola vez. Veredicto registrado: NO CONCLUYENTE (menos de las 200 "
                              "operaciones exigidas). Ninguna cifra es evidencia a favor ni en contra.",
}
GENERAL_WARNINGS = (
    SIMULATION_WARNING,
    "Todos los precios son el precio medio indicativo único de Yahoo Finance (H1), sin bid/ask real. Spread, slippage, comisión y financiación son supuestos.",
    "Las estrategias se probaron en períodos y con datos distintos: sus cifras no son comparables entre sí y este índice no elige una ganadora.",
    "«Resultado neto» suma solo las operaciones cerradas válidas. «Retorno de la cuenta» es el saldo final frente al capital inicial: incluye además las "
    "operaciones indeterminadas, contabilizadas con su resultado conservador (stop primero), y excluye la posición abierta al final. Por eso pueden diferir.",
    "Ningún resultado es una rentabilidad real, una validación independiente ni una recomendación de inversión.",
)


@dataclass(frozen=True)
class ReportSpec:
    file: str  # name of the HTML in the folder
    study: Study
    period: str
    scenario: str
    strategy_id: str


def embedded(html: str) -> dict[str, Any]:
    """The data a report page carries."""
    found = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)
    if found is None:
        raise StudyError("el archivo no es un reporte de operaciones: no lleva datos embebidos")
    return json.loads(found.group(1))


def same_trades(payload: Mapping[str, Any], original_html: str) -> int:
    """How many trades `payload` shares, field for field, with the report in
    `original_html`, for each of its strategies. Raises StudyError unless
    every trade of those strategies is in both and identical, with the same
    study, dataset and results hashes."""
    original = embedded(original_html)
    theirs = {s["id"]: s for s in original["strategies"]}
    count = 0
    for key in ("study_sha256", "dataset_sha256", "period", "scenario"):
        if payload["meta"][key] != original["meta"][key]:
            raise StudyError(f"la copia y el original difieren en {key}")
    for mine in payload["strategies"]:
        other = theirs.get(mine["id"])
        if other is None or mine["results_sha256"] != other["results_sha256"] or mine["summary"]["closed"] != other["summary"]["closed"]:
            raise StudyError(f"la copia de {mine['id']} no procede de la misma corrida que el original")
        if [[t[k] for k in COMPARED] for t in mine["trades"]] != [[t[k] for k in COMPARED] for t in other["trades"]]:
            raise StudyError(f"las operaciones de {mine['id']} no coinciden con el reporte original")
        count += len(mine["trades"])
    return count


def _entry(spec: ReportSpec, payload: Mapping[str, Any], html: str) -> dict[str, Any]:
    (strategy,) = payload["strategies"]
    summary, total = strategy["summary"], strategy["summary"]["closed"]["TOTAL"]
    data = html.encode("utf-8")
    return {
        "file": spec.file, "label": strategy["label"], "id": strategy["id"], "title": strategy["title"], "rules": strategy["rules"],
        "period": f"{day(summary['start'])} a {day(summary['end'])}", "start": summary["start"][:10], "scenario_id": spec.scenario,
        "scenario": SCENARIO_NAMES.get(spec.scenario, spec.scenario), "period_id": spec.period, "n": total["n"], "wins": total["wins"],
        "losses": total["losses"], "win_rate": total["win_rate"], "win_rate_interval_95": total["win_rate_interval_95"],
        "expectancy_r": total["expectancy_r"], "profit_factor": total["profit_factor"], "net_pnl": total["net_pnl"],
        "initial_capital": summary["initial_capital"], "final_balance": summary["final_balance"], "return_fraction": summary["return_fraction"],
        "drawdown_equity": summary["drawdown_equity"]["fraction"], "signals": summary["funnel"]["signals"], "opened": summary["funnel"]["opened"],
        "indeterminate": summary["funnel"]["indeterminate"], "open_at_end": summary["funnel"]["open_at_end"],
        "indeterminate_conservative": summary["indeterminate_conservative"], "indeterminate_optimistic": summary["indeterminate_optimistic"],
        "open_unrealized": summary["open_unrealized"], "exits": total["exits"], "standing": STANDING.get(strategy["id"], ""),
        "warnings": payload["limitations"][:3], "study_name": payload["meta"]["study_name"], "study_sha256": payload["meta"]["study_sha256"],
        "dataset_sha256": payload["meta"]["dataset_sha256"], "results_sha256": strategy["results_sha256"],
        "parameters_sha256": strategy["parameters_sha256"], "engine": payload["meta"]["engine"], "commit": payload["meta"]["commit"],
        "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
    }


def build_distribution(specs: Sequence[ReportSpec], *, index_name: str = INDEX_NAME) -> tuple[dict[str, str], list[dict[str, Any]], list[dict[str, Any]]]:
    """(file name -> HTML, the index's entries, the payload of each report in order)."""
    if len({spec.file for spec in specs} | {index_name}) != len(specs) + 1:
        raise StudyError("dos reportes de la distribución tendrían el mismo nombre de archivo")
    files: dict[str, str] = {}
    entries, payloads = [], []
    for spec in specs:
        payload = build_payload(spec.study, period=spec.period, scenario=spec.scenario, only=[spec.strategy_id], index_href=index_name)
        files[spec.file] = report_html(payload)
        entries.append(_entry(spec, payload, files[spec.file]))
        payloads.append(payload)
    files[index_name] = index_html(entries)
    return files, entries, payloads


def index_html(entries: Sequence[Mapping[str, Any]]) -> str:
    data = json.dumps({"reports": list(entries), "warnings": list(GENERAL_WARNINGS)}, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return INDEX_PAGE.replace("__DATA__", data.replace("<", "\\u003c"))


INDEX_PAGE = r'''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Investment Committee · Índice de reportes de trading</title>
<style>
:root{color-scheme:light;--bg:#f4f4f2;--surface:#fcfcfb;--surface-2:#f0efec;--border:#d9d8d2;--text:#0b0b0b;--text-2:#52514e;--text-3:#7a7972;--accent:#2a78d6;--warn-bg:#fff6dc;--warn-bd:#e2b93b}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#111110;--surface:#1a1a19;--surface-2:#242422;--border:#3a3a37;--text:#f0efec;--text-2:#c3c2b7;--text-3:#96958c;--accent:#3987e5;--warn-bg:#33290d;--warn-bd:#8a6d14}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 "Segoe UI",system-ui,-apple-system,Helvetica,Arial,sans-serif}
main{max-width:1180px;margin:0 auto;padding:18px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:0 0 4px}h3{font-size:12px;margin:12px 0 4px;color:var(--text-2);text-transform:uppercase;letter-spacing:.04em}
.sub{color:var(--text-2);margin:0 0 14px}.muted{color:var(--text-3);font-size:12px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px;margin:0 0 14px}
.warn{background:var(--warn-bg);border:1px solid var(--warn-bd);border-radius:10px;padding:10px 16px;margin:0 0 14px}
.warn ul{margin:6px 0;padding-left:20px}
.tablewrap{overflow:auto;border:1px solid var(--border);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:7px 10px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--border);font-variant-numeric:tabular-nums}
th{background:var(--surface-2);cursor:pointer;user-select:none}td.l,th.l{text-align:left}
tbody tr:last-child td{border-bottom:0}
a{color:var(--accent)}
a.open{display:inline-block;background:var(--accent);color:#fff;text-decoration:none;border-radius:7px;padding:7px 14px;font-weight:600}
a.open:hover{filter:brightness(1.1)}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin:10px 0}
.tile{background:var(--surface-2);border-radius:8px;padding:8px 10px}
.tile b{display:block;font-size:17px;font-weight:600}.tile span{color:var(--text-2);font-size:12px}.tile small{display:block;color:var(--text-3);font-size:11px}
button{font:inherit;color:var(--text);background:var(--surface);border:1px solid var(--border);border-radius:7px;padding:5px 11px;cursor:pointer}
button.on{background:var(--accent);border-color:var(--accent);color:#fff}
.badge{display:inline-block;font-size:11px;border:1px solid var(--border);border-radius:5px;padding:0 6px;color:var(--text-2);margin-left:6px;vertical-align:middle}
details{margin:6px 0}summary{cursor:pointer;color:var(--text-2)}
code{font:12px ui-monospace,Consolas,monospace;word-break:break-all}
ul{margin:6px 0;padding-left:20px}li{margin:3px 0}
.kv{display:flex;justify-content:space-between;gap:12px;border-bottom:1px dotted var(--border);padding:2px 0;font-size:12.5px}.kv span:first-child{color:var(--text-2);white-space:nowrap}.kv span:last-child{text-align:right}
</style>
</head>
<body>
<main>
<h1>Investment Committee · Índice de reportes de trading</h1>
<p class="sub">Operaciones simuladas de tres estrategias experimentales de Forex (EUR/USD y GBP/USD, velas de 1 hora). Cada reporte se abre sin conexión: mantenga los archivos juntos en la misma carpeta.</p>
<div class="warn"><b>Antes de leer cualquier cifra</b><ul id="warnings"></ul></div>
<div class="card">
  <div class="row"><h2>Resumen de los reportes</h2><span id="filters"></span></div>
  <p class="muted">Pulse un encabezado para ordenar. Las filas de períodos distintos no son comparables entre sí.</p>
  <div class="tablewrap"><table><thead id="thead"></thead><tbody id="tbody"></tbody></table></div>
</div>
<div id="cards"></div>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("data").textContent), $ = id => document.getElementById(id);
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const signed = s => (Number(s) > 0 ? "+" : "") + s, pct = (f, d) => (Number(f) * 100).toFixed(d == null ? 1 : d) + " %";
const size = b => (b / 1048576).toFixed(2) + " MB";
const S = {key: null, dir: 1, label: ""};
const COLS = [
  {k: "file", h: "Reporte", l: 1, f: r => '<a href="' + encodeURI(r.file) + '">Estrategia ' + esc(r.label) + "</a>"},
  {k: "start", h: "Período", l: 1, f: r => esc(r.period)}, {k: "scenario", h: "Escenario", l: 1, f: r => esc(r.scenario)},
  {k: "n", h: "Operaciones", n: 1}, {k: "win_rate", h: "Acierto", n: 1, f: r => pct(r.win_rate)},
  {k: "expectancy_r", h: "Expectativa", n: 1, f: r => signed(r.expectancy_r) + " R"}, {k: "net_pnl", h: "Resultado neto", n: 1, f: r => signed(r.net_pnl) + " USD"},
  {k: "return_fraction", h: "Retorno de la cuenta", n: 1, f: r => pct(r.return_fraction, 2)}, {k: "drawdown_equity", h: "Drawdown máx.", n: 1, f: r => pct(r.drawdown_equity, 2)}
];
function rows() {
  const out = D.reports.filter(r => !S.label || r.label === S.label);
  if (S.key) { const c = COLS.find(x => x.k === S.key); out.sort((a, b) => { const x = c.n ? Number(a[c.k]) : a[c.k], y = c.n ? Number(b[c.k]) : b[c.k]; return (x < y ? -1 : x > y ? 1 : 0) * S.dir; }); }
  return out;
}
function render() {
  $("thead").innerHTML = "<tr>" + COLS.map(c => '<th data-k="' + c.k + '"' + (c.l ? ' class="l"' : "") + ">" + esc(c.h) + (S.key === c.k ? (S.dir > 0 ? " ▲" : " ▼") : "") + "</th>").join("") + "</tr>";
  $("tbody").innerHTML = rows().map(r => "<tr>" + COLS.map(c => "<td" + (c.l ? ' class="l"' : "") + ">" + (c.f ? c.f(r) : esc(r[c.k])) + "</td>").join("") + "</tr>").join("");
  $("cards").innerHTML = rows().map(r => {
    const ci = r.win_rate_interval_95, exits = Object.keys(r.exits).map(k => k + " " + r.exits[k]).join(" · ");
    const account = r.initial_capital + " USD " + signed(r.net_pnl) + " (cerradas válidas)" +
      (r.indeterminate ? " " + signed(r.indeterminate_conservative) + " (" + r.indeterminate + " indeterminadas, lectura conservadora)" : "") + " = " + r.final_balance + " USD";
    return '<div class="card"><div class="row"><div><h2>Estrategia ' + esc(r.label) + " · " + esc(r.title.replace(/^Estrategia \w+ · /, "")) + '</h2><span class="muted">' + esc(r.period) + " · " + esc(r.scenario) + " · " + r.n + " operaciones cerradas válidas</span></div>" +
      '<a class="open" href="' + encodeURI(r.file) + '">Abrir el reporte</a></div>' +
      '<div class="tiles"><div class="tile"><b>' + r.n + "</b><span>operaciones cerradas válidas</span><small>" + r.signals + " señales · " + r.opened + " entradas</small></div>" +
      '<div class="tile"><b>' + r.wins + " / " + r.losses + "</b><span>ganadoras / perdedoras</span><small>acierto " + pct(r.win_rate) + (ci ? " (IC 95 %: " + pct(ci[0]) + " a " + pct(ci[1]) + ")" : "") + "</small></div>" +
      '<div class="tile"><b>' + signed(r.expectancy_r) + " R</b><span>expectativa por operación</span><small>factor de beneficio " + r.profit_factor + "</small></div>" +
      '<div class="tile"><b>' + signed(r.net_pnl) + " USD</b><span>resultado neto</span><small>solo operaciones cerradas válidas</small></div>" +
      '<div class="tile"><b>' + pct(r.return_fraction, 2) + "</b><span>retorno de la cuenta</span><small>saldo final " + r.final_balance + " USD</small></div>" +
      '<div class="tile"><b>' + pct(r.drawdown_equity, 2) + "</b><span>drawdown máximo</span><small>sobre equity, marcas horarias</small></div></div>" +
      "<p><b>Qué valor tiene este resultado.</b> " + esc(r.standing) + "</p>" +
      "<p class='muted'>Cuenta: " + esc(account) + "." + (r.open_at_end ? " " + r.open_at_end + " posición abierta al final (" + signed(r.open_unrealized) + " USD no realizados), fuera del conteo y del saldo." : "") + " Salidas: " + esc(exits) + ".</p>" +
      "<details><summary>Reglas de la estrategia</summary><ul>" + r.rules.map(x => "<li>" + esc(x) + "</li>").join("") + "</ul></details>" +
      "<details><summary>Advertencias de este reporte</summary><ul>" + r.warnings.map(x => "<li>" + esc(x) + "</li>").join("") + "</ul></details>" +
      "<details><summary>Identificadores técnicos y procedencia</summary>" +
      [["Archivo", esc(r.file) + " · " + size(r.bytes) + " (" + r.bytes.toLocaleString("es") + " bytes)"], ["SHA-256 del archivo", "<code>" + r.sha256 + "</code>"],
       ["Estrategia", "<code>" + esc(r.id) + "</code>"], ["Huella de parámetros", "<code>" + r.parameters_sha256 + "</code>"],
       ["Estudio de origen", "<code>" + esc(r.study_name) + "</code>"], ["Estudio (SHA-256)", "<code>" + r.study_sha256 + "</code>"],
       ["Corrida · período / escenario", "<code>" + esc(r.period_id) + " / " + esc(r.scenario_id) + "</code>"], ["Resultados de la corrida (SHA-256)", "<code>" + r.results_sha256 + "</code>"],
       ["Dataset (SHA-256)", "<code>" + r.dataset_sha256 + "</code>"], ["Motor · código", esc(r.engine) + " · <code>" + esc(r.commit.slice(0, 12)) + "</code>"]]
        .map(x => '<div class="kv"><span>' + x[0] + "</span><span>" + x[1] + "</span></div>").join("") + "</details></div>";
  }).join("");
}
$("warnings").innerHTML = D.warnings.map(w => "<li>" + esc(w) + "</li>").join("");
const labels = [...new Set(D.reports.map(r => r.label))];
$("filters").innerHTML = ['<button data-l="" class="on">Todas</button>'].concat(labels.map(l => '<button data-l="' + esc(l) + '">Estrategia ' + esc(l) + "</button>")).join(" ");
$("filters").onclick = e => { const b = e.target.closest("button"); if (!b) return; S.label = b.dataset.l; [...$("filters").children].forEach(x => x.classList.toggle("on", x === b)); render(); };
$("thead").onclick = e => { const th = e.target.closest("th"); if (!th) return; S.dir = S.key === th.dataset.k ? -S.dir : 1; S.key = th.dataset.k; render(); };
render();
document.body.dataset.ready = "1";
document.body.dataset.reports = String(D.reports.length);
</script>
</body>
</html>
'''
