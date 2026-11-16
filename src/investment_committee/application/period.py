"""PeriodEvaluationService: multi-month robustness evaluation of the scanner
with the audited replay engine.

Data are downloaded ONCE per instrument and kept as a reproducible snapshot
(market_intelligence.snapshots: CSV + manifest with SHA-256 hashes) under
data/raw/snapshots/; later runs read the snapshot unless asked to refresh.
Then, without changing any strategy parameter, risk rule or policy:
  - a CONTINUOUS replay of the whole period for each cost scenario
    (base, spread and slippage x1.5, x2), sliced into session months;
  - a NORMALIZED replay of each month alone from 10 000 USD (base costs).
Everything here is a RETROSPECTIVE BACKTEST: these months have been seen,
so they cannot validate anything (simulation.replay, docs).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from investment_committee.market_intelligence import MarketDataError, MarketIntelligenceService
from investment_committee.market_intelligence.snapshots import DEFAULT_SNAPSHOT_ROOT, SnapshotManifest, read_snapshot, write_snapshot
from investment_committee.simulation import DEFAULT_UNIVERSE, InstrumentProfile, RiskLimits, ScanStrategy, TrendMomentumVolatilityStrategy
from investment_committee.simulation.period import (
    DataGap,
    MonthRow,
    MonthWindow,
    closed_bars_before,
    continuous_months,
    data_gaps,
    month_windows,
    normalized_month,
    scale_costs,
)
from investment_committee.simulation.replay import ReplayConfig, ReplayReport, run_replay

from .errors import ApplicationServiceError
from .replay import WARMUP, YAHOO_1H_MAX_DAYS

_logger = logging.getLogger(__name__)
COST_SCENARIOS: tuple[tuple[str, Decimal], ...] = (("base", Decimal("1")), ("x1.5", Decimal("1.5")), ("x2", Decimal("2")))
SOURCE = "Yahoo Finance (yfinance), velas de 1 hora etiquetadas por apertura"


class PeriodEvaluationError(ApplicationServiceError):
    """The period cannot be evaluated (no data, not enough warm-up, too old)."""


@dataclass(frozen=True)
class PeriodReport:
    first_month: str
    last_month: str
    windows: Sequence[MonthWindow]
    manifest: SnapshotManifest
    snapshot_dir: str
    warmup_bars: Mapping[str, int]  # closed bars before the first decision
    required_warmup: int
    gaps: Mapping[str, Sequence[DataGap]]
    continuous: Mapping[str, ReplayReport]  # by cost scenario
    normalized: Mapping[str, ReplayReport]  # by month, base costs
    continuous_rows: Sequence[MonthRow]
    normalized_rows: Sequence[MonthRow]
    generated_at: datetime
    symbols: Sequence[str] = field(default_factory=tuple)


class PeriodEvaluationService:
    def __init__(
        self,
        market_intelligence_service: MarketIntelligenceService,
        snapshot_root: Path = DEFAULT_SNAPSHOT_ROOT,
        universe: Sequence[InstrumentProfile] = DEFAULT_UNIVERSE,
        limits: RiskLimits | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._market = market_intelligence_service
        self._root = Path(snapshot_root)
        self._universe = tuple(universe)
        self._limits = limits or RiskLimits()
        self._clock = clock

    def evaluate(
        self, first_day: date, end_day: date, refresh: bool = False, strategy: ScanStrategy | None = None, normalized: bool = True,
    ) -> PeriodReport:
        """Session months from `first_day`'s month to the month BEFORE
        `end_day` (end exclusive, like --hasta 2026-10-01)."""
        windows = month_windows(first_day, end_day - timedelta(days=1))
        if not windows:
            raise PeriodEvaluationError("el periodo no contiene ningún mes")
        start, end = windows[0].start, windows[-1].end
        manifest, bars, directory = self._snapshot(start, end, refresh)
        strategy = strategy or TrendMomentumVolatilityStrategy()
        required = getattr(strategy, "min_bars", None) or strategy.parameters.min_bars  # type: ignore[attr-defined]
        warmup = {s: closed_bars_before(bars.get(s, ()), start) for s in (p.symbol for p in self._universe)}
        short = [s for s, n in warmup.items() if n < required]
        if short:
            raise PeriodEvaluationError(f"velas previas insuficientes para los indicadores en {', '.join(short)} (se necesitan {required})")

        continuous = {
            name: run_replay(bars, ReplayConfig(start=start, end=end, universe=scale_costs(self._universe, factor), limits=self._limits),
                             strategy, generated_at=self._clock())
            for name, factor in COST_SCENARIOS
        }
        normalized = {
            w.label: run_replay(bars, ReplayConfig(start=w.start, end=w.end, universe=self._universe, limits=self._limits), strategy, generated_at=self._clock())
            for w in windows
        } if normalized else {}
        symbols = [p.symbol for p in self._universe]
        continuous_rows = [row for name, rep in continuous.items() for result in rep.results for row in continuous_months(result, windows, symbols, name)]
        normalized_rows = [normalized_month(result, w, symbols, "base") for w in windows if w.label in normalized for result in normalized[w.label].results]
        return PeriodReport(
            first_month=windows[0].label, last_month=windows[-1].label, windows=windows, manifest=manifest, snapshot_dir=str(directory),
            warmup_bars=warmup, required_warmup=required,
            gaps={s: data_gaps(bars.get(s, ()), start, end) for s in symbols},
            continuous=continuous, normalized=normalized, continuous_rows=continuous_rows, normalized_rows=normalized_rows,
            generated_at=self._clock(), symbols=symbols,
        )

    def _snapshot(self, start: datetime, end: datetime, refresh: bool):  # type: ignore[no-untyped-def]
        directory = self._root / f"fx_1h_{start:%Y%m%d}_{end:%Y%m%d}"
        if (directory / "manifest.json").exists() and not refresh:
            manifest, bars = read_snapshot(directory)
            return manifest, bars, directory
        now = self._clock()
        if end > now:
            raise PeriodEvaluationError("el periodo termina en el futuro")
        days = (now - start + WARMUP).days + 1
        if days > YAHOO_1H_MAX_DAYS:
            raise PeriodEvaluationError(f"Yahoo solo ofrece velas de 1 hora de los últimos {YAHOO_1H_MAX_DAYS} días")
        bars = {}
        for profile in self._universe:
            try:
                snapshot = self._market.get_intraday_history(profile.symbol, interval="1h", period=f"{days}d")
            except MarketDataError as exc:
                raise PeriodEvaluationError(f"{profile.symbol}: sin datos ({exc})") from exc
            bars[profile.symbol] = [b for b in snapshot.price_bars if start - WARMUP <= b.timestamp and b.timestamp + timedelta(hours=1) <= end]
        manifest = write_snapshot(directory, bars, source=SOURCE, interval="1h", requested_from=start - WARMUP, requested_to=end, downloaded_at=now)
        _, reread = read_snapshot(directory)  # evaluate exactly what was stored
        return manifest, reread, directory


# --- tables for CSV / JSON ---


def _num(value: Any) -> Any:
    return None if value is None else str(value) if isinstance(value, Decimal) else value


def month_table(rows: Sequence[MonthRow], symbols: Sequence[str]) -> list[dict[str, Any]]:
    table = []
    for r in rows:
        item = {k: _num(getattr(r, k)) for k in ("mode", "scenario", "policy", "month", "start_balance", "end_balance", "pnl", "net_return",
                                                  "trades", "wins", "losses", "win_rate", "expectancy", "profit_factor", "max_drawdown",
                                                  "max_drawdown_fraction", "costs", "risk_rejections", "other_rejections")}
        for s in symbols:
            item[f"pnl_{s}"] = str(r.by_instrument[s])
            item[f"trades_{s}"] = r.trades_by_instrument[s]
        table.append(item)
    return table


def trades_table(report: PeriodReport) -> list[dict[str, Any]]:
    rows = []
    for scenario, rep in report.continuous.items():
        for result in rep.results:
            for t in sorted(result.trades, key=lambda x: (x.decided_at, x.symbol)):
                rows.append({
                    "scenario": scenario, "policy": result.policy.value, "symbol": t.symbol, "direction": t.direction.value,
                    "decided_at": t.decided_at.isoformat(), "entry_time": t.entry_time.isoformat(), "entry_price": str(t.entry_price),
                    "stop_loss": str(t.stop_loss), "take_profit": str(t.take_profit), "units": t.units, "risk_usd": str(t.risk_usd),
                    "exit_time": t.exit_time.isoformat(), "exit_price": str(t.exit_price), "exit_reason": t.exit_reason.value,
                    "same_bar_conflict": t.same_bar_conflict, "net_pnl_usd": str(t.net_pnl_usd), "costs_usd": str(t.costs_usd),
                })
    return rows


def equity_table(report: PeriodReport) -> list[dict[str, Any]]:
    rows = []
    for scenario, rep in report.continuous.items():
        for result in rep.results:
            for p in result.equity_curve:
                rows.append({"scenario": scenario, "policy": result.policy.value, "at": p.at.isoformat(), "balance": str(p.balance),
                             "equity": str(p.equity), "open_positions": p.open_positions, "open_risk": str(p.open_risk)})
    return rows


def rejections_table(report: PeriodReport) -> list[dict[str, Any]]:
    from investment_committee.simulation.period import RISK_REJECTIONS

    rows = []
    for scenario, rep in report.continuous.items():
        for result in rep.results:
            for r in result.rejections:
                rows.append({"scenario": scenario, "policy": result.policy.value, "decided_at": r.decided_at.isoformat(), "symbol": r.symbol,
                             "reason": r.reason, "risk_limit": r.reason in RISK_REJECTIONS, "detail": r.detail})
    return rows


def summary(report: PeriodReport) -> dict[str, Any]:
    """Machine-readable summary (JSON)."""
    def totals(rep: ReplayReport) -> dict[str, Any]:
        return {r.policy.value: {"final_balance": str(r.final_balance), "net_pnl": str(r.metrics.net_pnl), "net_return": str(r.net_return),
                                 "trades": r.metrics.trades, "wins": r.metrics.wins, "losses": r.metrics.losses,
                                 "profit_factor": _num(r.metrics.profit_factor), "expectancy": _num(r.metrics.expectancy),
                                 "max_drawdown": str(r.max_drawdown), "max_drawdown_fraction": str(r.max_drawdown_fraction),
                                 "costs": str(r.metrics.total_costs),
                                 "by_instrument": {s: str(m.net_pnl) for s, m in r.by_instrument.items()}} for r in rep.results}
    return {
        "kind": "BACKTEST_RETROSPECTIVO",
        "validated": False,
        "note": "meses ya observados: no es validación fuera de muestra ni seguimiento prospectivo",
        "period": {"first_month": report.first_month, "last_month": report.last_month,
                   "start": report.windows[0].start.isoformat(), "end": report.windows[-1].end.isoformat(),
                   "month_definition": "días de sesión FX (17:00 a 17:00 de Nueva York)"},
        "data": {"source": report.manifest.source, "snapshot": report.snapshot_dir, "downloaded_at": report.manifest.downloaded_at,
                 "combined_sha256": report.manifest.combined_sha256,
                 "series": [s.__dict__ for s in report.manifest.series],
                 "warmup_bars": dict(report.warmup_bars), "required_warmup": report.required_warmup,
                 "gaps": {s: [{"start": g.start.isoformat(), "end": g.end.isoformat(), "hours": g.hours} for g in gaps] for s, gaps in report.gaps.items()}},
        "no_trade_baseline": str(report.continuous["base"].config.limits.capital),
        "continuous": {name: totals(rep) for name, rep in report.continuous.items()},
        "normalized": {month: totals(rep) for month, rep in report.normalized.items()},
    }
