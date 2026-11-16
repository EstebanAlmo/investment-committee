"""Plotly figures of the trade viewer. Needs the optional `viewer`
dependencies. Every number drawn comes from a TradeView / Run (the verified
study); this module only decides how it looks.

The x axis is the position of each bar in the window, not clock time, so a
weekend or a data hole takes no room; the hours are written on the axis in
the chosen zone and both kinds of interruption are marked with a vertical
line (dotted grey: market closed; dashed amber: expected hours without a
bar). Decimals become floats here, for drawing only.

Reading rules written into the chart itself:
  - candles are Yahoo's mid price; entry, stop, target and exit are the
    ESTIMATED executions of the engine;
  - the thin dotted lines are the stop and target moved onto the candles'
    price (DERIVED): that is where a candle has to reach;
  - an exit inside a bar shades the whole bar - the moment within the hour
    is unknown; an exit at an opening is a point on that bar;
  - an indeterminate trade shows both hypothetical exits and never one.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from investment_committee.domain import PriceBar

from .study_store import Run, instant, number
from .trade_view import DEFAULT_ZONE, INSIDE_BAR, LOST, SIMULATION_WARNING, STILL_OPEN, UNKNOWN, WON, ZONES, IntervalView, TradeView, _px, pips, show_time

HOUR = timedelta(hours=1)
COLORS = {WON: "#1a7f37", LOST: "#c62828", UNKNOWN: "#b26a00", STILL_OPEN: "#5f6b7a", "NEUTRA": "#5f6b7a"}
EMA_COLORS = ("#1565c0", "#ef6c00", "#6a1b9a", "#00838f", "#4e342e")
STRATEGY_COLORS = ("#1565c0", "#ad1457", "#2e7d32", "#ef6c00")


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _short(moment: datetime, zone: str) -> str:
    return moment.astimezone(ZoneInfo(ZONES[zone])).strftime("%d-%m %H:%M")


def _local(text: str, zone: str) -> datetime:
    moment = instant(text)
    assert moment is not None
    return moment.astimezone(ZoneInfo(ZONES[zone])).replace(tzinfo=None)


def _candles(bars: Sequence[PriceBar], zone: str) -> go.Candlestick:
    return go.Candlestick(
        x=list(range(len(bars))), open=[float(b.open) for b in bars], high=[float(b.high) for b in bars], low=[float(b.low) for b in bars],
        close=[float(b.close) for b in bars], name="Precio medio de Yahoo (H1, observado)", text=[show_time(b.timestamp, zone) for b in bars],
        increasing_line_color="#26a69a", decreasing_line_color="#ef5350")


def _time_axis(figure: go.Figure, bars: Sequence[PriceBar], zone: str, row: int | None) -> None:
    step = max(1, len(bars) // 12)
    ticks = list(range(0, len(bars), step))
    where = {} if row is None else {"row": row, "col": 1}  # None: a figure without subplots
    figure.update_xaxes(tickvals=ticks, ticktext=[_short(bars[i].timestamp, zone) for i in ticks], title_text=f"Hora de apertura de cada vela ({zone})", **where)


def _interruptions(figure: go.Figure, bars: Sequence[PriceBar], holes: Sequence[tuple[int, tuple[datetime, ...]]]) -> None:
    missing = dict(holes)
    for index in range(len(bars) - 1):
        if index in missing:
            figure.add_vline(x=index + 0.5, line_dash="dash", line_color="#b26a00", line_width=1.5, row=1, col=1,
                             annotation_text=f"sin datos: {len(missing[index])} h", annotation_font_color="#b26a00")
        elif bars[index + 1].timestamp - bars[index].timestamp > HOUR:
            figure.add_vline(x=index + 0.5, line_dash="dot", line_color="#9aa0a6", line_width=1, row=1, col=1, annotation_text="mercado cerrado",
                             annotation_font_color="#9aa0a6")


def trade_title(view: TradeView, zone: str = DEFAULT_ZONE) -> str:
    result = (f"{view.net_pnl} USD · {view.r_multiple} R" if view.net_pnl is not None
              else f"conservador {view.conservative['net_pnl']} USD / optimista {view.optimistic['net_pnl']} USD" if view.conservative and view.optimistic
              else "sin resultado")
    return (f"{view.trade['strategy_id']} · {view.symbol} · H1 · {view.direction} · {view.outcome}"
            f"{' · ' + view.exit_reason if view.exit_reason else ''} · {result}<br>"
            f"<sup>ID {view.trade_id} · escenario {view.key.scenario} · período {view.key.period} · fuente {view.data_kind} · "
            f"estudio {view.study_sha256[:12]} · entrada {show_time(view.entry_at, zone)}</sup><br><sup>{SIMULATION_WARNING}</sup>")


def trade_figure(view: TradeView, zone: str = DEFAULT_ZONE, *, show_rejected: bool = False, show_rsi: bool = True, show_atr: bool = True) -> go.Figure:
    panels = 1 + show_rsi + show_atr
    heights = [0.62, 0.19, 0.19][:panels] if panels == 3 else [0.75, 0.25][:panels] if panels == 2 else [1.0]
    figure = make_subplots(rows=panels, cols=1, shared_xaxes=True, vertical_spacing=0.04, row_heights=heights)
    bars, color, n = view.bars, COLORS[view.outcome], len(view.bars)
    figure.add_trace(_candles(bars, zone), row=1, col=1)
    for (name, values), line in zip(view.indicators.items(), EMA_COLORS):
        figure.add_trace(go.Scatter(x=list(range(n)), y=[_f(v) for v in values], mode="lines", name=f"{name} (registrada por la estrategia)",
                                    line=dict(color=line, width=1.3), connectgaps=False), row=1, col=1)

    left, right = view.entry_index - 0.5, view.last_index + 0.5
    span = [left, right]

    def level(price: Decimal, name: str, tone: str, dash: str, width: float) -> None:
        figure.add_trace(go.Scatter(x=span, y=[float(price)] * 2, mode="lines", name=name, line=dict(color=tone, dash=dash, width=width)), row=1, col=1)

    level(view.entry, f"Entrada {view.entry} (ESTIMADA)", "#37474f", "solid", 1.6)
    level(view.stop, f"Stop loss {view.stop} · {view.stop_pips:f} pips · 1 R (ESTIMADO)", "#c62828", "dash", 1.6)
    level(view.target, f"Take profit {view.target} · {view.target_pips:f} pips · {view.reward_risk_gross.quantize(Decimal('0.01'))} R (ESTIMADO)",
          "#1a7f37", "dash", 1.6)
    level(view.stop_trigger_mid, f"Stop sobre precio medio {_px(view.stop_trigger_mid)} (DERIVADO)", "#c62828", "dot", 0.9)
    level(view.target_trigger_mid, f"Objetivo sobre precio medio {_px(view.target_trigger_mid)} (DERIVADO)", "#1a7f37", "dot", 0.9)
    figure.add_shape(type="rect", x0=left, x1=right, y0=float(view.entry), y1=float(view.stop), fillcolor="#c62828", opacity=0.07, line_width=0,
                     name="zona de riesgo", row=1, col=1)
    figure.add_shape(type="rect", x0=left, x1=right, y0=float(view.entry), y1=float(view.target), fillcolor="#1a7f37", opacity=0.07, line_width=0,
                     name="zona de recompensa", row=1, col=1)

    def mark(x: float, y: Decimal, name: str, symbol: str, tone: str, size: int = 13) -> None:
        figure.add_trace(go.Scatter(x=[x], y=[float(y)], mode="markers", name=name,
                                    marker=dict(symbol=symbol, size=size, color=tone, line=dict(color="#111", width=1))), row=1, col=1)

    if view.signal_index is not None:
        mark(view.signal_index, bars[view.signal_index].close, f"Vela de señal (cierra {show_time(view.signal_candle_close_at, zone)})", "diamond", "#fbc02d")
    mark(view.entry_index, view.entry, f"Entrada {view.direction} en la apertura · {show_time(view.entry_at, zone)}",
         "triangle-up" if view.direction == "LONG" else "triangle-down", "#37474f", 15)
    if view.exit_index is not None and view.exit_price is not None:
        where = "dentro de esta vela, instante desconocido" if view.exit_timing == INSIDE_BAR else "en la apertura de esta vela"
        mark(view.exit_index, view.exit_price, f"Salida {view.exit_reason}{' con gap' if view.trade['result']['gap'] else ''} {view.exit_price} ({where})", "x", color, 14)
        if view.exit_timing == INSIDE_BAR:
            figure.add_vrect(x0=view.exit_index - 0.5, x1=view.exit_index + 0.5, fillcolor=color, opacity=0.15, line_width=0, row=1, col=1)
    if view.uncertainty_indexes is not None and view.conservative and view.optimistic:
        low, high = view.uncertainty_indexes
        figure.add_vrect(x0=low - 0.5, x1=high + 0.5, fillcolor=COLORS[UNKNOWN], opacity=0.18, line_width=0, row=1, col=1,
                         annotation_text="ORDEN NO OBSERVABLE", annotation_font_color=COLORS[UNKNOWN], annotation_position="bottom left")
        mark(high, number(view.conservative["exit_price"]) or view.stop, f"Si el stop fue primero: {view.conservative['net_pnl']} USD (hipotético)",
             "x-open", "#c62828", 14)
        mark(high, number(view.optimistic["exit_price"]) or view.target, f"Si el objetivo fue primero: {view.optimistic['net_pnl']} USD (hipotético)",
             "x-open", "#1a7f37", 14)
    if show_rejected:
        for reasons in dict.fromkeys(r.reasons for r in view.rejected if r.index is not None):
            group = [r for r in view.rejected if r.reasons == reasons and r.index is not None]
            figure.add_trace(go.Scatter(
                x=[r.index for r in group], y=[float(bars[r.index].open) for r in group], mode="markers",  # type: ignore[index]
                name="Señal rechazada: " + " + ".join(reasons), text=[f"{r.direction} · {show_time(r.at, zone)}<br>{r.detail}" for r in group],
                marker=dict(symbol="circle-open", size=12, color="#6a1b9a", line=dict(width=2))), row=1, col=1)
    _interruptions(figure, bars, view.holes)

    row = 1
    if show_rsi:
        row += 1
        figure.add_trace(go.Scatter(x=list(range(n)), y=[_f(v) for v in view.rsi], mode="lines", name="RSI14 (registrado)", line=dict(color="#455a64", width=1.2),
                                    connectgaps=False), row=row, col=1)
        figure.add_hline(y=50, line_dash="dot", line_color="#9aa0a6", row=row, col=1)
        figure.update_yaxes(title_text="RSI14", row=row, col=1)
    if show_atr:
        row += 1
        figure.add_trace(go.Scatter(x=list(range(n)), y=[None if v is None else float(pips(view.symbol, v)) for v in view.atr], mode="lines",
                                    name="ATR14 en pips (registrado)", line=dict(color="#795548", width=1.2), connectgaps=False), row=row, col=1)
        figure.update_yaxes(title_text="ATR14 (pips)", row=row, col=1)
    _time_axis(figure, bars, zone, row)
    figure.update_yaxes(title_text=f"{view.symbol} (precio)", tickformat=".5f", row=1, col=1)
    figure.update_layout(title=dict(text=trade_title(view, zone), font=dict(size=15)), xaxis_rangeslider_visible=False, template="plotly_white",
                         height=760 if panels == 3 else 640, margin=dict(l=70, r=30, t=120, b=60), hovermode="x unified",
                         legend=dict(orientation="h", yanchor="top", y=-0.12, font=dict(size=10)))
    return figure


def interval_figure(interval: IntervalView, zone: str = DEFAULT_ZONE) -> go.Figure:
    """Each run's entries, exits and rejected signals on the same candles.
    Nothing is netted or merged: one colour per strategy."""
    figure = go.Figure(_candles(interval.bars, zone))
    index = {bar.timestamp: i for i, bar in enumerate(interval.bars)}
    for activity, color in zip(interval.activity, STRATEGY_COLORS):
        name = f"{activity.key.strategy_id} ({activity.key.scenario}/{activity.key.period})"
        entries = [(index[instant(t["entry_at"])], t) for t in activity.trades if instant(t["entry_at"]) in index]  # type: ignore[index]
        exits = [(index[instant(t["exit_at"])], t) for t in activity.trades if t["exit_at"] and instant(t["exit_at"]) in index]  # type: ignore[index]
        rejected = [(index[instant(d["at"])], d) for d in activity.rejected if instant(d["at"]) in index]  # type: ignore[index]
        for points, label, symbol, price, text in (
            (entries, "entradas", "triangle-up", lambda t: t["proposal"]["entry"],
             lambda t: f"{t['direction']} · {t['proposal']['units']} unidades · {show_time(instant(t['entry_at']), zone)}"),
            (exits, "salidas", "x", lambda t: t["result"]["exit_price"], lambda t: f"{t['result']['reason']} · {t['result']['net_pnl']} USD"),
        ):
            figure.add_trace(go.Scatter(x=[i for i, _ in points], y=[float(price(t)) for _, t in points], mode="markers", name=f"{name}: {label}",
                                        text=[text(t) for _, t in points], marker=dict(symbol=symbol, size=13, color=color, line=dict(color="#111", width=1))))
        figure.add_trace(go.Scatter(x=[i for i, _ in rejected], y=[float(interval.bars[i].open) for i, _ in rejected], mode="markers",
                                    name=f"{name}: señales rechazadas", text=[f"{d['direction']} · {' + '.join(d['proposal']['rejections'])}" for _, d in rejected],
                                    marker=dict(symbol="circle-open", size=12, color=color, line=dict(width=2))))
    if interval.bars:
        _time_axis(figure, interval.bars, zone, None)
        _interruptions_plain(figure, interval.bars)
    figure.update_layout(title=f"{interval.symbol} · H1 · {interval.verdict}<br><sup>{SIMULATION_WARNING}</sup>", xaxis_rangeslider_visible=False,
                         template="plotly_white", height=560, legend=dict(orientation="h", yanchor="top", y=-0.15, font=dict(size=10)))
    figure.update_yaxes(tickformat=".5f")
    return figure


def _interruptions_plain(figure: go.Figure, bars: Sequence[PriceBar]) -> None:
    for i in range(len(bars) - 1):
        if bars[i + 1].timestamp - bars[i].timestamp > HOUR:
            figure.add_vline(x=i + 0.5, line_dash="dot", line_color="#9aa0a6", line_width=1)


# --- the run as a whole ---


def drawdown_series(values: Sequence[Decimal]) -> list[Decimal]:
    """For each point, the fall from the highest value so far, as a negative
    percentage of that maximum."""
    out, peak = [], None
    for value in values:
        peak = value if peak is None or value > peak else peak
        out.append((value - peak) / peak * 100 if peak else Decimal(0))
    return out


def equity_figure(run: Run, zone: str = DEFAULT_ZONE) -> go.Figure:
    x = [_local(p["at"], zone) for p in run.equity]
    figure = go.Figure()
    figure.add_trace(go.Scatter(x=x, y=[float(p["balance"]) for p in run.equity], mode="lines", name="Saldo realizado", line=dict(color="#1565c0", width=1.8)))
    figure.add_trace(go.Scatter(x=x, y=[float(p["equity"]) for p in run.equity], mode="lines", name="Equity estimado (marcas horarias)",
                                line=dict(color="#ef6c00", width=1)))
    figure.update_layout(title=f"Curva de capital · {run.key.folder}", template="plotly_white", height=380, yaxis_title="USD", xaxis_title=f"Hora ({zone})")
    return figure


def drawdown_figure(run: Run, zone: str = DEFAULT_ZONE) -> go.Figure:
    x = [_local(p["at"], zone) for p in run.equity]
    figure = go.Figure()
    for column, name, color in (("balance", "Sobre saldo realizado", "#1565c0"), ("equity", "Sobre equity estimado", "#ef6c00")):
        figure.add_trace(go.Scatter(x=x, y=[float(v) for v in drawdown_series([Decimal(p[column]) for p in run.equity])], mode="lines", name=name,
                                    line=dict(color=color, width=1.2), fill="tozeroy" if column == "equity" else None))
    figure.update_layout(title="Drawdown (marcas horarias: puede subestimar el riesgo dentro de la hora)", template="plotly_white", height=300,
                         yaxis_title="% desde el máximo", xaxis_title=f"Hora ({zone})")
    return figure


def monthly_figure(summary: Mapping[str, Any]) -> go.Figure:
    months = list(summary["monthly"])
    figure = go.Figure()
    figure.add_trace(go.Bar(x=months, y=[float(summary["monthly"][m]["net_pnl"]) for m in months], name="Cerradas válidas (USD)",
                            text=[f"n = {summary['monthly'][m]['closed_valid']}" for m in months],
                            marker_color=[COLORS[WON] if Decimal(summary["monthly"][m]["net_pnl"]) >= 0 else COLORS[LOST] for m in months]))
    figure.add_trace(go.Bar(x=months, y=[float(summary["monthly"][m]["indeterminate_conservative"]) for m in months],
                            name="Indeterminadas, lectura conservadora (USD)", marker_color=COLORS[UNKNOWN]))
    figure.update_layout(title="Resultado por mes (mes UTC en que la cuenta registró la operación)", template="plotly_white", height=320, barmode="group")
    return figure


def distribution_figure(summary: Mapping[str, Any], scope: str = "TOTAL") -> go.Figure:
    buckets = summary["closed"][scope]["r_distribution"]
    figure = go.Figure(go.Bar(x=list(buckets), y=list(buckets.values()), marker_color="#455a64"))
    figure.update_layout(title=f"Distribución de resultados en R · operaciones cerradas válidas (n = {summary['closed'][scope]['n']})", template="plotly_white",
                         height=300, yaxis_title="operaciones")
    return figure


def exits_figure(summary: Mapping[str, Any], scope: str = "TOTAL") -> go.Figure:
    exits = summary["closed"][scope]["exits"]
    figure = go.Figure(go.Bar(x=list(exits), y=list(exits.values()), marker_color=[COLORS[WON] if k == "OBJETIVO" else COLORS[LOST] if k == "STOP" else "#5f6b7a"
                                                                                 for k in exits]))
    figure.update_layout(title="Salidas por motivo · operaciones cerradas válidas", template="plotly_white", height=300, yaxis_title="operaciones")
    return figure
