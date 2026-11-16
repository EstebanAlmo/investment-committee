"""Metrics, report and reproducible record of a forex_backtest run.

Pure functions of a BacktestRun: nothing here walks prices or decides a
trade. Every figure describes a simulation under an ESTIMATED execution
model - never a real net return.

Three groups that are never mixed:
  valid closed trades   CERRADA: left by stop, target, time or weekly close
                        at a determined price. Win rate, expectancy, profit
                        factor, averages, durations and costs use ONLY these.
  indeterminate trades  counted apart, with the sum of their conservative
                        and of their optimistic results.
  open at the end       counted apart; no result.
The account (balance, return, drawdown) is what the engine booked: valid
trades plus the conservative result of the indeterminate ones.

Definitions (n = valid closed trades; a win is net P&L > 0, a loss < 0):
  win rate        wins / n
  interval        Wilson score interval at 95 % (z = 1.959964), only when
                  n >= 30:  (p + z2/2n +- z sqrt(p(1-p)/n + z2/4n2)) / (1 + z2/n)
  expectancy R    mean of r_multiple (net P&L / estimated loss at the stop)
  profit factor   sum of wins / |sum of losses|; undefined without losses
  concentration   share of the sum of wins that comes from the best 1, 3, 5
  drawdown        the largest fall from a previous maximum, as a fraction of
                  that maximum, on the hourly series - balance and equity
With n < 30 everything computable is still shown, labelled
MUESTRA_INSUFICIENTE. Undefined figures are None, never 0.

Costs are estimates: `spread` is information (it is already inside the
prices and inside the P&L); slippage likewise; commission and financing are
the only amounts subtracted outside prices.

Reproducibility: content_sha256(run) hashes everything the run decided
(canonical JSON: decimals as text, instants in UTC). The moment of
execution, the operating system and the Python version are recorded apart
and never enter a hash: the same inputs give the same hash on Windows and
macOS.
"""

from __future__ import annotations

import hashlib
import json
import platform
from collections import Counter
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from investment_committee.domain import PriceBar
from investment_committee.market_intelligence.snapshots import bars_to_csv

from .forex_backtest import CONSERVATIVE, BacktestRun, Trade, TradeStatus
from .signals import Outcome, TradeSignal
from .trade_proposal import ExitReason, Rejection

MIN_SAMPLE = 30
INSUFFICIENT_SAMPLE = "MUESTRA_INSUFICIENTE"
SUFFICIENT_SAMPLE = "MUESTRA_DE_30_O_MAS"
Z_95 = Decimal("1.959964")
R_BUCKETS = ("< -1", "[-1, 0)", "[0, 1)", "[1, 2)", "[2, 3)", ">= 3")
_ZERO = Decimal("0")
_RATIO = Decimal("0.0001")
_CENT = Decimal("0.01")
WARNINGS = (
    "Simulación retrospectiva con modelo de ejecución ESTIMADO: precio indicativo único, sin bid/ask real.",
    "Spread, slippage, comisión y financiación son supuestos; ningún resultado es una rentabilidad neta real.",
    "El drawdown se calcula con marcas horarias: puede omitir movimientos dentro de la hora y subestimar el riesgo.",
    "Las operaciones indeterminadas quedan fuera de las métricas de operaciones cerradas válidas.",
    "No es una validación independiente ni permite elegir una estrategia ganadora.",
)


def _ratio(value: Decimal) -> Decimal:
    return value.quantize(_RATIO, rounding=ROUND_HALF_EVEN)


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_EVEN)


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    return sum(values, _ZERO) / len(values) if values else None


def wilson_interval(wins: int, n: int) -> tuple[Decimal, Decimal]:
    """The 95 % Wilson score interval of a proportion wins / n."""
    if n <= 0 or not 0 <= wins <= n:
        raise ValueError("0 <= wins <= n and n > 0")
    p, size, z2 = Decimal(wins) / n, Decimal(n), Z_95 * Z_95
    centre = p + z2 / (2 * size)
    half = Z_95 * (p * (1 - p) / size + z2 / (4 * size * size)).sqrt()
    return _ratio((centre - half) / (1 + z2 / size)), _ratio((centre + half) / (1 + z2 / size))


