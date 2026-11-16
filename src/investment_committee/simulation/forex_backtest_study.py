"""The fixed set of backtest runs that compares the experimental Forex
strategies, and the files it leaves for audit and for the trade viewer.

EXPERIMENTAL, ESTIMATED execution model (see forex_backtest.py). Nothing
here chooses a winner or tunes anything: the scenarios are decided before
any data is read.

run_study(bars, ...)
  1. one signal pass per strategy over the whole dataset, from the first
     bar's close to the last bar's close;
  2. for every strategy, two periods:
       COMPLETO  from the first instant the strategy can evaluate;
       COMUN     from the latest of the strategies' first evaluable
                 instants, with a FRESH account - a calendar window from
                 which no hour is removed;
  3. for every period, every scenario of SCENARIOS, each a complete run
     with its own account.

Scenarios (all costs are assumptions):
  BASE                                   spread and slippage of
                                         trade_proposal.InstrumentRules,
                                         commission 0, no financing rate
  COSTOS_X1_5, COSTOS_X2                 spread, slippage and commission scaled
  FINANCIACION_ILUSTRATIVA_3_POR_CIENTO  3 % a year, illustrative
  COMISION_HIPOTETICA_7_USD_POR_100K     see below
  INDETERMINADAS_OPTIMISTAS              the account books the optimistic
                                         result of indeterminate trades

Commission convention: USD per 100 000 units of base currency for the WHOLE
round trip (entry and exit together), charged once when the trade is
settled - not per side. 7 USD per 100 000 is therefore 7 USD per standard
lot opened and closed (the same as 3.50 per side). It is made up.

yahoo_week: the calendar observed in the Yahoo snapshot. New York's FX week
runs Sunday 17:00 to Friday 17:00 New York time; Yahoo's hourly bars begin
two hours later, Sunday 19:00, and end at the same Friday 17:00. With the
New York calendar those two Sunday hours would count as missing every
week. Session rules (entry blocks, weekly close, financing nights) are New
York's in every case; the calendar only says which hours must have a bar.

write_study(directory, ...) writes, with the same bytes on every system:
  manifest.json                 commit, dataset hash, strategies, scenarios,
                                calendar, one results hash per run
  summary.csv                   one row per run and scope (pair or TOTAL)
  <strategy>/signals.csv        every evaluation, with its indicator values
  <strategy>/<period>/<scenario>/
      record.json run.json report.txt trades.csv decisions.csv equity.csv
It never writes into an existing folder: results are not rewritten.
"""

from __future__ import annotations

import csv
import io
import json
import platform
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from investment_committee.domain import PriceBar
from investment_committee.market_intelligence.quality import Calendar
from investment_committee.market_intelligence.quotes import QuoteKind, to_utc

from .forex_backtest import (
    BASE,
    COSTS_X1_5,
    COSTS_X2,
    ENGINE_ID,
    ILLUSTRATIVE_FINANCING,
    OPTIMISTIC,
    STRATEGY_A,
    STRATEGY_B,
    BacktestRun,
    Scenario,
    Strategy,
    Trade,
    evaluate_signals,
    hypothetical_commission,
    run_account,
)
from .forex_backtest_metrics import _sha256, canonical, common_start, content_sha256, first_evaluable_at, record, render, summarize
from .sessions import is_week_open
from .signals import TradeSignal
from .trade_proposal import instrument_for

HOUR = timedelta(hours=1)
FULL, COMMON = "COMPLETO", "COMUN"
SCENARIOS: tuple[Scenario, ...] = (BASE, COSTS_X1_5, COSTS_X2, ILLUSTRATIVE_FINANCING, hypothetical_commission(Decimal("7")), OPTIMISTIC)
COMMISSION_CONVENTION = ("USD por 100 000 unidades, ida y vuelta: se cobra una sola vez por operación completa (entrada y salida juntas), "
                         "no por lado. Es un supuesto, no una comisión verificada.")
