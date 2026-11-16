"""One self-contained HTML file with every trade of a verified study, for a
reviewer who has neither Python nor this repository: summary, searchable
table, a candle chart per trade with the recorded indicators, a replay
that hides the future, and CSV export. Standard library only - no Plotly,
no Streamlit, nothing downloaded: the page (offline_page.py) draws on a
canvas with its own JavaScript and carries its data inside.

Nothing is recomputed here. The study is loaded through study_store, which
checks the manifest, the snapshot and each run against their hashes; the
trades are the engine's records; the indicators are the values the
strategy wrote in signals.csv; the candles are the snapshot's bars. Before
writing anything this module also checks that the trades it is about to
show add up to the engine's own summary (count, wins, losses, net result),
and refuses to build the report otherwise.

A trade counts in the main figures only if it is a valid closed trade, as
in the engine's metrics: closed, with a result, not indeterminate. Any
other (open at the end, indeterminate) is kept, shown apart and never
counted.

What is rounded, and only for drawing: indicator lines carry six decimals
(RSI two). Candles keep every digit of the snapshot. Every figure of a
trade is the engine's own text.

Nothing identifies the machine: no path, no user, no environment, and the
manifest's execution block (operating system, Python) is left out.
"""

from __future__ import annotations

import csv
import io
import json
from decimal import Decimal
from typing import Any, Mapping, Sequence

from investment_committee.simulation import ema_cross, ema_pullback, ema_trend_cross

from .offline_page import PAGE
from .study_store import Run, RunKey, Signals, Study, StudyError, check_signals, load_run, load_signals, number
from .trade_view import HOUR, SIMULATION_WARNING, UTC, TradeView, _series, build_trade_view, indicator_columns, lots

PERIOD, SCENARIO = "COMUN", "BASE"
HTML_NAME = "investment_committee_trades.html"
SIX, TWO = Decimal("0.000001"), Decimal("0.01")

# (CSV header, key of the trade as embedded in the page). The page's own export uses the same list.
CSV_COLUMNS: tuple[tuple[str, str], ...] = (
    ("ID", "id"), ("Orden", "order"), ("Estrategia", "strategy"), ("Etiqueta", "label"), ("Escenario", "scenario"), ("Período", "period"),
    ("Par", "sym"), ("Dirección", "dir"), ("Estado", "status"), ("Resultado", "outcome"), ("Cierre de la vela de señal (UTC)", "sig"),
    ("Entrada (UTC)", "t_in"), ("Salida (UTC)", "t_out"), ("Duración (h)", "hours"), ("Velas observadas", "bars_held"), ("Entrada", "entry"),
    ("Stop loss", "stop"), ("Take profit", "target"), ("Salida", "exit"), ("SL (pips)", "sl_pips"), ("TP (pips)", "tp_pips"), ("Unidades", "units"),
    ("Lotes", "lots"), ("Capital antes (USD)", "equity"), ("Riesgo presupuestado (USD)", "budget"), ("Pérdida estimada en el stop (USD)", "est_loss"),
    ("Riesgo (% capital)", "risk_pct"), ("Resultado (USD)", "pnl"), ("Resultado (R)", "r"), ("Motivo de salida", "reason"), ("Gap", "gap"),
    ("Noches", "nights"), ("Comisión (USD)", "commission"), ("Financiación (USD)", "financing"), ("ATR14 en la señal", "atr"),
    ("Fuente de datos", "data_kind"), ("Hash del estudio", "study_sha256"), ("Advertencia", "warning"),
)