@dataclass(frozen=True)
class Drawdown:
    fraction: Decimal  # of the maximum it fell from
    amount: Decimal
    peak: Decimal
    peak_at: datetime | None
    trough_at: datetime | None


def drawdown(points: Sequence[tuple[datetime, Decimal]]) -> Drawdown:
    """The largest fall from a previous maximum, by fraction of that maximum."""
    worst = Drawdown(_ZERO, _ZERO, points[0][1] if points else _ZERO, None, None)
    peak, peak_at = None, None
    for at, value in points:
        if peak is None or value > peak:
            peak, peak_at = value, at
        if peak > 0 and (peak - value) / peak > worst.fraction:
            worst = Drawdown((peak - value) / peak, peak - value, peak, peak_at, at)
    return Drawdown(_ratio(worst.fraction), worst.amount, worst.peak, worst.peak_at, worst.trough_at)


@dataclass(frozen=True)
class ClosedStats:
    """Valid closed trades only."""

    n: int
    sample: str
    wins: int
    losses: int
    flat: int
    win_rate: Decimal | None
    win_rate_interval_95: tuple[Decimal, Decimal] | None  # Wilson; None with fewer than 30
    net_pnl: Decimal
    gross_profit: Decimal
    gross_loss: Decimal  # positive
    average_win: Decimal | None
    average_loss: Decimal | None
    average_win_r: Decimal | None
    average_loss_r: Decimal | None
    expectancy_r: Decimal | None
    profit_factor: Decimal | None  # None: no trades, or no losses to divide by
    exits: Mapping[str, int]
    gaps: int
    average_hours: Decimal | None
    r_distribution: Mapping[str, int]
    costs: Mapping[str, Decimal]
    concentration: Mapping[int, Decimal | None]


def _bucket(r: Decimal) -> str:
    if r < -1:
        return R_BUCKETS[0]
    for label, limit in zip(R_BUCKETS[1:5], (0, 1, 2, 3)):
        if r < limit:
            return label
    return R_BUCKETS[5]


def closed_stats(trades: Sequence[Trade]) -> ClosedStats:
    """Statistics of the CLOSED trades among `trades`; the others are ignored."""
    closed = [t for t in trades if t.status is TradeStatus.CLOSED and t.result is not None]
    results = [t.result for t in closed if t.result is not None]
    n = len(results)
    wins = sorted((r.net_pnl for r in results if r.net_pnl > 0), reverse=True)
    losses = [r.net_pnl for r in results if r.net_pnl < 0]
    gross_profit, gross_loss = sum(wins, _ZERO), -sum(losses, _ZERO)
    win_r, loss_r = [r.r_multiple for r in results if r.net_pnl > 0], [r.r_multiple for r in results if r.net_pnl < 0]
    hours = [Decimal(int((t.exit_at - t.entry_at).total_seconds())) / 3600 for t in closed if t.exit_at is not None]
    units = [(Decimal(t.proposal.units or 0), t.proposal, t.result) for t in closed]

    def rounded(value: Decimal | None, to=_ratio) -> Decimal | None:  # type: ignore[no-untyped-def]
        return None if value is None else to(value)

    return ClosedStats(
        n=n, sample=SUFFICIENT_SAMPLE if n >= MIN_SAMPLE else INSUFFICIENT_SAMPLE, wins=len(wins), losses=len(losses), flat=n - len(wins) - len(losses),
        win_rate=_ratio(Decimal(len(wins)) / n) if n else None,
        win_rate_interval_95=wilson_interval(len(wins), n) if n >= MIN_SAMPLE else None,
        net_pnl=sum((r.net_pnl for r in results), _ZERO), gross_profit=gross_profit, gross_loss=gross_loss,
        average_win=rounded(_mean(wins), _money), average_loss=rounded(_mean(losses), _money),
        average_win_r=rounded(_mean(win_r)), average_loss_r=rounded(_mean(loss_r)),
        expectancy_r=rounded(_mean([r.r_multiple for r in results])),
        profit_factor=_ratio(gross_profit / gross_loss) if gross_loss > 0 else None,
        exits={reason.value: sum(1 for r in results if r.reason is reason) for reason in ExitReason},
        gaps=sum(1 for r in results if r.gap), average_hours=rounded(_mean(hours), _money),
        r_distribution={label: sum(1 for r in results if _bucket(r.r_multiple) == label) for label in R_BUCKETS},
        costs={
            "spread_informativo": sum((p.spread_cost or _ZERO for _, p, _r in units), _ZERO),
            "slippage_entrada_informativo": sum((p.entry_slippage_cost or _ZERO for _, p, _r in units), _ZERO),
            "slippage_salida_informativo": sum((_money(count * (p.slippage or _ZERO)) for count, p, r in units if r is not None and r.reason is not ExitReason.TARGET), _ZERO),
            "comision": sum((r.commission_cost for r in results), _ZERO),
            "financiacion": sum((r.financing_cost for r in results), _ZERO),
        },
        concentration={k: (_ratio(sum(wins[:k], _ZERO) / gross_profit) if gross_profit > 0 else None) for k in (1, 3, 5)},
    )