YAHOO_CALENDAR = ("calendario observado en Yahoo: la semana empieza el domingo a las 19:00 de Nueva York (dos horas después de la apertura "
                  "de las 17:00) y termina el viernes a las 17:00 de Nueva York. Las reglas de sesión siguen siendo las de Nueva York.")
STANDARD_LOT = Decimal("100000")


def yahoo_week(moment: datetime) -> bool:
    """Whether Yahoo's hourly FX data cover `moment` (see the module text)."""
    return is_week_open(moment) and is_week_open(moment - 2 * HOUR)


@dataclass(frozen=True)
class Study:
    signals: Mapping[str, Mapping[str, Mapping[datetime, TradeSignal]]]  # strategy -> instrument -> instant
    strategies: Mapping[str, Strategy]
    first_evaluable: Mapping[str, datetime]
    common_start: datetime
    end: datetime
    data_kind: QuoteKind
    data_source: str
    runs: Mapping[tuple[str, str, str], BacktestRun]  # (strategy, period, scenario)


def run_study(
    bars: Mapping[str, Sequence[PriceBar]], *, data_kind: QuoteKind, data_source: str, calendar: Calendar = yahoo_week,
    strategies: Sequence[Strategy] = (STRATEGY_A, STRATEGY_B), scenarios: Sequence[Scenario] = SCENARIOS,
) -> Study:
    labels = [to_utc(b.timestamp) for series in bars.values() for b in series]
    if not labels:
        raise ValueError("no bars")
    first, end = min(labels) + HOUR, max(labels) + HOUR
    signals = {s.strategy_id: evaluate_signals(s, bars, start=first, end=end, data_kind=data_kind, data_source=data_source, calendar=calendar)
               for s in strategies}
    starts = {name: first_evaluable_at(evaluations) for name, evaluations in signals.items()}
    together = common_start(*signals.values())
    if together is None:
        raise ValueError("a strategy can never evaluate these bars: there is no common period")
    runs: dict[tuple[str, str, str], BacktestRun] = {}
    for strategy in strategies:
        own = starts[strategy.strategy_id]
        assert own is not None
        for period, start in ((FULL, own), (COMMON, together)):
            for scenario in scenarios:
                runs[(strategy.strategy_id, period, scenario.name)] = run_account(
                    strategy.strategy_id, strategy.parameters_sha256, signals[strategy.strategy_id], bars, start=start, end=end,
                    scenario=scenario, calendar=calendar)
    return Study(signals, {s.strategy_id: s for s in strategies}, {k: v for k, v in starts.items() if v is not None}, together, end,
                 data_kind, data_source, runs)


# --- tables ---