RULES: Mapping[str, Mapping[str, Any]] = {
    ema_cross.STRATEGY_ID: {
        "title": "Estrategia A · cruce EMA20/EMA50 confirmado por RSI",
        "rules": (
            "Velas H1 cerradas de EUR/USD y GBP/USD (precio medio indicativo de Yahoo).",
            "LONG: la EMA20 cruza por encima de la EMA50 en la vela que acaba de cerrar (EMA20 ≤ EMA50 en la anterior, EMA20 > EMA50 en esta) y RSI14 > 50.",
            "SHORT: la EMA20 cruza por debajo de la EMA50 y RSI14 < 50. Un RSI de exactamente 50 no confirma ningún lado.",
            "Un cruce sin confirmación no se confirma después con otra vela.",
            "Ventana de 300 horas esperadas; se rechaza la evaluación si faltan más de 3 velas en la ventana o alguna entre las últimas 50.",
        ),
    },
    ema_trend_cross.STRATEGY_ID: {
        "title": "Estrategia B · cruce EMA3/EMA9 a favor de la tendencia EMA20/50/120",
        "rules": (
            "Velas H1 cerradas de EUR/USD y GBP/USD (precio medio indicativo de Yahoo).",
            "LONG: la EMA3 cruza por encima de la EMA9, con EMA20 > EMA50 > EMA120 (orden estricto) y RSI14 > 50.",
            "SHORT: la EMA3 cruza por debajo de la EMA9, con EMA20 < EMA50 < EMA120 y RSI14 < 50.",
            "Un cruce que falla la tendencia o el RSI no se confirma después con otra vela.",
            "Ventana de 720 horas esperadas; se rechaza la evaluación si faltan más de 7 velas en la ventana o alguna entre las últimas 50.",
        ),
    },
    ema_pullback.STRATEGY_ID: {
        "title": "Estrategia C · entrada al terminar un retroceso dentro de una tendencia EMA20/50/120",
        "rules": (
            "Velas H1 cerradas de EUR/USD y GBP/USD (precio medio indicativo de Yahoo).",
            "LONG: tendencia EMA20 > EMA50 > EMA120 (orden estricto) en la vela que acaba de cerrar.",
            "Retroceso: una racha de 1 a 12 velas que cerraron en o por debajo de su EMA9, de las que al menos una tocó la EMA20 (mínimo ≤ EMA20), "
            "con la tendencia intacta en todas y sin ningún cierre por debajo de la EMA50.",
            "Confirmación: la primera vela que cierra de nuevo por encima de la EMA9 y de la EMA20, y que es alcista (cierre > apertura).",
            "SHORT: las mismas reglas invertidas (máximo ≥ EMA20, cierres en o por encima de la EMA9, vela bajista).",
            "El retroceso debe haberse observado completo: sin huecos de datos ni cierre de mercado dentro. Un retroceso que falla una condición no da señal.",
            "Ventana de 720 horas esperadas; se rechaza la evaluación si faltan más de 7 velas en la ventana o alguna entre las últimas 50.",
        ),
        "shared": (
            "Entrada: apertura de la vela siguiente a la de la señal, como cotización ESTIMADA (bid/ask = precio de Yahoo ∓ medio spread asumido). "
            "Si esa apertura se alejó más de 0,25 ATR del cierre de la señal, no se entra.",
            "Stop loss estructural: el extremo del retroceso (mínimo más bajo para LONG, máximo más alto para SHORT) más un margen de 0,10 × ATR14. "
            "Si queda a menos de 1,0 × ATR de la entrada se lleva a 1,0 × ATR; si queda a más de 3,0 × ATR, no se opera.",
            "Take profit: el menor nivel que deja al menos 2 R netos de los costos estimados.",
            "Tamaño: 0,5 % del capital en riesgo, en pasos de 1 000 unidades. Cuenta hipotética de 10 000 USD, apalancamiento 30:1.",
            "Límites: una posición por par; pérdida diaria máxima 1,5 %; riesgo en el mismo lado del dólar 1 %; margen usado hasta 75 % del capital.",
            "Sin entradas con el mercado cerrado, los viernes desde las 12:00 de Nueva York ni entre las 16:00 y las 18:00 de Nueva York.",
            "Salidas: stop, objetivo, 72 velas de mercado abierto (TIEMPO) o viernes 16:00 de Nueva York (CIERRE_SEMANAL). Sin trailing ni salidas parciales.",
            "Stop y objetivo en la misma vela, o una hora esperada sin vela con la posición abierta: la operación queda INDETERMINADA y no cuenta.",
            "Costos del escenario BASE: spread asumido 1,0 pip (EUR/USD) y 1,5 pips (GBP/USD), slippage 0,2 y 0,3 pips por ejecución, comisión 0, "
            "financiación no verificada (0). COSTOS_X1_5 multiplica spread y slippage por 1,5. Todos son supuestos, no costos observados.",
        ),
        "limitations": (
            "Los precios son el precio medio indicativo único de Yahoo Finance (H1), sin bid/ask real. Toda ejecución es ESTIMADA: no son operaciones reales.",
            "Prueba retrospectiva registrada de antemano (docs/research/ema-pullback-forex-0.1.0.md) sobre D2, velas desde el 14 de julio de 2025 hasta "
            "el 20 de marzo de 2026, leído una sola vez. No es una validación independiente ni prospectiva: misma fuente y mismos años que los datos de "
            "desarrollo, y la idea salió de datos posteriores.",
            "El registro exige al menos 200 operaciones cerradas válidas para cualquier veredicto. Con menos, el resultado es NO CONCLUYENTE sea cual sea, "
            "y ninguna cifra de este reporte es evidencia a favor ni en contra de la estrategia.",
            "Los criterios registrados se juzgan con costos × 1,5. Este archivo muestra el escenario indicado en el encabezado.",
            "Enero y febrero de 2026 no tienen ninguna hora evaluable y marzo de 2026 solo unas decenas: faltan velas en los datos de Yahoo (festivos de "
            "Navidad y Año Nuevo, y del 30 de enero al 2 de febrero) y la regla de datos de la estrategia rechaza las horas posteriores a un hueco.",
            "Las velas anteriores al 14 de julio de 2025 se muestran solo como contexto: sirvieron para calcular los indicadores, no para operar.",
        ),
    },
}
SHARED_RULES = (
    "Entrada: apertura de la vela siguiente a la de la señal, como cotización ESTIMADA (bid/ask = precio de Yahoo ∓ medio spread asumido). "
    "Si esa apertura se alejó más de 0,25 ATR del cierre de la señal, no se entra.",
    "Stop loss: a 1,5 × ATR14 de la entrada. Take profit: el menor nivel que deja al menos 3 R netos de los costos estimados.",
    "Tamaño: 0,5 % del capital en riesgo, en pasos de 1 000 unidades. Cuenta hipotética de 10 000 USD por estrategia, apalancamiento 30:1.",
    "Límites: una posición por par; pérdida diaria máxima 1,5 %; riesgo en el mismo lado del dólar 1 %; margen usado hasta 75 % del capital.",
    "Sin entradas con el mercado cerrado, los viernes desde las 12:00 de Nueva York ni entre las 16:00 y las 18:00 de Nueva York.",
    "Salidas: stop, objetivo, 72 velas de mercado abierto (TIEMPO) o viernes 16:00 de Nueva York (CIERRE_SEMANAL). Sin trailing ni salidas parciales.",
    "Stop y objetivo en la misma vela, o una hora esperada sin vela con la posición abierta: la operación queda INDETERMINADA y no cuenta.",
    "Costos del escenario BASE: spread asumido 1,0 pip (EUR/USD) y 1,5 pips (GBP/USD), slippage 0,2 y 0,3 pips por ejecución, comisión 0, "
    "financiación no verificada (0). Todos son supuestos, no costos observados.",
)
COMMON_LIMITATIONS = (
    "Dentro de una vela de una hora no se conoce el orden de los precios: una salida 'dentro de la vela' se fecha al cierre de esa vela.",
    "El stop y el objetivo se comparan con el lado de salida (precio de la vela ∓ medio spread); sobre las velas de precio medio el nivel parece "
    "activarse medio spread antes o después. El gráfico dibuja los niveles registrados.",
    "Los indicadores son los valores que la estrategia registró al evaluar cada vela; donde no evaluó (datos incompletos, mercado cerrado) "
    "la línea se interrumpe. No se interpola nada.",
    "El eje de tiempo omite las horas sin vela (fines de semana y huecos de datos): una línea vertical punteada marca cada salto.",
    "Las cifras de la selección filtrada se suman en el navegador a partir de los resultados registrados; las del resumen general son las del motor.",
)
LIMITATIONS = (
    "Los precios son el precio medio indicativo único de Yahoo Finance (H1), sin bid/ask real. Toda ejecución es ESTIMADA: no son operaciones reales.",
    "El período (marzo a septiembre de 2026) ya fue inspeccionado muchas veces: no es una validación independiente ni prospectiva.",
    "Las dos estrategias pierden en todos los escenarios de costos del estudio. Este reporte muestra solo el escenario BASE del período común.",
    "Dentro de una vela de una hora no se conoce el orden de los precios: una salida 'dentro de la vela' se fecha al cierre de esa vela.",
    "El stop y el objetivo se comparan con el lado de salida (precio de la vela ∓ medio spread); sobre las velas de precio medio el nivel parece "
    "activarse medio spread antes o después. El gráfico dibuja los niveles registrados.",
    "Los indicadores son los valores que la estrategia registró al evaluar cada vela; donde no evaluó (datos incompletos, mercado cerrado) "
    "la línea se interrumpe. No se interpola nada.",
    "El eje de tiempo omite las horas sin vela (fines de semana y huecos de datos): una línea vertical punteada marca cada salto.",
    "Las cifras de la selección filtrada se suman en el navegador a partir de los resultados registrados; las del resumen general son las del motor.",
)