@dataclass(frozen=True)
class Funnel:
    """From confirmed signals to trades, for one instrument or all."""

    signals: int
    rejected: int
    rejections: Mapping[str, int]  # a proposal can carry several reasons
    rejected_position_open: int
    opened: int
    closed_valid: int
    indeterminate: int
    open_at_end: int


@dataclass(frozen=True)
class Month:
    closed_valid: int
    wins: int
    net_pnl: Decimal  # valid closed trades
    indeterminate: int
    indeterminate_conservative: Decimal
    indeterminate_optimistic: Decimal


@dataclass(frozen=True)
class Summary:
    engine: str
    strategy_id: str
    parameters_sha256: str
    scenario: str
    indeterminate_policy: str
    start: datetime
    end: datetime
    coverage: Mapping[str, Mapping[str, int]]
    funnel: Mapping[str, Funnel]  # per symbol, and "TOTAL"
    closed: Mapping[str, ClosedStats]  # per symbol, and "TOTAL"
    indeterminate_by_kind: Mapping[str, int]
    indeterminate_conservative: Decimal
    indeterminate_optimistic: Decimal
    conservative_pnl: Decimal  # valid + indeterminate as stops
    optimistic_pnl: Decimal  # valid + indeterminate as targets: same trades and sizes, NOT the optimistic run
    monthly: Mapping[str, Month]  # by the month (UTC) the account booked the trade
    initial_capital: Decimal
    final_balance: Decimal
    return_fraction: Decimal  # of the account as booked
    final_equity: Decimal
    open_unrealized: Decimal
    drawdown_balance: Drawdown
    drawdown_equity: Drawdown
    stale_valuations: int  # hourly points valued at a price that was not current
    warnings: Sequence[str] = WARNINGS


def _funnel(run: BacktestRun, symbol: str | None) -> Funnel:
    decisions = [d for d in run.decisions if symbol in (None, d.symbol)]
    trades = [t for t in run.trades if symbol in (None, t.symbol)]
    rejected = [d for d in decisions if not d.proposal.accepted]
    reasons = Counter(reason.value for d in rejected for reason in d.proposal.rejections)
    return Funnel(
        signals=len(decisions), rejected=len(rejected), rejections=dict(sorted(reasons.items())),
        rejected_position_open=sum(1 for d in rejected if Rejection.POSITION_OPEN in d.proposal.rejections),
        opened=len(trades), closed_valid=sum(1 for t in trades if t.status is TradeStatus.CLOSED),
        indeterminate=sum(1 for t in trades if t.status is TradeStatus.INDETERMINATE),
        open_at_end=sum(1 for t in trades if t.status is TradeStatus.OPEN_AT_END),
    )