def _fraction(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"))


def _text(value: object) -> str:
    plain = canonical(value)
    return "" if plain is None else str(plain)


def _csv(header: Sequence[str], table: Sequence[Mapping[str, object]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    for row in table:
        writer.writerow([_text(row.get(name)) for name in header])
    return out.getvalue()


TRADE_COLUMNS: tuple[str, ...] = (
    "trade_id", "order", "strategy_id", "scenario", "period", "symbol",
    "direction", "status", "data_kind", "signal_candle_close_at", "quote_available_at", "entry_at",
    "exit_at", "recorded_at", "bars_held", "exit_reason", "gap", "exit_price_source",
    "reference_price", "bid", "ask", "spread", "slippage", "atr",
    "entry", "stop", "target", "exit_price", "stop_pips", "target_pips",
    "planned_reward_risk_gross", "planned_reward_risk_net_before_financing", "planned_reward_risk_net", "equity_before", "units", "lots",
    "notional", "margin_required", "risk_budget", "estimated_loss_at_stop", "risk_fraction_of_equity", "expected_net_profit",
    "gross_pnl", "commission_cost", "financing_status", "nights", "financing_cost", "net_pnl",
    "r_multiple", "pnl_fraction_of_equity", "loss_beyond_estimate", "exceeded_budget", "spread_cost_informative", "entry_slippage_cost_informative",
    "booked_pnl", "conservative_pnl", "conservative_exit_price", "optimistic_pnl", "optimistic_exit_price", "uncertainty_kind",
    "uncertainty_start", "uncertainty_end", "unobserved_hours", "last_price_before", "first_price_after", "valuation_price",
    "valuation_at", "valuation_stale", "unrealized_pnl", "flags",
)


def trade_row(trade: Trade, scenario: str, period: str) -> dict[str, object]:
    """One trade as the engine recorded it, plus plain conversions (pips,
    lots, fractions of equity). Empty where a figure does not exist."""
    p, result, unknown, doubt = trade.proposal, trade.result, trade.indeterminate, trade.uncertainty
    rules = instrument_for(trade.symbol)
    assert rules is not None and p.units is not None and p.equity is not None and p.estimated_loss_at_stop is not None
    return {
        "trade_id": p.proposal_id, "order": trade.order, "strategy_id": trade.strategy_id, "scenario": scenario, "period": period,
        "symbol": trade.symbol, "direction": trade.direction, "status": trade.status, "data_kind": p.data_kind,
        "signal_candle_close_at": trade.signal_candle_close_at, "quote_available_at": trade.quote_available_at, "entry_at": trade.entry_at,
        "exit_at": trade.exit_at, "recorded_at": trade.recorded_at, "bars_held": trade.bars_held,
        "exit_reason": result.reason if result else None, "gap": result.gap if result else None, "exit_price_source": trade.exit_price_source,
        "reference_price": None if p.bid is None or p.ask is None else (p.bid + p.ask) / 2, "bid": p.bid, "ask": p.ask, "spread": p.spread,
        "slippage": p.slippage, "atr": p.atr, "entry": p.entry, "stop": p.stop, "target": p.target, "exit_price": result.exit_price if result else None,
        "stop_pips": p.stop_distance_pips, "target_pips": p.target_distance_pips, "planned_reward_risk_gross": p.gross_reward_risk,
        "planned_reward_risk_net_before_financing": p.net_reward_risk_before_financing, "planned_reward_risk_net": p.net_reward_risk,
        "equity_before": p.equity, "units": p.units, "lots": Decimal(p.units) / STANDARD_LOT, "notional": p.notional, "margin_required": p.margin_required,
        "risk_budget": p.risk_budget, "estimated_loss_at_stop": p.estimated_loss_at_stop,
        "risk_fraction_of_equity": _fraction(p.estimated_loss_at_stop / p.equity), "expected_net_profit": p.expected_net_profit,
        "gross_pnl": result.gross_pnl if result else None, "commission_cost": result.commission_cost if result else None,
        "financing_status": p.financing_status, "nights": result.nights if result else None,
        "financing_cost": result.financing_cost if result else None, "net_pnl": result.net_pnl if result else None,
        "r_multiple": result.r_multiple if result else None,
        "pnl_fraction_of_equity": _fraction(result.net_pnl / p.equity) if result else None,
        "loss_beyond_estimate": result.loss_beyond_estimate if result else None, "exceeded_budget": result.exceeded_budget if result else None,
        "spread_cost_informative": p.spread_cost, "entry_slippage_cost_informative": p.entry_slippage_cost,
        "booked_pnl": trade.booked_pnl,
        "conservative_pnl": unknown.conservative.net_pnl if unknown else None, "conservative_exit_price": unknown.conservative.exit_price if unknown else None,
        "optimistic_pnl": unknown.optimistic.net_pnl if unknown else None, "optimistic_exit_price": unknown.optimistic.exit_price if unknown else None,
        "uncertainty_kind": doubt.kind if doubt else None, "uncertainty_start": doubt.start if doubt else None, "uncertainty_end": doubt.end if doubt else None,
        "unobserved_hours": len(doubt.unobserved_hours) if doubt else None,
        "last_price_before": doubt.last_price_before if doubt else None, "first_price_after": doubt.first_price_after if doubt else None,
        "valuation_price": trade.valuation_price, "valuation_at": trade.valuation_at, "valuation_stale": trade.valuation_stale if trade.valuation_at else None,
        "unrealized_pnl": trade.unrealized_pnl, "flags": " ".join(p.flags),
    }


def trades_csv(run: BacktestRun, period: str) -> str:
    table = [trade_row(t, run.scenario.name, period) for t in run.trades]
    assert all(tuple(row) == TRADE_COLUMNS for row in table)
    return _csv(TRADE_COLUMNS, table)


def decisions_csv(run: BacktestRun) -> str:
    header = ["order", "at", "symbol", "direction", "signal_id", "signal_candle_close_at", "quote_available_at", "status", "rejections", "detail",
              "bid", "ask", "atr", "entry", "stop", "target", "units", "equity", "data_kind"]
    return _csv(header, [{
        "order": d.order, "at": d.at, "symbol": d.symbol, "direction": d.direction, "signal_id": d.signal_id,
        "signal_candle_close_at": d.signal_candle_close_at, "quote_available_at": d.quote_available_at, "status": d.proposal.status,
        "rejections": " ".join(r.value for r in d.proposal.rejections), "detail": d.proposal.detail, "bid": d.proposal.bid, "ask": d.proposal.ask,
        "atr": d.proposal.atr, "entry": d.proposal.entry, "stop": d.proposal.stop, "target": d.proposal.target, "units": d.proposal.units,
        "equity": d.proposal.equity, "data_kind": d.proposal.data_kind} for d in run.decisions])


def equity_csv(run: BacktestRun) -> str:
    return _csv(["at", "balance", "equity", "open_positions", "stale"], [canonical(p) for p in run.equity])


def signals_csv(signals: Mapping[str, Mapping[datetime, TradeSignal]]) -> str:
    """Every evaluation of a strategy, one row per instrument and instant,
    with whatever the evaluation carries (its indicator values included)."""
    table = [{"at": moment, **canonical(signal)} for symbol in sorted(signals) for moment, signal in sorted(signals[symbol].items())]
    names = sorted({name for row in table for name in row} - {"at", "symbol"})
    return _csv(["at", "symbol", *names], [{k: (" ".join(map(str, v)) if isinstance(v, list) else v) for k, v in row.items()} for row in table])


def summary_rows(key: tuple[str, str, str], run: BacktestRun) -> list[dict[str, object]]:
    summary, table = summarize(run), []
    for scope, funnel in summary.funnel.items():
        stats, account = summary.closed[scope], scope == "TOTAL"
        low, high = stats.win_rate_interval_95 or (None, None)
        table.append({
            "strategy_id": key[0], "period": key[1], "scenario": key[2], "scope": scope, "start": run.start, "end": run.end,
            "signals": funnel.signals, "rejected": funnel.rejected, "rejected_position_open": funnel.rejected_position_open, "opened": funnel.opened,
            "closed_valid": funnel.closed_valid, "indeterminate": funnel.indeterminate, "open_at_end": funnel.open_at_end,
            "n": stats.n, "sample": stats.sample, "wins": stats.wins, "losses": stats.losses, "win_rate": stats.win_rate,
            "wilson_95_low": low, "wilson_95_high": high, "expectancy_r": stats.expectancy_r, "profit_factor": stats.profit_factor,
            "net_pnl": stats.net_pnl, "average_win": stats.average_win, "average_loss": stats.average_loss, "average_hours": stats.average_hours,
            **{f"exits_{name}": count for name, count in stats.exits.items()}, "gaps": stats.gaps,
            **{f"cost_{name}": amount for name, amount in stats.costs.items()},
            **{f"best_{k}_share_of_wins": share for k, share in stats.concentration.items()},
            "indeterminate_conservative": summary.indeterminate_conservative if account else None,
            "indeterminate_optimistic": summary.indeterminate_optimistic if account else None,
            "conservative_pnl": summary.conservative_pnl if account else None, "optimistic_pnl_same_trades": summary.optimistic_pnl if account else None,
            "final_balance": summary.final_balance if account else None, "return_fraction": summary.return_fraction if account else None,
            "drawdown_balance_fraction": summary.drawdown_balance.fraction if account else None,
            "drawdown_equity_fraction": summary.drawdown_equity.fraction if account else None,
            "stale_valuations": summary.stale_valuations if account else None,
        })
    return table


def write_study(
    directory: Path, study: Study, *, commit: str, dataset: str, dataset_description: str, calendar_description: str, executed_at: datetime,
) -> dict[str, Any]:
    """Write the study under `directory` (which must not exist) and return
    its manifest. No path of this machine is written anywhere."""
    if directory.exists():
        raise FileExistsError(f"{directory.name} ya existe: los resultados de un estudio no se reescriben")
    files: dict[str, str] = {}
    runs: dict[str, Any] = {}
    table: list[dict[str, object]] = []
    for name, signals in study.signals.items():
        files[f"{name}/signals.csv"] = signals_csv(signals)
    for key, run in study.runs.items():
        folder = "/".join(key)
        entry = record(run, commit=commit, dataset=dataset, dataset_description=dataset_description, executed_at=executed_at)
        files[f"{folder}/record.json"] = json.dumps(entry, sort_keys=True, indent=1, ensure_ascii=False) + "\n"
        files[f"{folder}/run.json"] = json.dumps(canonical(run), sort_keys=True, indent=1, ensure_ascii=False) + "\n"
        files[f"{folder}/report.txt"] = render(summarize(run))
        files[f"{folder}/trades.csv"] = trades_csv(run, key[1])
        files[f"{folder}/decisions.csv"] = decisions_csv(run)
        files[f"{folder}/equity.csv"] = equity_csv(run)
        runs[folder] = {"results_sha256": content_sha256(run), "reproducible_sha256": entry["reproducible_sha256"], "start": canonical(run.start),
                        "trades": len(run.trades), "final_balance": canonical(run.final_balance)}
        table += summary_rows(key, run)
    files["summary.csv"] = _csv(list(table[0]), table)
    reproducible = {
        "engine": ENGINE_ID, "commit": commit, "dataset_sha256": dataset, "dataset_description": dataset_description,
        "data_kind": study.data_kind.value, "data_source": study.data_source, "calendar": calendar_description,
        "strategies": {name: {"parameters_sha256": s.parameters_sha256, "first_evaluable_at": canonical(study.first_evaluable[name])}
                       for name, s in study.strategies.items()},
        "common_start": canonical(study.common_start), "end": canonical(study.end),
        "periods": {FULL: "desde la primera hora evaluable de cada estrategia", COMMON: "desde el inicio común, con una cuenta nueva"},
        "scenarios": [canonical(s) for s in dict.fromkeys(run.scenario for run in study.runs.values())],
        "commission_convention": COMMISSION_CONVENTION, "runs": runs,
        "warning": "Simulación retrospectiva con modelo de ejecución ESTIMADO; ningún resultado es una rentabilidad neta real ni elige una ganadora.",
    }
    manifest = {**reproducible, "study_sha256": _sha256(reproducible),
                "execution": {"executed_at": canonical(executed_at), "operating_system": platform.platform(), "python": platform.python_version(),
                              "note": "metadatos no determinantes: no entran en ningún hash"}}
    files["manifest.json"] = json.dumps(manifest, sort_keys=True, indent=1, ensure_ascii=False) + "\n"
    for name, text in files.items():
        path = directory.joinpath(*name.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return manifest
