"""The page of the offline trade report (offline_report.py): one HTML
document with its styles and its JavaScript inside, and `__DATA__` where
the data go. No library, no network, no file next to it: it opens from
file:// with a double click, in Chrome or Edge.

The script never computes a trade. It draws the embedded records and, for
the filtered selection only, adds the recorded results in whole cents.
In the "sin conocer el futuro" mode nothing after the revealed candle is
drawn or written: no exit, result, colour or duration, until the reader
asks for it.

Opening the file with `#selftest` appended runs a short self-check of the
controls and writes its outcome into the page (used by the browser test).
"""

from __future__ import annotations

PAGE = r'''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Investment Committee · operaciones simuladas</title>
<style>
:root{color-scheme:light;--bg:#f4f4f2;--surface:#fcfcfb;--surface-2:#f0efec;--border:#d9d8d2;--text:#0b0b0b;--text-2:#52514e;--text-3:#7a7972;
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--good:#008300;--bad:#e34948;--warn-bg:#fff6dc;--warn-bd:#e2b93b;--accent:#2a78d6;--shade:rgba(42,120,214,.07)}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#111110;--surface:#1a1a19;--surface-2:#242422;--border:#3a3a37;--text:#f0efec;--text-2:#c3c2b7;--text-3:#96958c;
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--good:#2aa32a;--bad:#e66767;--warn-bg:#33290d;--warn-bd:#8a6d14;--accent:#3987e5;--shade:rgba(57,135,229,.10)}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 "Segoe UI",system-ui,-apple-system,Helvetica,Arial,sans-serif}
main{max-width:1320px;margin:0 auto;padding:16px}
h1{font-size:21px;margin:0 0 2px}h2{font-size:16px;margin:0 0 10px}h3{font-size:13px;margin:14px 0 6px;color:var(--text-2);text-transform:uppercase;letter-spacing:.04em}
.sub{color:var(--text-2);margin:0 0 12px}
a{color:var(--accent)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px;margin:0 0 14px}
.warn{background:var(--warn-bg);border:1px solid var(--warn-bd);border-radius:10px;padding:10px 14px;margin:0 0 14px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media (max-width:860px){.grid2{grid-template-columns:1fr}}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(128px,1fr));gap:8px}
.tile{background:var(--surface-2);border-radius:8px;padding:8px 10px}
.tile b{display:block;font-size:17px;font-weight:600}.tile span{color:var(--text-2);font-size:12px}.tile small{display:block;color:var(--text-3);font-size:11px}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.row label{font-size:12px;color:var(--text-2);display:flex;flex-direction:column;gap:2px}
select,input[type=search],button{font:inherit;color:var(--text);background:var(--surface);border:1px solid var(--border);border-radius:7px;padding:6px 9px}
button{cursor:pointer}button:hover:not(:disabled){border-color:var(--accent)}button:disabled{opacity:.45;cursor:not-allowed}
button.on{background:var(--accent);border-color:var(--accent);color:#fff}
button.primary{border-color:var(--accent)}
.tabs button{border-radius:7px;min-width:150px;text-align:left}
.tabs small{display:block;font-size:11px;opacity:.8}
.tablewrap{max-height:340px;overflow:auto;border:1px solid var(--border);border-radius:8px;margin-top:10px}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{padding:5px 8px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--border)}
th{position:sticky;top:0;background:var(--surface-2);cursor:pointer;user-select:none;font-weight:600;z-index:1}
th.nosort{cursor:default;color:var(--text-3)}
td.l,th.l{text-align:left}
tbody tr{cursor:pointer}tbody tr:hover{background:var(--surface-2)}tbody tr.sel{background:var(--shade);outline:2px solid var(--accent);outline-offset:-2px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;vertical-align:middle;background:var(--text-3)}
.dot.w{background:var(--good)}.dot.x{background:var(--bad)}
.badge{display:inline-block;font-size:11px;border:1px solid var(--border);border-radius:5px;padding:0 5px;color:var(--text-2)}
.hidden{color:var(--text-3);font-style:italic}
.chartbox{position:relative;border:1px solid var(--border);border-radius:8px;background:var(--surface);overflow:hidden}
canvas{display:block;width:100%;touch-action:pan-y}
#chart{height:560px;cursor:crosshair}
@media (max-width:700px){#chart{height:470px}}
#tip{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--border);border-radius:7px;padding:6px 8px;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.18);display:none;min-width:170px;z-index:3}
#tip i,.chip i{display:inline-block;width:10px;height:3px;border-radius:2px;margin-right:5px;vertical-align:middle}
#tip div{display:flex;justify-content:space-between;gap:12px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.chip{font-size:12px;padding:3px 8px;border-radius:999px}.chip.off{opacity:.45;text-decoration:line-through}
.sheet{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:4px 22px;margin-top:6px}
.sheet section{break-inside:avoid}
.kv{display:flex;justify-content:space-between;gap:10px;border-bottom:1px dotted var(--border);padding:2px 0;font-size:13px}
.kv span:first-child{color:var(--text-2)}.kv span:last-child{text-align:right;font-variant-numeric:tabular-nums}
.replay{background:var(--surface-2);border-radius:8px;padding:10px;margin:8px 0;display:none}
.replay.show{display:block}
details{margin:6px 0}summary{cursor:pointer;color:var(--text-2)}
code{font:12px ui-monospace,Consolas,monospace;word-break:break-all}
ul{margin:6px 0;padding-left:20px}li{margin:3px 0}
.muted{color:var(--text-3);font-size:12px}
#cum{height:130px}
</style>
</head>
<body>
<main>
<p class="sub" id="crumb" style="margin:0 0 4px"></p>
<h1 id="title"></h1>
<p id="subhead" style="margin:2px 0 2px;font-size:15px"></p>
<p class="sub" id="subtitle"></p>
<div class="warn" id="warning"></div>

<div class="card">
  <h2>Resumen general <span class="muted">· cifras del motor, operaciones cerradas válidas</span></h2>
  <div class="grid2" id="summaries"></div>
</div>

<div class="card">
  <div class="row" style="justify-content:space-between">
    <div class="row tabs" id="tabs"></div>
    <div class="row">
      <label>Zona horaria<select id="zone"><option value="UTC">UTC</option><option value="America/New_York">Nueva York</option><option value="America/Bogota">Bogotá</option><option value="local">Hora de este equipo</option></select></label>
      <label>Modo de análisis
        <span class="row"><button id="modeFull" class="on">Operación completa</button><button id="modeBlind">Sin conocer el futuro</button></span></label>
    </div>
  </div>
  <div class="row" style="margin-top:10px">
    <label>Buscar<input type="search" id="q" placeholder="ID, fecha, par…" size="22"></label>
    <label>Par<select id="fSym"></select></label>
    <label>Dirección<select id="fDir"><option value="">Todas</option><option>LONG</option><option>SHORT</option></select></label>
    <label>Resultado<select id="fOut"><option value="">Todos</option><option>GANADORA</option><option>PERDEDORA</option><option>NEUTRA</option></select></label>
    <label>Tipo de salida<select id="fExit"></select></label>
    <label>&nbsp;<button id="reset">Limpiar filtros</button></label>
    <label>&nbsp;<button id="csv">Exportar CSV de la selección</button></label>
    <label style="flex-direction:row;align-items:center;gap:6px;margin-top:14px"><input type="checkbox" id="showEx"> <span id="exLabel"></span></label>
  </div>
  <div id="selSummary" style="margin-top:10px"></div>
  <div class="tablewrap"><table><thead id="thead"></thead><tbody id="tbody"></tbody></table></div>
  <p class="muted" id="tableNote"></p>
</div>

<div class="card" id="tradeCard">
  <div class="row" style="justify-content:space-between">
    <h2 id="tradeTitle" style="margin:0"></h2>
    <div class="row">
      <button id="prev" title="Operación anterior de la selección">◀ Anterior</button>
      <select id="pick" aria-label="Operación"></select>
      <button id="next" title="Operación siguiente de la selección">Siguiente ▶</button>
    </div>
  </div>
  <div class="replay" id="replay">
    <div class="row">
      <b>Sin conocer el futuro</b>
      <button id="step1" class="primary">Vela siguiente ▶</button>
      <button id="step5">+5 velas</button>
      <button id="play">Reproducir</button>
      <button id="restart">Reiniciar</button>
      <button id="reveal">Revelar resultado</button>
      <button id="full">Ver operación completa</button>
      <span id="replayInfo" class="muted"></span>
    </div>
    <div id="replayNow" style="margin-top:6px"></div>
  </div>
  <div class="chips" id="chips"></div>
  <div class="chartbox" id="chartbox"><canvas id="chart"></canvas><div id="tip"></div></div>
  <div class="row" style="margin-top:6px;justify-content:space-between">
    <span class="muted">Arrastre para desplazar · rueda del ratón para acercar · doble clic para restablecer. Velas: precio medio indicativo de Yahoo, H1.</span>
    <span class="row"><button id="zin" title="Acercar">＋</button><button id="zout" title="Alejar">－</button><button id="zreset">Restablecer vista</button></span>
  </div>
  <div class="sheet" id="sheet"></div>
</div>

<div class="card">
  <h2>Reglas de las estrategias</h2>
  <div class="grid2" id="rules"></div>
  <h3>Reglas comunes de entrada, tamaño y salida</h3>
  <ul id="shared"></ul>
</div>
<div class="card">
  <h2>Limitaciones e integridad</h2>
  <ul id="limits"></ul>
  <h3>Procedencia y hashes</h3>
  <div id="integrity"></div>
</div>
<pre id="selftest" style="display:none"></pre>
</main>

<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("data").textContent);
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const SERIES = ["--s1", "--s2", "--s3", "--s4", "--s5"];
const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const BLIND_AHEAD = 92, BEFORE = 50, AFTER = 20;
const S = {si: 0, id: null, blind: false, revealed: {}, step: 0, zone: "UTC", sort: {key: "order", dir: 1}, off: {}, x0: 0, x1: 1, timer: null, hover: null, showEx: false};

// ---------- numbers and time ----------
const cents = s => Math.round(Number(s) * 100);
const tenK = s => Math.round(Number(s) * 10000);
const money = c => (c < 0 ? "-" : c > 0 ? "+" : "") + (Math.abs(c) / 100).toFixed(2);
const signed = s => s == null ? "—" : (Number(s) > 0 ? "+" : "") + s;
const pct = (f, d) => (Number(f) * 100).toFixed(d == null ? 1 : d) + " %";
const fmts = {};
function parts(sec) {
  const zone = S.zone;
  if (!fmts[zone]) {
    const o = {year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23"};
    if (zone !== "local") o.timeZone = zone;
    fmts[zone] = new Intl.DateTimeFormat("en-CA", o);
  }
  const p = {};
  for (const x of fmts[zone].formatToParts(new Date(sec * 1000))) p[x.type] = x.value;
  return p;
}
const zoneName = () => ({UTC: "UTC", "America/New_York": "Nueva York", "America/Bogota": "Bogotá", local: "hora local"}[S.zone]);
function when(sec) { if (sec == null) return "—"; const p = parts(sec); return p.year + "-" + p.month + "-" + p.day + " " + p.hour + ":" + p.minute; }
const iso = s => s == null ? null : Date.parse(s) / 1000;

// ---------- data access ----------
const strat = () => D.strategies[S.si];
const isBlind = t => S.blind && !S.revealed[t.id];
const tradeById = id => strat().trades.find(t => t.id === id);
function filtered() {
  const q = $("q").value.trim().toLowerCase(), sym = $("fSym").value, dir = $("fDir").value;
  const out = S.blind ? "" : $("fOut").value, ex = S.blind ? "" : $("fExit").value;
  const rows = strat().trades.filter(t => {
    if (!t.valid && !S.showEx) return false;
    if (sym && t.sym !== sym) return false;
    if (dir && t.dir !== dir) return false;
    if (out && t.outcome !== out) return false;
    if (ex && t.reason !== ex) return false;
    if (q) {
      const hay = [t.id, t.sym, t.dir, when(iso(t.t_in)), S.blind ? "" : (t.reason || "") + " " + t.outcome].join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  const k = S.sort.key, d = S.sort.dir, text = {sym: 1, dir: 1, reason: 1, outcome: 1, t_in: 1};
  rows.sort((a, b) => {
    let x = a[k], y = b[k];
    if (!text[k]) { x = x == null ? -Infinity : Number(x); y = y == null ? -Infinity : Number(y); }
    else { x = x || ""; y = y || ""; }
    return (x < y ? -1 : x > y ? 1 : a.order - b.order) * d;
  });
  return rows;
}
// figures of a set of valid closed trades, in whole cents and ten-thousandths of R
function figures(rows) {
  const v = rows.filter(t => t.valid);
  let net = 0, gp = 0, gl = 0, w = 0, l = 0, r = 0;
  for (const t of v) { const c = cents(t.pnl); net += c; r += tenK(t.r); if (c > 0) { w++; gp += c; } else if (c < 0) { l++; gl -= c; } }
  const byExit = v.slice().sort((a, b) => iso(a.t_out) - iso(b.t_out) || a.order - b.order);
  let cum = 0, peak = 0, dd = 0; const curve = [];
  for (const t of byExit) { cum += cents(t.pnl); peak = Math.max(peak, cum); dd = Math.max(dd, peak - cum); curve.push({t, cum}); }
  return {n: v.length, w, l, net, gp, gl, r, dd, curve};
}

// ---------- static sections ----------
function tile(value, label, small) { return '<div class="tile"><b>' + esc(value) + "</b><span>" + esc(label) + "</span>" + (small ? "<small>" + esc(small) + "</small>" : "") + "</div>"; }
function renderStatic() {
  const m = D.meta;
  $("title").textContent = m.headline; $("subhead").textContent = m.subhead; document.title = m.headline + " · " + m.subhead;
  $("crumb").innerHTML = (m.index_href ? '<a href="' + esc(m.index_href) + '">← Índice de reportes</a> · ' : "") + esc(m.title);
  $("subtitle").textContent = (m.period === "COMUN" ? "Período común " : "Período ") + m.common_start.slice(0, 10) + " a " + m.end.slice(0, 10) + " · escenario " + m.scenario + " · EUR/USD y GBP/USD · H1 · estudio " + m.study_sha256.slice(0, 12) + "…";
  $("warning").innerHTML = "<b>Simulación, no operaciones reales.</b> " + esc(m.warning) + " Fuente: " + esc(m.data_source) + ". Datos " + esc(m.data_kind) + ". No es una validación independiente ni una recomendación.";
  $("summaries").innerHTML = D.strategies.map(s => {
    const c = s.summary.closed.TOTAL, f = s.summary.funnel, ci = c.win_rate_interval_95, ex = s.trades.filter(t => !t.valid);
    const opt = s.summary.indeterminate_policy === "OPTIMISTA", booked = opt ? s.summary.indeterminate_optimistic : s.summary.indeterminate_conservative;
    const exits = Object.keys(c.exits).map(k => k + " " + c.exits[k]).join(" · ");
    const months = Object.keys(s.summary.monthly).map(k => { const x = s.summary.monthly[k]; return "<tr><td class='l'>" + k + "</td><td>" + x.closed_valid + "</td><td>" + x.wins + "</td><td>" + signed(x.net_pnl) + "</td></tr>"; }).join("");
    const pairs = Object.keys(s.summary.closed).filter(k => k !== "TOTAL").map(k => { const x = s.summary.closed[k]; return "<tr><td class='l'>" + k + "</td><td>" + x.n + "</td><td>" + x.wins + "</td><td>" + x.expectancy_r + "</td><td>" + signed(x.net_pnl) + "</td></tr>"; }).join("");
    return "<div><h3>" + esc(s.label) + " · " + esc(s.id) + "</h3><div class='tiles'>" +
      tile(c.n, "cerradas válidas", f.signals + " señales · " + f.opened + " entradas") +
      tile(c.wins + " / " + c.losses, "ganadoras / perdedoras", "acierto " + pct(c.win_rate) + (ci ? " (IC 95 %: " + pct(ci[0]) + " a " + pct(ci[1]) + ")" : "")) +
      tile(signed(c.expectancy_r) + " R", "expectativa por operación", "factor de beneficio " + c.profit_factor) +
      tile(signed(c.net_pnl) + " USD", "resultado neto", "suma de las " + c.n + " operaciones cerradas válidas") +
      tile(s.summary.final_balance + " USD", "saldo final de la cuenta", pct(s.summary.return_fraction, 2) + " sobre " + s.summary.initial_capital + " USD" +
        (f.indeterminate ? " · incluye " + f.indeterminate + " indeterminadas contabilizadas en " + signed(booked) + " USD" : "")) +
      tile("+" + c.gross_profit + " / -" + c.gross_loss, "ganancias / pérdidas (USD)", "media " + signed(c.average_win) + " / " + c.average_loss) +
      tile(pct(s.summary.drawdown_equity.fraction, 2), "drawdown máximo (equity)", s.summary.drawdown_equity.amount + " USD · sobre saldo " + pct(s.summary.drawdown_balance.fraction, 2)) +
      "</div><p class='muted'>Salidas: " + esc(exits) + " · duración media " + c.average_hours + " h" +
      (ex.length ? " · <b>" + ex.length + " fuera del conteo</b> (" + esc(ex.map(t => t.status).join(", ")) + ")" : " · ninguna operación abierta o indeterminada") + "</p>" +
      "<p class='muted'>Cuenta: " + s.summary.initial_capital + " USD " + signed(c.net_pnl) + " (cerradas válidas)" +
      (f.indeterminate ? " " + signed(booked) + " (" + f.indeterminate + " indeterminadas, contabilizadas con la lectura " + (opt ? "optimista: objetivo primero; con la conservadora serían " + signed(s.summary.indeterminate_conservative) : "conservadora: stop primero; con la optimista serían " + signed(s.summary.indeterminate_optimistic)) + ")" : "") +
      " = " + s.summary.final_balance + " USD de saldo realizado (" + pct(s.summary.return_fraction, 2) + ")." +
      (Number(s.summary.open_unrealized) ? " La posición abierta al final vale " + signed(s.summary.open_unrealized) + " USD no realizados, fuera del saldo." : "") + "</p>" +
      "<details><summary>Por par y por mes</summary><table><thead><tr><th class='l nosort'>Par</th><th class='nosort'>n</th><th class='nosort'>ganadoras</th><th class='nosort'>expectativa R</th><th class='nosort'>neto USD</th></tr></thead><tbody>" + pairs +
      "</tbody></table><table style='margin-top:8px'><thead><tr><th class='l nosort'>Mes (cierre)</th><th class='nosort'>n</th><th class='nosort'>ganadoras</th><th class='nosort'>neto USD</th></tr></thead><tbody>" + months + "</tbody></table></details></div>";
  }).join("");
  $("tabs").innerHTML = D.strategies.map((s, i) => '<button data-i="' + i + '">Estrategia ' + esc(s.label) + "<small>" + esc(s.id) + "</small></button>").join("");
  $("rules").innerHTML = D.strategies.map(s => "<div><h3>" + esc(s.title) + "</h3><ul>" + s.rules.map(r => "<li>" + esc(r) + "</li>").join("") + "</ul><p class='muted'>Huella de parámetros: <code>" + esc(s.parameters_sha256) + "</code></p></div>").join("");
  $("shared").innerHTML = D.shared_rules.map(r => "<li>" + esc(r) + "</li>").join("");
  $("limits").innerHTML = D.limitations.map(r => "<li>" + esc(r) + "</li>").join("");
  const check = D.strategies.map(s => { const f = figures(s.trades), c = s.summary.closed.TOTAL; return f.n === c.n && f.w === c.wins && f.l === c.losses && f.net === cents(c.net_pnl); });
  document.body.dataset.check = check.every(Boolean) ? "ok" : "fail";
  $("integrity").innerHTML = "<div class='kv'><span>Estudio (SHA-256)</span><span><code>" + esc(m.study_sha256) + "</code></span></div>" +
    "<div class='kv'><span>Dataset (SHA-256)</span><span><code>" + esc(m.dataset_sha256) + "</code></span></div>" +
    D.strategies.map(s => "<div class='kv'><span>Resultados de " + esc(s.label) + " (SHA-256)</span><span><code>" + esc(s.results_sha256) + "</code></span></div>").join("") +
    "<div class='kv'><span>Motor · código</span><span>" + esc(m.engine) + " · <code>" + esc(m.commit.slice(0, 12)) + "</code></span></div>" +
    "<div class='kv'><span>Datos</span><span>" + esc(m.dataset_description) + "</span></div>" +
    "<div class='kv'><span>Calendario</span><span>" + esc(m.calendar) + "</span></div>" +
    "<div class='kv'><span>Comprobación al abrir</span><span>" + (check.every(Boolean) ? "las operaciones embebidas suman exactamente el resumen del motor" : "<b>NO coincide con el resumen del motor: no use este archivo</b>") + "</span></div>" +
    "<p class='muted'>Al generar este archivo se verificaron los hashes del manifiesto, del dataset y de cada corrida. signals.csv (los indicadores) no tiene hash propio en el estudio: se aceptó porque coincide con la corrida verificada en cada decisión. Los indicadores se dibujan con seis decimales (RSI con dos); las velas conservan todos los dígitos de Yahoo.</p>";
}

// ---------- table ----------
const COLS = [
  {k: "order", h: "#"}, {k: "t_in", h: "Entrada", l: 1, f: t => when(iso(t.t_in))}, {k: "sym", h: "Par", l: 1}, {k: "dir", h: "Dirección", l: 1},
  {k: "entry", h: "Precio entrada"}, {k: "stop", h: "Stop loss"}, {k: "target", h: "Take profit"}, {k: "units", h: "Unidades"}, {k: "est_loss", h: "Riesgo USD"},
  {k: "exit", h: "Precio salida", res: 1}, {k: "pnl", h: "P&L USD", res: 1, f: t => signed(t.pnl)}, {k: "r", h: "R", res: 1, f: t => signed(t.r)},
  {k: "hours", h: "Horas", res: 1}, {k: "reason", h: "Salida", l: 1, res: 1, f: t => (t.reason || "—") + (t.gap ? " (gap)" : "")},
  {k: "outcome", h: "Resultado", l: 1, res: 1, f: t => '<span class="dot ' + (t.outcome === "GANADORA" ? "w" : t.outcome === "PERDEDORA" ? "x" : "") + '"></span>' + esc(t.outcome) + (t.valid ? "" : ' <span class="badge">no cuenta</span>'), raw: 1}
];
function renderTable() {
  const rows = filtered();
  $("thead").innerHTML = "<tr>" + COLS.map(c => { const lock = S.blind && c.res; return '<th data-k="' + c.k + '" class="' + (c.l ? "l " : "") + (lock ? "nosort" : "") + '">' + esc(c.h) + (S.sort.key === c.k ? (S.sort.dir > 0 ? " ▲" : " ▼") : "") + "</th>"; }).join("") + "</tr>";
  $("tbody").innerHTML = rows.map(t => '<tr data-id="' + esc(t.id) + '"' + (t.id === S.id ? ' class="sel"' : "") + ">" + COLS.map(c => {
    if (c.res && isBlind(t)) return '<td class="hidden' + (c.l ? " l" : "") + '">oculto</td>';
    const v = c.f ? c.f(t) : t[c.k];
    return "<td" + (c.l ? ' class="l"' : "") + ">" + (c.raw ? v : esc(v == null ? "—" : v)) + "</td>";
  }).join("") + "</tr>").join("") || '<tr><td class="l" colspan="' + COLS.length + '">Ninguna operación cumple los filtros.</td></tr>';
  const nv = rows.filter(t => t.valid).length;
  $("tableNote").textContent = nv + " de " + strat().summary.closed.TOTAL.n + " operaciones cerradas válidas" + (rows.length > nv ? " + " + (rows.length - nv) + " fuera del conteo" : "") + " · horas en " + zoneName() + ".";
  renderSelection(rows);
  const pick = $("pick");
  pick.innerHTML = rows.map(t => '<option value="' + esc(t.id) + '">#' + t.order + " · " + when(iso(t.t_in)) + " · " + t.sym + " " + t.dir + "</option>").join("");
  if (rows.some(t => t.id === S.id)) pick.value = S.id;
  const pos = rows.findIndex(t => t.id === S.id);
  $("prev").disabled = pos <= 0; $("next").disabled = pos < 0 || pos >= rows.length - 1;
}
function renderSelection(rows) {
  const box = $("selSummary");
  if (S.blind) { box.innerHTML = "<p class='muted'>Resumen de la selección oculto en el modo «sin conocer el futuro»: los resultados solo aparecen en las operaciones que usted revele.</p>"; return; }
  const f = figures(rows);
  if (!f.n) { box.innerHTML = "<p class='muted'>Sin operaciones válidas en la selección.</p>"; return; }
  box.innerHTML = "<div class='tiles'>" + tile(f.n, "operaciones en la selección") + tile(f.w + " / " + f.l, "ganadoras / perdedoras", "acierto " + (f.w / f.n * 100).toFixed(1) + " %") +
    tile(money(f.gp) + " / " + money(-f.gl), "ganancias / pérdidas USD") + tile(money(f.net) + " USD", "resultado neto") +
    tile((f.r / f.n / 10000 >= 0 ? "+" : "") + (f.r / f.n / 10000).toFixed(4) + " R", "expectativa por operación", f.gl ? "factor de beneficio " + (f.gp / f.gl).toFixed(4) : "sin pérdidas") +
    tile(money(-f.dd) + " USD", "drawdown máximo", "sobre el P&L acumulado de la selección, por orden de salida") + "</div>" +
    "<div class='chartbox' style='margin-top:8px'><canvas id='cum'></canvas></div><p class='muted'>P&L neto acumulado de la selección (USD), por orden de salida. Sumado en el navegador a partir de los resultados registrados.</p>";
  drawCum(f);
}
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function setup(canvas) {
  const r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(r.width * dpr)); canvas.height = Math.max(1, Math.round(r.height * dpr));
  const g = canvas.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, r.width, r.height);
  return {g, w: r.width, h: r.height};
}
function nice(lo, hi, n) {
  const span = (hi - lo) || 1, raw = span / n, mag = Math.pow(10, Math.floor(Math.log10(raw))), m = raw / mag;
  const step = (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * mag, out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(v);
  return out;
}
function drawCum(f) {
  const c = $("cum"); if (!c) return;
  const {g, w, h} = setup(c), L = 8, R = 60, T = 8, B = 8, vals = [0].concat(f.curve.map(p => p.cum / 100));
  const lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals), pad = (hi - lo || 1) * .08;
  const y = v => T + (hi + pad - v) / (hi - lo + 2 * pad) * (h - T - B), x = i => L + i / Math.max(1, vals.length - 1) * (w - L - R);
  g.font = "11px Segoe UI,system-ui,sans-serif"; g.textBaseline = "middle";
  for (const v of nice(lo - pad, hi + pad, 3)) { g.strokeStyle = css("--border"); g.lineWidth = 1; g.beginPath(); g.moveTo(L, y(v)); g.lineTo(w - R, y(v)); g.stroke(); g.fillStyle = css("--text-3"); g.fillText(v.toFixed(0), w - R + 6, y(v)); }
  g.strokeStyle = css("--text-3"); g.setLineDash([4, 3]); g.beginPath(); g.moveTo(L, y(0)); g.lineTo(w - R, y(0)); g.stroke(); g.setLineDash([]);
  g.strokeStyle = css("--s1"); g.lineWidth = 2; g.lineJoin = "round"; g.beginPath(); vals.forEach((v, i) => i ? g.lineTo(x(i), y(v)) : g.moveTo(x(i), y(v))); g.stroke();
  c.onmousemove = e => {
    const r = c.getBoundingClientRect(), i = Math.max(1, Math.min(vals.length - 1, Math.round((e.clientX - r.left - L) / (w - L - R) * (vals.length - 1)))), p = f.curve[i - 1];
    c.title = "#" + p.t.order + " · " + when(iso(p.t.t_out)) + " · " + signed(p.t.pnl) + " USD · acumulado " + money(p.cum) + " USD";
  };
}

// ---------- trade panel ----------
function lastVisible(t) {
  const n = D.bars[t.sym].t.length;
  return isBlind(t) ? Math.min(n - 1, t.i_in - 1 + S.step) : Math.min(n - 1, t.i_last + AFTER);
}
function maxStep(t) { return Math.min(D.bars[t.sym].t.length - 1, t.i_in + BLIND_AHEAD) - (t.i_in - 1); }
function resetView(t) {
  S.x0 = Math.max(0, t.i_in - BEFORE);
  S.x1 = isBlind(t) ? t.i_in + 26 : lastVisible(t) + 4;
  follow(t);
}
function follow(t) { const end = lastVisible(t) + 4; if (isBlind(t) && end > S.x1) { const d = end - S.x1; S.x0 += d; S.x1 += d; } }
function clampView(t) {
  const lo = Math.max(0, t.i_in - 600), hi = (isBlind(t) ? Math.max(lastVisible(t), t.i_in) + 26 : lastVisible(t) + 12);
  let w = Math.min(Math.max(S.x1 - S.x0, 12), hi - lo);
  if (S.x0 < lo) S.x0 = lo; if (S.x0 + w > hi) S.x0 = hi - w;
  S.x1 = S.x0 + w;
}
function select(id, keepStep) {
  const t = tradeById(id); if (!t) return;
  stopPlay(); S.id = id; if (!keepStep) S.step = 0; S.hover = null;
  resetView(t); renderTable(); renderTrade();
}
function kv(a, b) { return '<div class="kv"><span>' + esc(a) + "</span><span>" + b + "</span></div>"; }
function renderTrade() {
  const t = tradeById(S.id), s = strat(); if (!t) return;
  const blind = isBlind(t), ind = s.indicators[t.sym], names = Object.keys(ind);
  $("tradeTitle").innerHTML = "Estrategia " + esc(s.label) + " · " + esc(t.sym) + " " + esc(t.dir) + " · #" + t.order + (blind ? ' <span class="badge">resultado oculto</span>' : ' · <span class="dot ' + (t.outcome === "GANADORA" ? "w" : t.outcome === "PERDEDORA" ? "x" : "") + '"></span>' + esc(t.outcome) + (t.valid ? "" : ' <span class="badge">no cuenta en el resumen</span>'));
  $("replay").classList.toggle("show", S.blind);
  if (S.blind) {
    const mx = maxStep(t), done = !blind;
    $("step1").disabled = $("step5").disabled = $("play").disabled = done || S.step >= mx;
    $("reveal").disabled = $("full").disabled = done;
    $("replayInfo").textContent = done ? "Resultado revelado para esta operación." : "Velas reveladas desde la entrada: " + S.step + ". El resultado, la salida y la duración siguen ocultos.";
    const b = D.bars[t.sym], i = lastVisible(t), sign = t.dir === "LONG" ? 1 : -1, risk = Math.abs(Number(t.entry) - Number(t.stop));
    $("replayNow").innerHTML = done ? "" : "<span class='muted'>Última vela conocida: " + when(b.t[i]) + " · cierre " + b.c[i].toFixed(5) +
      (S.step > 0 ? " · distancia a la entrada " + ((b.c[i] - Number(t.entry)) * sign / risk >= 0 ? "+" : "") + ((b.c[i] - Number(t.entry)) * sign / risk).toFixed(2) + " R (sobre el precio medio, sin costos; no es el resultado)" : " · la entrada es la apertura de la vela siguiente") + "</span>";
  }
  $("chips").innerHTML = names.filter(n => n !== "ATR14" && n !== "RSI14").map((n, i) => '<button class="chip' + (S.off[n] ? " off" : "") + '" data-n="' + esc(n) + '"><i style="background:var(' + SERIES[i % 5] + ')"></i>' + esc(n) + "</button>").join("") +
    '<span class="chip"><i style="background:var(--text);height:2px"></i>Entrada</span><span class="chip"><i style="background:var(--bad)"></i>Stop loss</span><span class="chip"><i style="background:var(--good)"></i>Take profit</span>';
  const at = (n) => { const v = ind[n] && t.i_sig != null ? ind[n][t.i_sig] : null; return v == null ? "—" : String(v); };
  const hide = '<span class="hidden">oculto</span>';
  let h = "<section><h3>Operación</h3>" + kv("ID", "<code>" + esc(t.id) + "</code>") + kv("Par · dirección", esc(t.sym) + " · " + esc(t.dir)) +
    kv("Cierre de la vela de señal", when(iso(t.sig))) + kv("Entrada (apertura siguiente)", when(iso(t.t_in))) +
    kv("Salida", blind ? hide : t.t_out ? when(iso(t.t_out)) : "sin salida") + kv("Duración", blind ? hide : t.hours == null ? "—" : t.hours + " h · " + t.bars_held + " velas") +
    kv("Motivo de salida", blind ? hide : (t.reason || "—") + (t.gap ? " con gap" : "") + (t.timing === "DENTRO_DE_LA_VELA" ? " · dentro de la vela" : t.timing ? " · en una apertura" : "")) +
    kv("Estado", blind ? hide : esc(t.status)) + "</section>";
  h += "<section><h3>Precios</h3>" + kv("Entrada estimada", t.entry) + kv("Stop loss", t.stop + " · " + t.sl_pips + " pips") + kv("Take profit", t.target + " · " + t.tp_pips + " pips · " + t.rr_gross + " R bruto") +
    kv("Precio de salida", blind ? hide : t.exit || "—") + kv("Bid / ask estimados", t.bid + " / " + t.ask) + kv("Spread · slippage asumidos", t.spread + " · " + t.slippage) + kv("R neto planificado", t.rr_net || "—") + "</section>";
  h += "<section><h3>Tamaño y riesgo</h3>" + kv("Unidades · lotes", t.units + " · " + t.lots) + kv("Nocional · margen", t.notional + " · " + t.margin + " USD") + kv("Capital antes", t.equity + " USD") +
    kv("Riesgo presupuestado", t.budget + " USD") + kv("Pérdida estimada en el stop", t.est_loss + " USD (" + t.risk_pct + " %)") + kv("Ganancia estimada en el objetivo", t.exp_profit + " USD") + "</section>";
  h += "<section><h3>Resultado</h3>" + kv("P&L neto", blind ? hide : t.pnl == null ? "sin resultado" + (t.unrealized ? " (no realizado " + signed(t.unrealized) + " USD)" : "") : signed(t.pnl) + " USD") +
    kv("Resultado en R", blind ? hide : signed(t.r)) + kv("Comisión · financiación", blind ? hide : (t.commission == null ? "—" : t.commission + " · " + t.financing + " USD · " + (t.nights || 0) + " noches")) +
    kv("Financiación", esc(t.fin_status)) + kv("Marcas del motor", "<span style='font-size:11px'>" + esc(t.flags.join(" ")) + "</span>") + "</section>";
  h += "<section><h3>Indicadores en la vela de señal</h3>" + names.map(n => kv(n, at(n))).join("") + (t.extra || []).map(x => kv(x[0], esc(x[1]))).join("") + kv("Condiciones", "<span style='font-size:11px'>" + esc(t.sig_reasons) + "</span>") + "</section>";
  $("sheet").innerHTML = h;
  draw();
}

// ---------- the chart ----------
let G = null;
function draw() {
  const t = tradeById(S.id); if (!t) return;
  clampView(t);
  const {g, w, h} = setup($("chart")), b = D.bars[t.sym], ind = strat().indicators[t.sym], blind = isBlind(t), end = lastVisible(t);
  const L = 6, R = 70, T = 8, AX = 22, gapP = 8, hasR = !!ind.RSI14, hasA = !!ind.ATR14;
  const subs = (hasR ? 1 : 0) + (hasA ? 1 : 0), subH = h < 500 ? 62 : 82, pH = h - T - AX - subs * (subH + gapP), pw = w - L - R, bw = pw / (S.x1 - S.x0);
  const X = i => L + (i - S.x0 + .5) * bw, i0 = Math.max(0, Math.floor(S.x0)), i1 = Math.min(end, Math.ceil(S.x1));
  const ema = Object.keys(ind).filter(n => n !== "RSI14" && n !== "ATR14");
  const entry = Number(t.entry), stop = Number(t.stop), target = Number(t.target), exit = !blind && t.exit ? Number(t.exit) : null;
  let lo = Math.min(entry, stop, target), hi = Math.max(entry, stop, target);
  for (let i = i0; i <= i1; i++) { lo = Math.min(lo, b.l[i]); hi = Math.max(hi, b.h[i]); ema.forEach(n => { const v = ind[n][i]; if (v != null && !S.off[n]) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }); }
  if (exit != null) { lo = Math.min(lo, exit); hi = Math.max(hi, exit); }
  const pad = (hi - lo) * .06 || 1e-4; lo -= pad; hi += pad;
  const Y = v => T + (hi - v) / (hi - lo) * pH;
  const ink = css("--text"), ink2 = css("--text-2"), ink3 = css("--text-3"), border = css("--border"), surface = css("--surface");
  g.font = "11px Segoe UI,system-ui,sans-serif"; g.textBaseline = "middle"; g.lineWidth = 1;
  g.save(); g.beginPath(); g.rect(L, 0, pw, h); g.clip();
  // region of the trade
  const until = blind ? Math.min(end + 1, S.x1) : (t.i_out != null ? t.i_out : t.i_last);
  if (until >= t.i_in) { g.fillStyle = css("--shade"); g.fillRect(X(t.i_in) - bw / 2, T, (until - t.i_in + (blind ? 0 : 1)) * bw, pH); }
  // gaps in time and day labels
  g.fillStyle = ink3; g.textAlign = "center";
  let lastLabel = -1e9, prevDay = null;
  for (let i = i0; i <= Math.min(b.t.length - 1, Math.ceil(S.x1)); i++) {
    if (i > 0 && b.t[i] - b.t[i - 1] !== 3600 && i <= end) { g.strokeStyle = ink3; g.setLineDash([2, 4]); g.beginPath(); g.moveTo(X(i) - bw / 2, T); g.lineTo(X(i) - bw / 2, h - AX); g.stroke(); g.setLineDash([]); }
    if (i > end) break;
    const p = parts(b.t[i]), day = p.month + p.day;
    if (day !== prevDay && prevDay !== null || i === i0) {
      if (X(i) - lastLabel > 58) { g.strokeStyle = border; g.beginPath(); g.moveTo(X(i) - bw / 2, T); g.lineTo(X(i) - bw / 2, h - AX + 4); g.stroke(); g.fillText(Number(p.day) + " " + MONTHS[Number(p.month) - 1] + (bw > 9 ? " " + p.hour + ":00" : ""), Math.max(L + 40, X(i)), h - AX / 2 + 2); lastLabel = X(i); }
    } else if (bw >= 22 && Number(p.hour) % 4 === 0 && X(i) - lastLabel > 46) { g.fillText(p.hour + ":00", X(i), h - AX / 2 + 2); lastLabel = X(i); }
    prevDay = day;
  }
  g.restore();
  // price grid and axis
  g.textAlign = "left";
  for (const v of nice(lo, hi, 6)) { g.strokeStyle = border; g.beginPath(); g.moveTo(L, Y(v)); g.lineTo(L + pw, Y(v)); g.stroke(); g.fillStyle = ink3; g.fillText(v.toFixed(5), L + pw + 6, Y(v)); }
  g.save(); g.beginPath(); g.rect(L, T, pw, pH); g.clip();
  // candles: rising hollow, falling filled
  const cw = Math.max(1, Math.min(bw * .7, 14));
  for (let i = i0; i <= i1; i++) {
    const up = b.c[i] >= b.o[i], x = X(i), yo = Y(b.o[i]), yc = Y(b.c[i]);
    g.strokeStyle = up ? ink : ink2; g.beginPath(); g.moveTo(x, Y(b.h[i])); g.lineTo(x, Y(b.l[i])); g.stroke();
    const top = Math.min(yo, yc), ht = Math.max(1, Math.abs(yc - yo));
    g.fillStyle = up ? surface : ink2; g.fillRect(x - cw / 2, top, cw, ht); g.strokeRect(x - cw / 2, top, cw, ht);
  }
  // indicator lines, broken where the strategy recorded nothing
  ema.forEach((n, k) => {
    if (S.off[n]) return;
    g.strokeStyle = css(SERIES[k % 5]); g.lineWidth = 2; g.lineJoin = "round"; g.beginPath(); let pen = false;
    for (let i = i0; i <= i1; i++) { const v = ind[n][i]; if (v == null) { pen = false; continue; } if (pen) g.lineTo(X(i), Y(v)); else g.moveTo(X(i), Y(v)); pen = true; }
    g.stroke(); g.lineWidth = 1;
  });
  // levels
  const xa = X(t.i_in) - bw / 2, xb = blind ? L + pw : X(t.i_out != null ? t.i_out : t.i_last) + bw / 2;
  const level = (v, color, dash) => { g.strokeStyle = color; g.lineWidth = 2; g.setLineDash(dash); g.beginPath(); g.moveTo(xa, Y(v)); g.lineTo(Math.max(xb, xa + 6), Y(v)); g.stroke(); g.setLineDash([]); g.lineWidth = 1; };
  level(stop, css("--bad"), []); level(target, css("--good"), []); level(entry, ink, [6, 4]);
  // markers: signal candle, entry, exit
  if (t.i_sig != null && t.i_sig >= i0 && t.i_sig <= i1) { const up = t.dir === "LONG", y = up ? Y(b.l[t.i_sig]) + 10 : Y(b.h[t.i_sig]) - 10; g.strokeStyle = surface; g.lineWidth = 2; g.fillStyle = css("--s1"); g.beginPath(); g.arc(X(t.i_sig), y, 5, 0, 6.3); g.fill(); g.stroke(); g.lineWidth = 1; }
  { const up = t.dir === "LONG", x = xa, y = Y(entry), s = 7; g.fillStyle = ink; g.strokeStyle = surface; g.lineWidth = 2; g.beginPath(); g.moveTo(x - s, y + (up ? s : -s)); g.lineTo(x + s, y + (up ? s : -s)); g.lineTo(x, y + (up ? -s * .6 : s * .6)); g.closePath(); g.fill(); g.stroke(); g.lineWidth = 1; }
  if (exit != null && t.i_out != null) { const x = t.timing === "EN_LA_APERTURA" ? X(t.i_out) - bw / 2 : X(t.i_out), y = Y(exit), s = 6; g.strokeStyle = surface; g.lineWidth = 5; g.beginPath(); g.moveTo(x - s, y - s); g.lineTo(x + s, y + s); g.moveTo(x + s, y - s); g.lineTo(x - s, y + s); g.stroke(); g.strokeStyle = ink; g.lineWidth = 2.5; g.stroke(); g.lineWidth = 1; }
  g.restore();
  // axis tags for the levels (ink text, a coloured stripe carries the identity)
  const tag = (v, color, text) => { const y = Math.max(T + 8, Math.min(T + pH - 8, Y(v))); g.fillStyle = surface; g.fillRect(L + pw + 1, y - 8, R - 2, 16); g.fillStyle = color; g.fillRect(L + pw + 1, y - 8, 3, 16); g.fillStyle = ink; g.fillText(text, L + pw + 7, y); };
  tag(stop, css("--bad"), "SL " + t.stop); tag(target, css("--good"), "TP " + t.target); tag(entry, ink, "E " + t.entry);
  // RSI and ATR panels, each with its own axis
  let top = T + pH + gapP; const panels = [];
  const sub = (name, values, fixed, fmt, refs) => {
    let a = fixed ? fixed[0] : Infinity, z = fixed ? fixed[1] : -Infinity;
    if (!fixed) { for (let i = i0; i <= i1; i++) { const v = values[i]; if (v != null) { a = Math.min(a, v); z = Math.max(z, v); } } if (a === Infinity) { a = 0; z = 1; } const p = (z - a) * .1 || z * .1 || 1; a -= p; z += p; }
    const y = v => top + (z - v) / (z - a) * subH;
    g.strokeStyle = border; g.strokeRect(L + .5, top + .5, pw - 1, subH - 1);
    g.fillStyle = ink3; g.textAlign = "left";
    for (const v of refs || nice(a, z, 2)) { if (v <= a || v >= z) continue; g.strokeStyle = border; g.setLineDash(refs ? [3, 3] : []); g.beginPath(); g.moveTo(L, y(v)); g.lineTo(L + pw, y(v)); g.stroke(); g.setLineDash([]); g.fillText(fmt(v), L + pw + 6, y(v)); }
    g.fillStyle = ink2; g.fillText(name, L + 6, top + 9);
    g.save(); g.beginPath(); g.rect(L, top, pw, subH); g.clip(); g.strokeStyle = ink2; g.lineWidth = 2; g.lineJoin = "round"; g.beginPath(); let pen = false;
    for (let i = i0; i <= i1; i++) { const v = values[i]; if (v == null) { pen = false; continue; } if (pen) g.lineTo(X(i), y(v)); else g.moveTo(X(i), y(v)); pen = true; }
    g.stroke(); g.lineWidth = 1; g.restore(); panels.push({top, y}); top += subH + gapP;
  };
  const pip = 1e4;
  if (hasR) sub("RSI14", ind.RSI14, [0, 100], v => v.toFixed(0), [30, 50, 70]);
  if (hasA) sub("ATR14 (pips)", ind.ATR14.map(v => v == null ? null : v * pip), null, v => v.toFixed(1));
  G = {L, R, T, pw, pH, bw, i0, i1, X, Y, lo, hi, w, h, end};
  // crosshair
  if (S.hover != null && S.hover >= i0 && S.hover <= i1) { g.strokeStyle = ink3; g.setLineDash([3, 3]); g.beginPath(); g.moveTo(X(S.hover), T); g.lineTo(X(S.hover), h - AX); g.stroke(); g.setLineDash([]); }
}
function tip(e) {
  const t = tradeById(S.id), box = $("tip"); if (!t || !G) return;
  const r = $("chart").getBoundingClientRect(), px = e.clientX - r.left, i = Math.floor(S.x0 + (px - G.L) / G.bw);
  if (i < G.i0 || i > G.i1 || px > G.L + G.pw) { S.hover = null; box.style.display = "none"; draw(); return; }
  S.hover = i; draw();
  const b = D.bars[t.sym], ind = strat().indicators[t.sym], names = Object.keys(ind).filter(n => n !== "RSI14" && n !== "ATR14");
  let h = "<b>" + when(b.t[i]) + "</b> <span class='muted'>" + zoneName() + "</span>" + ["o", "h", "l", "c"].map((k, j) => "<div><span>" + ["Apertura", "Máximo", "Mínimo", "Cierre"][j] + "</span><span>" + b[k][i].toFixed(5) + "</span></div>").join("");
  names.forEach((n, k) => { if (!S.off[n]) h += "<div><span><i style='background:var(" + SERIES[k % 5] + ")'></i>" + esc(n) + "</span><span>" + (ind[n][i] == null ? "—" : ind[n][i].toFixed(5)) + "</span></div>"; });
  if (ind.RSI14) h += "<div><span>RSI14</span><span>" + (ind.RSI14[i] == null ? "—" : ind.RSI14[i].toFixed(2)) + "</span></div>";
  if (ind.ATR14) h += "<div><span>ATR14</span><span>" + (ind.ATR14[i] == null ? "—" : (ind.ATR14[i] * 1e4).toFixed(1) + " pips") + "</span></div>";
  if (i === t.i_sig) h += "<div><span>Vela de la señal</span><span></span></div>";
  if (i === t.i_in) h += "<div><span>Vela de entrada</span><span>" + t.entry + "</span></div>";
  if (!isBlind(t) && i === t.i_out) h += "<div><span>Salida · " + esc(t.reason) + "</span><span>" + t.exit + "</span></div>";
  box.innerHTML = h; box.style.display = "block";
  const bwid = box.offsetWidth; box.style.left = Math.max(4, px + 14 + bwid > r.width - G.R ? px - bwid - 14 : px + 14) + "px"; box.style.top = "10px";
}
function zoom(factor, center) {
  const c = center == null ? (S.x0 + S.x1) / 2 : center, w = (S.x1 - S.x0) * factor;
  S.x0 = c - (c - S.x0) * factor; S.x1 = S.x0 + w; draw();
}

// ---------- replay ----------
function advance(n) {
  const t = tradeById(S.id); if (!t || !isBlind(t)) return;
  S.step = Math.min(maxStep(t), S.step + n); follow(t); renderTrade();
  if (S.step >= maxStep(t)) stopPlay();
}
function stopPlay() { if (S.timer) { clearInterval(S.timer); S.timer = null; } $("play").textContent = "Reproducir"; }
function reveal() { const t = tradeById(S.id); if (!t) return; stopPlay(); S.revealed[t.id] = true; resetView(t); renderTable(); renderTrade(); }
function setBlind(on) {
  stopPlay(); S.blind = on; S.revealed = {}; S.step = 0;
  $("modeBlind").classList.toggle("on", on); $("modeFull").classList.toggle("on", !on);
  $("fOut").disabled = $("fExit").disabled = $("csv").disabled = on;
  if (on) { $("fOut").value = ""; $("fExit").value = ""; if (COLS.find(c => c.k === S.sort.key && c.res)) S.sort = {key: "order", dir: 1}; }
  const t = tradeById(S.id); if (t) resetView(t);
  renderTable(); renderTrade();
}

// ---------- export ----------
function csvText(rows) {
  const q = v => { v = v == null ? "" : v === true ? "SI" : v === false ? "NO" : String(v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; };
  return D.csv_columns.map(c => q(c[0])).join(",") + "\n" + rows.map(t => D.csv_columns.map(c => q(t[c[1]])).join(",")).join("\n") + "\n";
}
function exportCsv() {
  if (S.blind) return;
  const blob = new Blob(["﻿" + csvText(filtered())], {type: "text/csv;charset=utf-8"}), a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "strategy_" + strat().label + "_trades_seleccion.csv";
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ---------- wiring ----------
function setStrategy(i) {
  S.si = i; S.off = {};
  [...$("tabs").children].forEach((b, k) => b.classList.toggle("on", k === i));
  const tr = strat().trades, syms = [...new Set(tr.map(t => t.sym))], exits = [...new Set(tr.map(t => t.reason).filter(Boolean))].sort();
  $("fSym").innerHTML = '<option value="">Todos</option>' + syms.map(s => "<option>" + esc(s) + "</option>").join("");
  $("fExit").innerHTML = '<option value="">Todos</option>' + exits.map(s => "<option>" + esc(s) + "</option>").join("");
  const ex = tr.filter(t => !t.valid).length;
  $("showEx").checked = S.showEx = false; $("showEx").disabled = !ex;
  $("exLabel").textContent = ex ? "Mostrar las " + ex + " fuera del conteo (abiertas o indeterminadas)" : "Ninguna operación fuera del conteo";
  S.revealed = {}; const first = filtered()[0]; S.id = null; if (first) select(first.id); else { renderTable(); }
}
function refresh() { const rows = filtered(); if (!rows.some(t => t.id === S.id) && rows[0]) select(rows[0].id); else { renderTable(); } }
function init() {
  renderStatic();
  $("tabs").onclick = e => { const b = e.target.closest("button"); if (b) setStrategy(Number(b.dataset.i)); };
  ["q", "fSym", "fDir", "fOut", "fExit"].forEach(id => $(id).addEventListener("input", refresh));
  $("showEx").onchange = e => { S.showEx = e.target.checked; refresh(); };
  $("reset").onclick = () => { ["q", "fSym", "fDir", "fOut", "fExit"].forEach(id => $(id).value = ""); refresh(); };
  $("zone").onchange = e => { S.zone = e.target.value; renderTable(); renderTrade(); };
  $("modeFull").onclick = () => setBlind(false); $("modeBlind").onclick = () => setBlind(true);
  $("thead").onclick = e => { const th = e.target.closest("th"); if (!th || th.classList.contains("nosort")) return; const k = th.dataset.k; S.sort = {key: k, dir: S.sort.key === k ? -S.sort.dir : 1}; renderTable(); };
  $("tbody").onclick = e => { const tr = e.target.closest("tr"); if (tr && tr.dataset.id) select(tr.dataset.id); };
  $("pick").onchange = e => select(e.target.value);
  const move = d => { const rows = filtered(), p = rows.findIndex(t => t.id === S.id); if (rows[p + d]) select(rows[p + d].id); };
  $("prev").onclick = () => move(-1); $("next").onclick = () => move(1);
  $("step1").onclick = () => advance(1); $("step5").onclick = () => advance(5);
  $("play").onclick = () => { if (S.timer) return stopPlay(); S.timer = setInterval(() => advance(1), 700); $("play").textContent = "Pausa"; };
  $("restart").onclick = () => { const t = tradeById(S.id); if (!t) return; stopPlay(); delete S.revealed[t.id]; S.step = 0; resetView(t); renderTable(); renderTrade(); };
  $("reveal").onclick = reveal; $("full").onclick = reveal;
  $("chips").onclick = e => { const b = e.target.closest("button"); if (b) { S.off[b.dataset.n] = !S.off[b.dataset.n]; renderTrade(); } };
  $("csv").onclick = exportCsv;
  $("zin").onclick = () => zoom(.7); $("zout").onclick = () => zoom(1.4); $("zreset").onclick = () => { const t = tradeById(S.id); if (t) { resetView(t); draw(); } };
  const c = $("chart"); let drag = null;
  c.addEventListener("pointerdown", e => { drag = {x: e.clientX, x0: S.x0, x1: S.x1}; c.setPointerCapture(e.pointerId); });
  c.addEventListener("pointermove", e => { if (drag && G) { const d = (e.clientX - drag.x) / G.bw; S.x0 = drag.x0 - d; S.x1 = drag.x1 - d; $("tip").style.display = "none"; S.hover = null; draw(); } else tip(e); });
  const up = () => { drag = null; }; c.addEventListener("pointerup", up); c.addEventListener("pointercancel", up);
  c.addEventListener("pointerleave", () => { if (!drag) { S.hover = null; $("tip").style.display = "none"; draw(); } });
  c.addEventListener("wheel", e => { if (!G) return; e.preventDefault(); const r = c.getBoundingClientRect(); zoom(e.deltaY > 0 ? 1.2 : 1 / 1.2, S.x0 + (e.clientX - r.left - G.L) / G.bw); }, {passive: false});
  c.addEventListener("dblclick", () => { const t = tradeById(S.id); if (t) { resetView(t); draw(); } });
  document.addEventListener("keydown", e => {
    if (/INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) return;
    if (e.key === "ArrowRight") { S.blind && isBlind(tradeById(S.id)) ? advance(1) : move(1); e.preventDefault(); }
    else if (e.key === "ArrowLeft" && !S.blind) { move(-1); e.preventDefault(); }
  });
  window.addEventListener("resize", () => { draw(); if (!S.blind) renderSelection(filtered()); });
  if (window.matchMedia) window.matchMedia("(prefers-color-scheme:dark)").addEventListener("change", () => { draw(); if (!S.blind) renderSelection(filtered()); });
  setStrategy(0);
  document.body.dataset.ready = "1";
  if (location.hash === "#selftest") selftest();
}

// ---------- self-check of the controls (file.html#selftest) ----------
function selftest() {
  const out = {check: document.body.dataset.check, strategies: []}, text = () => document.querySelector("main").innerText;
  D.strategies.forEach((s, i) => {
    setStrategy(i); const r = {label: s.label, rows: filtered().length, valid: s.trades.filter(t => t.valid).length, total: s.trades.length};
    $("fDir").value = "LONG"; refresh(); r.long = filtered().length; $("fDir").value = "SHORT"; refresh(); r.short = filtered().length; $("fDir").value = "";
    $("fOut").value = "GANADORA"; refresh(); r.winners = filtered().length; $("fOut").value = "";
    $("fExit").value = "STOP"; refresh(); r.stops = filtered().length; $("fExit").value = ""; refresh();
    S.sort = {key: "pnl", dir: -1}; renderTable(); r.best = filtered()[0].pnl; S.sort = {key: "order", dir: 1}; renderTable();
    r.csvLines = csvText(filtered()).trim().split("\n").length;
    const t = s.trades.find(x => x.valid && x.outcome === "GANADORA") || s.trades[0];
    select(t.id); r.fullShowsResult = $("sheet").innerText.includes(signed(t.pnl) + " USD");
    setBlind(true); select(t.id);
    r.blindHidesPnl = !text().includes(signed(t.pnl) + " USD") && !$("tradeCard").innerText.includes(t.outcome) && !$("tbody").innerText.includes("GANADORA") && !$("tbody").innerText.includes("PERDEDORA");
    r.blindStart = lastVisible(t) === t.i_in - 1; advance(1); advance(5); r.blindStep = lastVisible(t) === t.i_in + 5 && G.i1 <= t.i_in + 5;
    r.blindStillHidden = $("sheet").innerText.includes("oculto") && !$("sheet").innerText.includes(signed(t.pnl) + " USD");
    reveal(); r.revealShows = $("sheet").innerText.includes(signed(t.pnl) + " USD") && $("tbody").querySelector("tr.sel").innerText.includes(t.outcome);
    r.othersStillHidden = [...$("tbody").querySelectorAll("tr:not(.sel)")].every(x => x.innerText.includes("oculto"));
    setBlind(false); r.drawn = !!G && G.i1 > G.i0;
    out.strategies.push(r);
  });
  setStrategy(0);
  $("selftest").textContent = JSON.stringify(out);
  document.body.dataset.selftest = "done";
}
init();
</script>
</body>
</html>
'''