def summarize(run: BacktestRun) -> Summary:
    unknown = [t for t in run.trades if t.status is TradeStatus.INDETERMINATE and t.indeterminate is not None]
    conservative = sum((t.indeterminate.conservative.net_pnl for t in unknown if t.indeterminate), _ZERO)
    optimistic = sum((t.indeterminate.optimistic.net_pnl for t in unknown if t.indeterminate), _ZERO)
    total = closed_stats(run.trades)
    months: dict[str, Month] = {}
    for trade in run.trades:
        if trade.recorded_at is None:
            continue
        key = trade.recorded_at.astimezone(timezone.utc).strftime("%Y-%m")
        m = months.get(key, Month(0, 0, _ZERO, 0, _ZERO, _ZERO))
        if trade.status is TradeStatus.CLOSED and trade.result is not None:
            m = Month(m.closed_valid + 1, m.wins + (trade.result.net_pnl > 0), m.net_pnl + trade.result.net_pnl, m.indeterminate,
                      m.indeterminate_conservative, m.indeterminate_optimistic)
        elif trade.indeterminate is not None:
            m = Month(m.closed_valid, m.wins, m.net_pnl, m.indeterminate + 1, m.indeterminate_conservative + trade.indeterminate.conservative.net_pnl,
                      m.indeterminate_optimistic + trade.indeterminate.optimistic.net_pnl)
        months[key] = m
    capital = run.limits.capital
    last = run.equity[-1] if run.equity else None
    return Summary(
        engine=run.engine, strategy_id=run.strategy_id, parameters_sha256=run.parameters_sha256, scenario=run.scenario.name,
        indeterminate_policy=run.scenario.indeterminate_policy, start=run.start, end=run.end, coverage=run.coverage,
        funnel={**{symbol: _funnel(run, symbol) for symbol in run.symbols}, "TOTAL": _funnel(run, None)},
        closed={**{symbol: closed_stats([t for t in run.trades if t.symbol == symbol]) for symbol in run.symbols}, "TOTAL": total},
        indeterminate_by_kind=dict(sorted(Counter(t.uncertainty.kind for t in unknown if t.uncertainty).items())),
        indeterminate_conservative=conservative, indeterminate_optimistic=optimistic,
        conservative_pnl=total.net_pnl + conservative, optimistic_pnl=total.net_pnl + optimistic,
        monthly=dict(sorted(months.items())), initial_capital=capital, final_balance=run.final_balance,
        return_fraction=_ratio(run.final_balance / capital - 1), final_equity=last.equity if last else capital,
        open_unrealized=sum((t.unrealized_pnl or _ZERO for t in run.trades if t.status is TradeStatus.OPEN_AT_END), _ZERO),
        drawdown_balance=drawdown([(p.at, p.balance) for p in run.equity]), drawdown_equity=drawdown([(p.at, p.equity) for p in run.equity]),
        stale_valuations=sum(1 for p in run.equity if p.stale),
    )


def first_evaluable_at(signals: Mapping[str, Mapping[datetime, TradeSignal]]) -> datetime | None:
    """The first instant at which the strategy could evaluate any instrument
    (its data were not rejected): where its warm-up ends."""
    moments = [moment for evaluations in signals.values() for moment, signal in evaluations.items() if signal.outcome is not Outcome.DATA_REJECTED]
    return min(moments) if moments else None