SCENARIO_NAMES = {"BASE": "escenario base", "COSTOS_X1_5": "costos altos (× 1,5)", "COSTOS_X2": "costos × 2"}
MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def day(moment: str) -> str:
    """'2026-05-06T06:00:00+00:00' -> '6 may 2026' (the UTC date)."""
    year, month, number = moment[:10].split("-")
    return f"{int(number)} {MONTHS[int(month) - 1]} {year}"


# signals.csv column -> name, for what a strategy records about its signal besides indicators (strategy C's pullback)
SIGNAL_EXTRAS = (("leg_bars", "Velas del retroceso"), ("leg_start", "Inicio del retroceso (UTC)"), ("pullback_extreme", "Extremo del retroceso"))


def _text(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)


def _plain(value: Decimal | None, step: Decimal) -> float | None:
    return None if value is None else float(value.quantize(step))


def _exact(value: Decimal) -> float:
    """A snapshot price as a JSON number that parses back to the same digits."""
    as_float = float(value)
    if Decimal(repr(as_float)) != value:
        raise StudyError(f"el precio {value} del snapshot no se puede escribir sin perder dígitos")
    return as_float


def is_valid_closed(trade: Mapping[str, Any]) -> bool:
    """The engine's 'valid closed trade': closed, with a result, not indeterminate."""
    return trade["status"] == "CERRADA" and trade["result"] is not None and trade["indeterminate"] is None


