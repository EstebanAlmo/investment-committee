"""Files to share a review with someone who has no access to the
repository: one trade as an interactive HTML or a PNG, the filtered trades
as a CSV, several trades as one HTML report. Needs the optional `viewer`
dependencies.

Every export names strategy, pair, date, scenario, period, trade ID, the
data kind (ESTIMADA for the Yahoo study), the study's hash and the
simulation warning. Nothing identifies this machine: no path, no user
name, no environment. HTML files carry Plotly inside them, so they open
without internet.

PNG needs Kaleido and a Chrome or Chromium already installed; none is ever
installed from here. Without one, PngUnavailable explains it and the HTML
export keeps working.
"""

from __future__ import annotations

import csv
import html
import io
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .charts import trade_figure
from .trade_view import DEFAULT_ZONE, SIMULATION_WARNING, TradeView, show_time

STYLE = """
body{font-family:Segoe UI,Helvetica,Arial,sans-serif;margin:24px;color:#1f2933;max-width:1250px}
h1{font-size:20px} h2{font-size:16px;margin-top:28px;border-bottom:1px solid #d0d7de;padding-bottom:4px}
table{border-collapse:collapse;font-size:13px;margin:8px 0} td,th{border:1px solid #d0d7de;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#f3f4f6} .aviso{background:#fff8e1;border:1px solid #f0c36d;padding:8px 12px;font-size:13px;margin:12px 0}
.tipo{color:#5f6b7a;font-size:11px} .nota{background:#eef6ff;border:1px solid #b6d4fe;padding:8px 12px;font-size:13px}
"""


class PngUnavailable(Exception):
    """The PNG could not be rendered (Kaleido or its browser is missing)."""


def identity(view: TradeView, zone: str = DEFAULT_ZONE) -> dict[str, str]:
    """What every export states about the trade it shows."""
    return {
        "Estrategia": view.trade["strategy_id"], "Par": view.symbol, "Temporalidad": "H1", "Dirección": view.direction,
        "Fecha de entrada": show_time(view.entry_at, zone), "Escenario": view.key.scenario, "Período": view.key.period, "ID de operación": view.trade_id,
        "Fuente de datos": view.data_kind, "Estudio (hash)": view.study_sha256, "Advertencia": SIMULATION_WARNING,
    }


def safe_name(view: TradeView) -> str:
    """A file name for the trade that is valid on Windows and macOS."""
    raw = f"{view.trade['strategy_id']}_{view.symbol}_{view.entry_at.strftime('%Y%m%dT%H%MZ')}_{view.direction}_{view.key.scenario}_{view.key.period}"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw)


def _table(rows: Sequence[Sequence[str]], header: Sequence[str] | None = None) -> str:
    head = "" if header is None else "<tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in header) + "</tr>"
    return "<table>" + head + "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>" for row in rows) + "</table>"


def _note_block(note: Mapping[str, Any] | None) -> str:
    if not note:
        return ""
    rows = [("Calificación", note.get("rating", "")), ("Comentario", note.get("comment", "")), ("Problema identificado", note.get("problem", "")),
            ("Mejora sugerida", note.get("improvement", "")), ("Revisor", note.get("reviewer", "")), ("Fecha de revisión", note.get("reviewed_at", ""))]
    return ("<h2>Revisión humana (cualitativa; no modifica la operación ni la estrategia)</h2><div class='nota'>" + _table(rows) + "</div>")


def trade_section(view: TradeView, zone: str = DEFAULT_ZONE, *, note: Mapping[str, Any] | None = None, include_plotlyjs: bool | str = True,
                  show_rejected: bool = False) -> str:
    """The chart, identity, sheet and note of one trade as an HTML fragment."""
    figure = trade_figure(view, zone, show_rejected=show_rejected)
    sheet = _table([(f.section, f.label, f.value, f.kind) for f in view.sheet], ("Sección", "Campo", "Valor", "Tipo de dato"))
    return (f"<h2>{html.escape(view.symbol)} {html.escape(view.direction)} · {html.escape(view.outcome)} · {html.escape(view.trade_id)}</h2>"
            + _table(list(identity(view, zone).items()))
            + figure.to_html(full_html=False, include_plotlyjs=include_plotlyjs)
            + "<p class='tipo'>OBSERVADO: precio y reloj de Yahoo. ESTIMADO: modelo de ejecución y su dinero. DERIVADO: aritmética sobre los anteriores.</p>"
            + sheet + _note_block(note))


def _page(title: str, body: str) -> str:
    return (f"<!DOCTYPE html><html lang='es'><head><meta charset='utf-8'><title>{html.escape(title)}</title><style>{STYLE}</style></head><body>"
            f"<h1>{html.escape(title)}</h1><div class='aviso'>{html.escape(SIMULATION_WARNING)} No es una validación independiente ni una recomendación.</div>"
            f"{body}</body></html>")


def trade_html(view: TradeView, zone: str = DEFAULT_ZONE, *, note: Mapping[str, Any] | None = None, show_rejected: bool = False) -> str:
    """One trade as a self-contained interactive page."""
    return _page(f"Operación simulada · {view.trade['strategy_id']} · {view.symbol} · {view.key.scenario}",
                 trade_section(view, zone, note=note, show_rejected=show_rejected))


def trade_png(view: TradeView, zone: str = DEFAULT_ZONE, *, show_rejected: bool = False) -> bytes:
    """The trade's chart as a high-resolution PNG (1600 x 1000 at 2x)."""
    figure = trade_figure(view, zone, show_rejected=show_rejected)
    try:
        return figure.to_image(format="png", width=1600, height=1000, scale=2)
    except Exception as error:  # Kaleido raises several unrelated types when the browser is missing
        raise PngUnavailable(
            "No se pudo generar el PNG. Kaleido necesita Google Chrome o Chromium instalado en este equipo; no se instala ninguno automáticamente. "
            f"La exportación HTML sigue disponible. Detalle: {type(error).__name__}: {str(error)[:200]}") from error


def trades_csv(rows: Sequence[Mapping[str, Any]], study_sha256: str, data_kind: str) -> str:
    """The explorer's rows as CSV, each one carrying the study hash, the
    data kind and the warning."""
    out = io.StringIO()
    extra = {"Hash del estudio": study_sha256, "Fuente de datos": data_kind, "Advertencia": SIMULATION_WARNING}
    header = [*(rows[0] if rows else []), *extra]
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if row[name] is None else row[name] for name in rows[0]] + list(extra.values()))
    return out.getvalue()


def report_html(title: str, views: Sequence[TradeView], *, summary_rows: Sequence[Sequence[str]], limitations: Sequence[str], zone: str = DEFAULT_ZONE,
                notes: Mapping[str, Mapping[str, Any]] | None = None, show_rejected: bool = False) -> str:
    """Several trades in one page: the run's figures, the limitations, then
    each trade with its chart, sheet and review note. Plotly is embedded
    once."""
    notes = notes or {}
    body = "<h2>Métricas de la corrida</h2>" + _table(summary_rows, ("Métrica", "Valor"))
    body += "<h2>Limitaciones</h2><ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in limitations) + "</ul>"
    body += f"<h2>Operaciones seleccionadas ({len(views)})</h2>"
    for position, view in enumerate(views):
        body += trade_section(view, zone, note=notes.get(view.trade_id), include_plotlyjs=True if position == 0 else False, show_rejected=show_rejected)
    return _page(title, body)


def write_export(directory: Path, name: str, content: str | bytes) -> Path:
    """Write one export under `directory` (created if needed)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    return path