def common_start(*passes: Mapping[str, Mapping[datetime, TradeSignal]]) -> datetime | None:
    """Where the common comparison period begins: the latest of the
    strategies' first evaluable instants. A calendar window - no hour is
    removed from it because one strategy could not evaluate it."""
    starts = [first_evaluable_at(signals) for signals in passes]
    return None if not starts or any(s is None for s in starts) else max(s for s in starts if s is not None)


# --- report text ---


def _show(value: object, suffix: str = "") -> str:
    return "no definido" if value is None else f"{value}{suffix}"


def _percent(fraction: Decimal | None) -> str:
    return "no definido" if fraction is None else f"{(fraction * 100).quantize(_CENT)} %"


def render(summary: Summary) -> str:
    """The summary as text. Sample sizes are always shown."""
    lines = [
        f"BACKTEST {summary.strategy_id} | escenario {summary.scenario} | motor {summary.engine}",
        f"Período {summary.start.isoformat()} a {summary.end.isoformat()} | indeterminadas contabilizadas: {summary.indeterminate_policy}",
        *[f"AVISO: {warning}" for warning in summary.warnings],
        "",
    ]
    for name, funnel in summary.funnel.items():
        stats = summary.closed[name]
        lines += [
            f"[{name}]",
            f"  señales {funnel.signals} | rechazadas {funnel.rejected} (por posición abierta {funnel.rejected_position_open}) | abiertas {funnel.opened}",
            f"  cerradas válidas {funnel.closed_valid} | indeterminadas {funnel.indeterminate} | abiertas al final {funnel.open_at_end}",
            f"  motivos de rechazo: {dict(funnel.rejections) or 'ninguno'}",
            f"  operaciones cerradas válidas: n = {stats.n} ({stats.sample})",
            f"  aciertos {stats.wins} | pérdidas {stats.losses} | neutras {stats.flat} | porcentaje de aciertos {_percent(stats.win_rate)}"
            + (f" (Wilson 95 %: {_percent(stats.win_rate_interval_95[0])} a {_percent(stats.win_rate_interval_95[1])})" if stats.win_rate_interval_95 else ""),
            f"  resultado {stats.net_pnl} USD | ganancia media {_show(stats.average_win, ' USD')} | pérdida media {_show(stats.average_loss, ' USD')}",
            f"  expectativa {_show(stats.expectancy_r, ' R')} | profit factor {_show(stats.profit_factor)} | duración media {_show(stats.average_hours, ' h')}",
            f"  salidas {dict(stats.exits)} | con gap {stats.gaps}",
            f"  distribución en R {dict(stats.r_distribution)}",
            f"  costos estimados {({k: str(v) for k, v in stats.costs.items()})}",
            f"  concentración de ganancias (mejores 1, 3, 5): {', '.join(_percent(v) for v in stats.concentration.values())}",
        ]
        if name in summary.coverage:
            lines.append(f"  cobertura (evaluaciones por resultado): {dict(summary.coverage[name])}")
        lines.append("")
    lines += [
        "[CUENTA]",
        f"  capital inicial {summary.initial_capital} USD | saldo realizado final {summary.final_balance} USD ({_percent(summary.return_fraction)})",
        f"  equity estimado final {summary.final_equity} USD | no realizado en posiciones abiertas {summary.open_unrealized} USD",
        f"  drawdown sobre saldo realizado {_percent(summary.drawdown_balance.fraction)} ({summary.drawdown_balance.amount} USD)",
        f"  drawdown sobre equity estimado {_percent(summary.drawdown_equity.fraction)} ({summary.drawdown_equity.amount} USD)",
        f"  valoraciones horarias con precio no vigente: {summary.stale_valuations}",
        "[INDETERMINADAS]",
        f"  por tipo {dict(summary.indeterminate_by_kind) or 'ninguna'} | conservador {summary.indeterminate_conservative} USD | optimista {summary.indeterminate_optimistic} USD",
        f"  resultado total conservador {summary.conservative_pnl} USD | optimista (mismas operaciones y tamaños) {summary.optimistic_pnl} USD",
        "[POR MES] (mes UTC en que la cuenta registró la operación)",
        *[f"  {key}: válidas n = {m.closed_valid}, aciertos {m.wins}, resultado {m.net_pnl} USD | indeterminadas {m.indeterminate} "
          f"(conservador {m.indeterminate_conservative}, optimista {m.indeterminate_optimistic})" for key, m in summary.monthly.items()],
    ]
    return "\n".join(lines) + "\n"