def _trade(view: TradeView, first_bar: int, label: str, study: Study) -> dict[str, Any]:
    t, p, result = view.trade, view.trade["proposal"], view.trade["result"]
    equity, loss = number(p["equity"]), number(p["estimated_loss_at_stop"])
    assert equity is not None and loss is not None

    def bar(index: int | None) -> int | None:
        return None if index is None else index + first_bar

    hours = None if view.exit_at is None else format(Decimal((view.exit_at - view.entry_at) // HOUR), "f")
    return {
        "id": view.trade_id, "order": t["order"], "strategy": t["strategy_id"], "label": label, "scenario": view.key.scenario, "period": view.key.period,
        "sym": view.symbol, "dir": view.direction, "status": view.status, "outcome": view.outcome, "valid": is_valid_closed(t),
        "sig": view.signal_candle_close_at.isoformat(), "t_in": view.entry_at.isoformat(), "t_out": view.exit_at.isoformat() if view.exit_at else None,
        "hours": hours, "bars_held": t["bars_held"], "i_sig": bar(view.signal_index), "i_in": bar(view.entry_index), "i_out": bar(view.exit_index),
        "i_last": bar(view.last_index), "timing": view.exit_timing, "entry": str(view.entry), "stop": str(view.stop), "target": str(view.target),
        "exit": _text(view.exit_price), "sl_pips": format(view.stop_pips, "f"), "tp_pips": format(view.target_pips, "f"), "units": view.units,
        "lots": format(lots(view.units), "f"), "notional": p["notional"], "margin": p["margin_required"], "equity": p["equity"],
        "budget": p["risk_budget"], "est_loss": p["estimated_loss_at_stop"], "risk_pct": format((loss / equity * 100).quantize(Decimal("0.0001")), "f"),
        "pnl": _text(view.net_pnl), "r": _text(view.r_multiple), "reason": view.exit_reason, "gap": bool(result and result["gap"]),
        "nights": result["nights"] if result else None, "commission": result["commission_cost"] if result else None,
        "financing": result["financing_cost"] if result else None, "fin_status": p["financing_status"],
        "atr": format(number(p["atr"]).quantize(SIX), "f"), "bid": format(number(p["bid"]).quantize(SIX), "f"),  # type: ignore[union-attr]
        "ask": format(number(p["ask"]).quantize(SIX), "f"), "spread": format(number(p["spread"]).normalize(), "f"),  # type: ignore[union-attr]
        "slippage": p["slippage"], "rr_gross": format(view.reward_risk_gross.quantize(TWO), "f"), "rr_net": _text(p["net_reward_risk_before_financing"]),
        "exp_profit": p["expected_net_profit"], "flags": list(p["flags"]), "sig_reasons": view.signal_row.get("reasons", ""),
        "extra": [[name, view.signal_row[column]] for column, name in SIGNAL_EXTRAS if view.signal_row.get(column)],
        "unrealized": _text(t["unrealized_pnl"]), "data_kind": view.data_kind, "study_sha256": study.study_sha256, "warning": SIMULATION_WARNING,
    }


def _check_against_summary(run: Run, trades: Sequence[Mapping[str, Any]]) -> None:
    """The trades about to be shown must be the ones the engine counted."""
    total, valid = run.summary["closed"]["TOTAL"], [t for t in trades if t["valid"]]
    funnel = run.summary["funnel"]["TOTAL"]
    net = sum((Decimal(t["pnl"]) for t in valid), Decimal(0))
    found = (len(valid), sum(Decimal(t["pnl"]) > 0 for t in valid), sum(Decimal(t["pnl"]) < 0 for t in valid), net, len(trades))
    expected = (total["n"], total["wins"], total["losses"], Decimal(total["net_pnl"]), funnel["opened"])
    if found != expected or total["n"] != funnel["closed_valid"]:
        raise StudyError(f"las operaciones de {run.key.folder} no coinciden con el resumen del motor: {found} frente a {expected}")


def _strategy(study: Study, strategy_id: str, label: str, period: str, scenario: str) -> dict[str, Any]:
    key = RunKey(strategy_id, period, scenario)
    run, signals = load_run(study, key), load_signals(study, strategy_id)
    check_signals(run, signals)
    positions = {symbol: {bar.timestamp: index for index, bar in enumerate(series)} for symbol, series in study.bars.items()}
    trades = []
    for record in run.trades:
        view = build_trade_view(study, run, signals, record["proposal"]["proposal_id"], "UTC")
        trades.append(_trade(view, positions[view.symbol][view.entry_at] - view.entry_index, label, study))
    _check_against_summary(run, trades)
    return {
        "id": strategy_id, "label": label, "title": RULES.get(strategy_id, {}).get("title", strategy_id), "rules": list(RULES.get(strategy_id, {}).get("rules", ())),
        "parameters_sha256": run.data["parameters_sha256"], "results_sha256": study.manifest["runs"][key.folder]["results_sha256"],
        "indicators": _indicators(study, run, signals), "trades": trades, "summary": _summary(run),
        "execution_parameters": run.record["execution_parameters"], "risk_limits": run.record["risk_limits"],
    }


def _indicators(study: Study, run: Run, signals: Signals) -> dict[str, dict[str, list[float | None]]]:
    """symbol -> name -> one recorded value per bar of the snapshot (None where the strategy did not evaluate)."""
    out: dict[str, dict[str, list[float | None]]] = {}
    for symbol, bars in study.bars.items():
        rows = signals.get(symbol, {})
        columns = list(next(iter(rows.values()), {}))
        names = indicator_columns(run.data["strategy_id"], run.data["parameters_sha256"], columns)
        lines = {name: [_plain(v, SIX) for v in _series(bars, rows, column)] for column, name in names.items()}
        if "rsi" in columns:
            lines["RSI14"] = [_plain(v, TWO) for v in _series(bars, rows, "rsi")]
        if "atr" in columns:
            lines["ATR14"] = [_plain(v, SIX) for v in _series(bars, rows, "atr")]
        out[symbol] = lines
    return out


def _summary(run: Run) -> dict[str, Any]:
    s = run.summary
    kept = ("n", "wins", "losses", "flat", "win_rate", "win_rate_interval_95", "expectancy_r", "profit_factor", "net_pnl", "gross_profit", "gross_loss",
            "average_win", "average_loss", "average_win_r", "average_loss_r", "average_hours", "exits", "gaps", "sample")
    return {
        "closed": {scope: {name: figures.get(name) for name in kept} for scope, figures in s["closed"].items()},
        "funnel": {name: s["funnel"]["TOTAL"][name] for name in ("signals", "opened", "closed_valid", "indeterminate", "open_at_end", "rejected")},
        "drawdown_balance": s["drawdown_balance"], "drawdown_equity": s["drawdown_equity"], "initial_capital": s["initial_capital"],
        # the account: what it booked for indeterminate trades (outside the valid closed trades) and what is still open
        "indeterminate_conservative": s["indeterminate_conservative"], "indeterminate_optimistic": s["indeterminate_optimistic"],
        "indeterminate_policy": s["indeterminate_policy"], "final_equity": s["final_equity"], "open_unrealized": s["open_unrealized"],
        "final_balance": s["final_balance"], "return_fraction": s["return_fraction"], "monthly": s["monthly"], "warnings": list(s["warnings"]),
        "start": s["start"], "end": s["end"],
    }


def _labels(strategies: Sequence[str]) -> dict[str, str]:
    fixed = {ema_cross.STRATEGY_ID: "A", ema_trend_cross.STRATEGY_ID: "B", ema_pullback.STRATEGY_ID: "C"}
    known = [s for s in fixed if s in strategies]
    free = (letter for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if letter not in {fixed[s] for s in known})
    return {**{s: fixed[s] for s in known}, **{s: next(free) for s in sorted(strategies) if s not in known}}


def _notes(strategies: Sequence[str]) -> tuple[list[str], list[str]]:
    """(shared rules, limitations). A strategy shown alone may bring its own; otherwise those of the A/B study."""
    own = RULES.get(strategies[0], {}) if len(strategies) == 1 else {}
    return list(own.get("shared", SHARED_RULES)), [*own["limitations"], *COMMON_LIMITATIONS] if "limitations" in own else list(LIMITATIONS)


def build_payload(study: Study, *, period: str = PERIOD, scenario: str = SCENARIO, only: Sequence[str] | None = None,
                  index_href: str | None = None) -> dict[str, Any]:
    """Everything the page shows, from the verified study. `only` keeps some
    of the study's strategies (each keeps its letter); `index_href` is a
    relative link back to an index page that travels with the report."""
    m = study.manifest
    labels = _labels(study.strategies)
    if only is not None:
        if not only or any(name not in labels for name in only):
            raise StudyError(f"el estudio no contiene las estrategias pedidas: {list(only)}")
        labels = {name: label for name, label in labels.items() if name in only}
    shared, limitations = _notes(list(labels))
    names = list(labels.values())
    title = "Investment Committee · operaciones simuladas de " + (
        "la estrategia " + names[0] if len(names) == 1 else "las estrategias " + ", ".join(names[:-1]) + " y " + names[-1])
    bars = {symbol: {"t": [int(b.timestamp.astimezone(UTC).timestamp()) for b in series], "o": [_exact(b.open) for b in series],
                     "h": [_exact(b.high) for b in series], "l": [_exact(b.low) for b in series], "c": [_exact(b.close) for b in series]}
            for symbol, series in study.bars.items()}
    strategies = [_strategy(study, strategy, label, period, scenario) for strategy, label in labels.items()]
    counts = [f"{s['summary']['closed']['TOTAL']['n']} operaciones cerradas válidas" + ("" if len(strategies) == 1 else f" de {s['label']}")
              for s in strategies]
    headline = strategies[0]["title"] if len(strategies) == 1 else "Estrategias " + ", ".join(names[:-1]) + " y " + names[-1]
    subhead = (f"{day(strategies[0]['summary']['start'])} a {day(strategies[0]['summary']['end'])} · "
               f"{SCENARIO_NAMES.get(scenario, scenario)} · {' · '.join(counts)}")
    return {
        "meta": {
            "headline": headline, "subhead": subhead, "index_href": index_href,
            "study_name": study.name, "study_sha256": study.study_sha256, "dataset_sha256": m["dataset_sha256"],
            "dataset_description": m.get("dataset_description", ""), "data_source": m.get("data_source", ""), "data_kind": m["data_kind"],
            "engine": m.get("engine", ""), "commit": m.get("commit", ""), "calendar": m.get("calendar", ""), "common_start": m.get("common_start", ""),
            "end": m.get("end", ""), "period": period, "scenario": scenario, "warning": SIMULATION_WARNING, "title": title,
            "commission_convention": m.get("commission_convention", ""),
        },
        "shared_rules": shared, "limitations": limitations, "csv_columns": [list(column) for column in CSV_COLUMNS],
        "bars": bars, "strategies": strategies,
    }


def report_html(payload: Mapping[str, Any]) -> str:
    """The page with its data inside. `<` is escaped so that no text of the data can close the script block."""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c")
    if PAGE.count("__DATA__") != 1:
        raise StudyError("la plantilla del reporte no tiene exactamente un lugar para los datos")
    return PAGE.replace("__DATA__", data)


def trades_csv(strategy: Mapping[str, Any], *, valid: bool = True) -> str:
    """The valid closed trades of one strategy (or, with valid=False, the ones left out of the main count)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([header for header, _ in CSV_COLUMNS])
    for trade in strategy["trades"]:
        if trade["valid"] is valid:
            writer.writerow(["" if trade[name] is None else ("SI" if trade[name] is True else "NO" if trade[name] is False else trade[name])
                             for _, name in CSV_COLUMNS])
    return out.getvalue()


def build_files(study: Study, *, period: str = PERIOD, scenario: str = SCENARIO) -> dict[str, str]:
    """file name -> text: the report and one CSV per strategy, plus one CSV of the trades left out of the count, if any."""
    payload = build_payload(study, period=period, scenario=scenario)
    files = {HTML_NAME: report_html(payload)}
    left_out = []
    for strategy in payload["strategies"]:
        files[f"strategy_{strategy['label']}_trades.csv"] = trades_csv(strategy)
        left_out.extend(trade for trade in strategy["trades"] if not trade["valid"])
    if left_out:
        files["excluded_trades.csv"] = trades_csv({"trades": left_out}, valid=False)
    return files