# --- reproducible record ---


def canonical(value: Any) -> Any:
    """`value` as plain JSON data with one spelling: decimals as text,
    instants in UTC, enums by value, mappings with text keys."""
    if isinstance(value, Enum):  # first: a str-based enum is also a str
        return value.value
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, timedelta):
        return f"{int(value.total_seconds())}s"
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: canonical(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(canonical(key)): canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    raise TypeError(f"no canonical form for {type(value).__name__}")


def _sha256(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()


def content_sha256(run: BacktestRun) -> str:
    """SHA-256 of everything the run decided."""
    return _sha256(canonical(run))


def dataset_sha256(bars: Mapping[str, Sequence[PriceBar]]) -> str:
    """SHA-256 of the bars a run was given (symbols in order, bars as the
    snapshot CSV writes them)."""
    digest = hashlib.sha256()
    for symbol in sorted(bars):
        digest.update(symbol.encode("utf-8") + b"\n" + bars_to_csv(sorted(bars[symbol], key=lambda b: b.timestamp)).encode("utf-8"))
    return digest.hexdigest()


def record(run: BacktestRun, *, commit: str, dataset: str, dataset_description: str, executed_at: datetime) -> dict[str, Any]:
    """The audit record of a run. `reproducible` depends only on inputs and
    results; `execution` (when, where) never enters a hash."""
    if executed_at.tzinfo is None:
        raise ValueError("executed_at sin zona horaria")
    reproducible = {
        "engine": run.engine, "commit": commit, "dataset_sha256": dataset, "dataset_description": dataset_description,
        "strategy_id": run.strategy_id, "parameters_sha256": run.parameters_sha256,
        "period": {"start": canonical(run.start), "end": canonical(run.end)},
        "scenario": canonical(run.scenario), "execution_parameters": canonical(run.scenario.execution(run.strategy_id)),
        "instrument_rules": {symbol: canonical(run.scenario.rules(symbol)) for symbol in run.symbols},
        "risk_limits": canonical(run.limits),
        "uncertainty_policy": {
            "account_books": run.scenario.indeterminate_policy,
            "same_bar": "stop y objetivo en la misma vela: INDETERMINADA",
            "data_hole": "hora esperada sin vela con posición abierta: INDETERMINADA al volver los precios",
            "valid_for_metrics": False,
            "main_account_is_conservative": run.scenario.indeterminate_policy == CONSERVATIVE,
        },
        "results_sha256": content_sha256(run), "summary": canonical(summarize(run)),
    }
    return {
        "reproducible": reproducible, "reproducible_sha256": _sha256(reproducible),
        "execution": {"executed_at": canonical(executed_at), "operating_system": platform.platform(), "python": platform.python_version(),
                      "note": "metadatos no determinantes: no entran en ningún hash"},
    }


def write_record(directory: Path, entry: Mapping[str, Any], run: BacktestRun) -> list[Path]:
    """record.json, run.json (every decision, trade and hourly point) and
    report.txt under `directory`, with the same bytes on every system."""
    directory.mkdir(parents=True, exist_ok=True)
    files = {
        "record.json": json.dumps(entry, sort_keys=True, indent=1, ensure_ascii=False) + "\n",
        "run.json": json.dumps(canonical(run), sort_keys=True, indent=1, ensure_ascii=False) + "\n",
        "report.txt": render(summarize(run)),
    }
    for name, text in files.items():
        (directory / name).write_bytes(text.encode("utf-8"))
    return [directory / name for name in files]
