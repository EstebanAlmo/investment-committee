"""ProspectiveMonitor: the pre-registered prospective evaluation, one hourly
cycle at a time (docs/prospective-monitoring.md).

At each closed 1-hour candle inside the protocol window (2026-10-19 00:00
UTC to 2027-01-15 21:00 UTC) and inside the FX week, a cycle:
  1. refuses to run if any strategy's parameters differ from the frozen
     fingerprints of the protocol;
  2. records hours of the open week that were never processed as
     MONITOR_NO_EJECUTADO incidents - they are NOT evaluated later, so no
     retrospective signal is ever produced;
  3. downloads each instrument once (Yahoo, read-only) and waits, up to
     `data_wait`, for the candle that just closed; after that it evaluates
     with what exists and records DATOS_DESACTUALIZADOS;
  4. runs the four frozen strategies through the scanner (closed candles
     only, same controls, fresh 10 000 USD hypothetical account), writing
     scan, observations, ranked candidates and per-candle evaluations in one
     transaction (simulation.sqlite_store.MonitorScanLedger). A candle is
     evaluated once per strategy and instrument: a re-run, a restart or a
     provider that has not moved on adds nothing;
  5. updates the hypothetical follow-up of open candidates (tracker) and,
     if given, checks the Portfolio Guardian's alerts;
  6. queues notifications in the SQLite outbox (never sends them).
Nothing here opens a trade - real or simulated - or contacts a broker. The
scanner's own trade journal is never touched.

Time provenance: every evaluation stores the candle's close (`bar_close`),
the cycle's candle (`moment`), when data were fetched and when the
evaluation ran.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping, Sequence

from investment_committee.market_intelligence import MarketDataError, MarketIntelligenceService
from investment_committee.memory.database import DEFAULT_DB_PATH, audit, connect, utc
from investment_committee.simulation import DEFAULT_UNIVERSE, AccountState, InstrumentProfile, ScanStrategy, closed_bars
from investment_committee.simulation.sessions import is_week_open
from investment_committee.simulation.tracker import TrackerLedger
from investment_committee.simulation.strategies import all_strategies, parameters_fingerprint, strategy_parameters

from . import notifications as outbox
from .errors import ApplicationServiceError
from .opportunities import OpportunityScannerService, ScannerConfig
from .tracker import OpportunityTrackerService

_logger = logging.getLogger(__name__)
HOUR = timedelta(hours=1)
UTC = timezone.utc
PROTOCOL_START = datetime(2026, 10, 19, 0, tzinfo=UTC)
PROTOCOL_END = datetime(2027, 1, 15, 21, tzinfo=UTC)
# docs/strategy-research-v2.md: the versions and parameters the protocol froze.
FROZEN_FINGERPRINTS: Mapping[str, str] = {
    "tendencia-momentum-volatilidad@1.0.0": "13f2690da4ec129dc440967d45d07fd0576b561c05cd058634132cf4d0598977",
    "tendencia-confirmada-4h@0.1.0": "3185131d3789ad5b5a58ec4c62811ec50e596b19c487c1a071ef1cc58d007e0c",
    "ruptura-rango-londres@0.1.0": "52001a2c28a3b14061af068ce92785a590bbae53eaca0590e1003e3aa17026ae",
    "reversion-media-lateral@0.1.0": "9f4a57a00440d521b5cc07b69314e216e8686cc3afdb2ed27eb896362066bee8",
}
RELEVANT_STATUSES = ("ACTIVADA", "STOP", "OBJETIVO", "CIERRE_DE_SESIÓN", "EXPIRADA", "NO_EVALUABLE")


class ProtocolViolationError(ApplicationServiceError):
    """A strategy's parameters differ from the frozen protocol."""


@dataclass(frozen=True)
class MonitorSettings:
    window_start: datetime = PROTOCOL_START
    window_end: datetime = PROTOCOL_END
    data_wait: timedelta = timedelta(minutes=20)  # wait this long for the new candle before evaluating anyway
    max_lateness: timedelta = timedelta(minutes=50)  # later than this, the candle is a gap, not evaluated
    history_period: str = "30d"


@dataclass(frozen=True)
class CycleResult:
    moment: datetime
    status: str  # FUERA_DE_VENTANA | MERCADO_CERRADO | YA_PROCESADO | ESPERANDO_DATOS | TARDE | COMPLETADO | COMPLETADO_CON_INCIDENCIAS
    evaluated: Sequence[str] = ()  # strategy ids evaluated in this run
    new_recommendations: int = 0
    incidents: int = 0
    gaps_recorded: int = 0
    notifications_queued: int = 0
    detail: str = ""


class CachingMarket(MarketIntelligenceService):
    """Each instrument downloaded once per cycle; every consumer (scanner,
    tracker, guardian) sees the same bars. Failures are cached too."""

    def __init__(self, inner: MarketIntelligenceService, clock: Callable[[], datetime], period: str) -> None:
        self._inner, self._clock, self._period = inner, clock, period
        self._cache: dict[tuple[str, str], object] = {}
        self.fetched_at: dict[str, datetime] = {}

    def get_daily_history(self, ticker: str):  # type: ignore[no-untyped-def]
        return self._inner.get_daily_history(ticker)

    def get_intraday_history(self, ticker: str, interval: str, period: str):  # type: ignore[no-untyped-def]
        key = (ticker, interval)
        if key not in self._cache:
            try:
                self._cache[key] = self._inner.get_intraday_history(ticker, interval=interval, period=self._period)
            except MarketDataError as exc:
                self._cache[key] = exc
            self.fetched_at[ticker] = self._clock()
        value = self._cache[key]
        if isinstance(value, Exception):
            raise value
        return value


def verify_frozen(strategies: Sequence[ScanStrategy]) -> None:
    for strategy in strategies:
        strategy_id = f"{strategy.name}@{strategy.version}"
        expected = FROZEN_FINGERPRINTS.get(strategy_id)
        actual = parameters_fingerprint(strategy_parameters(strategy))
        if expected is None or actual != expected:
            raise ProtocolViolationError(f"{strategy_id}: los parámetros no coinciden con los congelados por el protocolo")


class ProspectiveMonitor:
    def __init__(
        self,
        market_intelligence_service: MarketIntelligenceService,
        db_path=DEFAULT_DB_PATH,  # type: ignore[no-untyped-def]
        settings: MonitorSettings | None = None,
        strategies: Sequence[ScanStrategy] | None = None,
        universe: Sequence[InstrumentProfile] = DEFAULT_UNIVERSE,
        guardian=None,  # type: ignore[no-untyped-def]  # PortfolioGuardianService | None
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        verify: bool = True,
        scan_ledger_factory: Callable[..., object] | None = None,  # (db_path, moment, fetched_at) -> MonitorScanLedger
        tracker_ledger: TrackerLedger | None = None,
    ) -> None:
        self._market = market_intelligence_service
        self._db = db_path
        self._settings = settings or MonitorSettings()
        self._strategies = tuple(strategies or all_strategies())
        self._universe = tuple(universe)
        self._guardian = guardian
        self._clock = clock
        if scan_ledger_factory is None or tracker_ledger is None:
            raise ValueError("build the monitor with application.factory.build_default_monitor (SQLite adapters)")
        self._scan_ledger_factory = scan_ledger_factory
        self._tracker_ledger = tracker_ledger
        if verify:
            verify_frozen(self._strategies)

    def run_once(self) -> CycleResult:
        now = self._clock()
        moment = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        s = self._settings
        if not (s.window_start <= moment <= s.window_end):
            return CycleResult(moment, "FUERA_DE_VENTANA", detail="fuera de la ventana del protocolo")
        if not is_week_open(moment - HOUR):
            return CycleResult(moment, "MERCADO_CERRADO", detail="la vela que cierra ahora está fuera de la semana Forex")

        with connect(self._db) as conn:
            cycle = conn.execute("SELECT status FROM monitor_cycles WHERE moment = ?", (utc(moment),)).fetchone()
            if cycle is not None and cycle["status"] != "EN_CURSO":
                return CycleResult(moment, "YA_PROCESADO")
            gaps = self._record_gaps(conn, moment, now)
            if now - moment > s.max_lateness:
                self._gap(conn, moment, now, f"el monitor corrió {int((now - moment).total_seconds() // 60)} min después del cierre; no se evalúa tarde")
                return CycleResult(moment, "TARDE", gaps_recorded=gaps + 1)

        market = CachingMarket(self._market, self._clock, s.history_period)
        missing = self._not_ready(market, moment)
        if missing and now - moment < s.data_wait:
            return CycleResult(moment, "ESPERANDO_DATOS", gaps_recorded=gaps, detail="falta la vela recién cerrada: " + ", ".join(missing))

        with connect(self._db) as conn:
            if cycle is None:
                conn.execute("INSERT INTO monitor_cycles (moment, started_at, status, detail_json) VALUES (?, ?, 'EN_CURSO', '{}')", (utc(moment), utc(now)))
                audit(conn, now, "CICLO_INICIADO", "monitor_cycle", utc(moment), {"missing": missing})
            done = {r["strategy_id"] for r in conn.execute(
                "SELECT DISTINCT strategy_id FROM monitor_evaluations WHERE moment = ?", (utc(moment),))}
            done |= {r["strategy_id"] for r in conn.execute("SELECT strategy_id FROM scans WHERE scan_id LIKE ?", (f"mon-{moment:%Y%m%dT%H%MZ}-%",))}

        evaluated, new_recs, incidents = [], [], 0
        for strategy in self._strategies:
            strategy_id = f"{strategy.name}@{strategy.version}"
            if strategy_id in done:
                continue
            ledger = self._scan_ledger_factory(self._db, moment, min(market.fetched_at.values(), default=now))
            scanner = OpportunityScannerService(
                market, ledger, ScannerConfig(universe=self._universe), strategy=strategy,  # type: ignore[arg-type]
                account_provider=lambda _now: AccountState.fresh(ScannerConfig().limits.capital),
                clock=self._clock, id_factory=lambda sid=strategy_id: f"mon-{moment:%Y%m%dT%H%MZ}-{sid}",
            )
            try:
                scanner.scan(ai_reviews=0)
            except Exception as exc:  # noqa: BLE001 - one strategy must not stop the cycle
                _logger.exception("monitor: %s failed at %s", strategy_id, moment)
                with connect(self._db) as conn:
                    conn.execute("INSERT INTO monitor_incidents (at, moment, kind, symbol, strategy_id, detail) VALUES (?, ?, 'ERROR_INTERNO', NULL, ?, ?)",
                                 (utc(self._clock()), utc(moment), strategy_id, f"{type(exc).__name__}: {exc}"[:300]))
                incidents += 1
                continue
            evaluated.append(strategy_id)
            new_recs += ledger.new_recommendations
            incidents += len(ledger.incidents)

        changes = self._follow_up(market)
        alert_events = self._guardian.check().events if self._guardian is not None else ()
        queued = self._notify(moment, new_recs, changes, alert_events)

        with connect(self._db) as conn:
            total_incidents = conn.execute("SELECT count(*) FROM monitor_incidents WHERE moment = ? AND kind != 'MONITOR_NO_EJECUTADO'", (utc(moment),)).fetchone()[0]
            status = "COMPLETADO" if total_incidents == 0 else "COMPLETADO_CON_INCIDENCIAS"
            conn.execute("UPDATE monitor_cycles SET status = ?, finished_at = ?, detail_json = ? WHERE moment = ?",
                         (status, utc(self._clock()), json.dumps({"evaluated": evaluated, "new_recommendations": len(new_recs), "status_changes": len(changes)}),
                          utc(moment)))
            audit(conn, self._clock(), "CICLO_TERMINADO", "monitor_cycle", utc(moment), {"status": status, "evaluated": evaluated})
        return CycleResult(moment, status, tuple(evaluated), len(new_recs), incidents, gaps, queued)

    def run_forever(self, interval: timedelta = timedelta(minutes=5), sleep: Callable[[float], None] = time.sleep,
                    iterations: int | None = None) -> None:
        """Continuous mode: one run_once every `interval`; stops with Ctrl+C
        (or after `iterations`, for tests)."""
        count = 0
        while iterations is None or count < iterations:
            try:
                result = self.run_once()
                _logger.info("monitor %s: %s %s", result.moment, result.status, result.detail)
            except ProtocolViolationError:
                raise
            except Exception:  # noqa: BLE001 - keep the loop alive; the next run records the gap
                _logger.exception("monitor cycle failed")
            count += 1
            if iterations is None or count < iterations:
                sleep(interval.total_seconds())

    # --- helpers ---

    def _not_ready(self, market: CachingMarket, moment: datetime) -> list[str]:
        missing = []
        for profile in self._universe:
            try:
                bars = closed_bars(market.get_intraday_history(profile.symbol, interval="1h", period=self._settings.history_period).price_bars, HOUR, moment)
            except MarketDataError:
                missing.append(f"{profile.symbol} (fallo del proveedor)")
                continue
            if not bars or bars[-1].timestamp + HOUR < moment:
                missing.append(profile.symbol)
        return missing

    def _gap(self, conn, moment: datetime, now: datetime, detail: str) -> bool:  # type: ignore[no-untyped-def]
        exists = conn.execute("SELECT 1 FROM monitor_incidents WHERE kind = 'MONITOR_NO_EJECUTADO' AND moment = ?", (utc(moment),)).fetchone()
        if exists:
            return False
        conn.execute("INSERT INTO monitor_incidents (at, moment, kind, symbol, strategy_id, detail) VALUES (?, ?, 'MONITOR_NO_EJECUTADO', NULL, NULL, ?)",
                     (utc(now), utc(moment), detail))
        return True

    def _record_gaps(self, conn, moment: datetime, now: datetime) -> int:  # type: ignore[no-untyped-def]
        """Open-week candles since the last cycle (or the window start) that
        no cycle processed: recorded, never evaluated afterwards."""
        last = conn.execute("SELECT max(moment) FROM monitor_cycles WHERE moment < ?", (utc(moment),)).fetchone()[0]
        start = datetime.fromisoformat(last) + HOUR if last else self._settings.window_start
        recorded, t = 0, start
        while t < moment:
            if is_week_open(t - HOUR) and conn.execute("SELECT 1 FROM monitor_cycles WHERE moment = ?", (utc(t),)).fetchone() is None:
                recorded += self._gap(conn, t, now, "ningún ciclo procesó esta vela (monitor detenido o equipo apagado)")
            t += HOUR
        return recorded

    def _follow_up(self, market: CachingMarket) -> list[tuple[str, str, str | None]]:
        """Tracker update; returns (candidate_id, new status, net P&L) changes."""
        ledger = self._tracker_ledger
        before = {c.candidate_id: (e.status.value if e else None) for c, e in ledger.state()}
        if not before:
            return []
        OpportunityTrackerService(market, ledger, clock=self._clock).update()
        changes = []
        for candidate, evaluation in ledger.state():
            if evaluation is not None and evaluation.status.value != before.get(candidate.candidate_id):
                changes.append((candidate.candidate_id, evaluation.status.value, None if evaluation.net_pnl_usd is None else str(evaluation.net_pnl_usd)))
        return changes

    def _notify(self, moment: datetime, new_recs: Sequence[str], changes, alert_events) -> int:  # type: ignore[no-untyped-def]
        now = self._clock()
        queued = 0
        with connect(self._db) as conn:
            def rec(cid: str):  # type: ignore[no-untyped-def]
                return conn.execute("SELECT r.*, s.status AS strategy_status FROM recommendations r JOIN strategies s USING (strategy_id) WHERE candidate_id = ?",
                                    (cid,)).fetchone()

            for cid in new_recs:
                row = rec(cid)
                queued += outbox.queue(conn, "NUEVA_OPORTUNIDAD", f"op:{cid}", outbox.opportunity_text(row, row["strategy_status"]), now)
            for cid, status, pnl in changes:
                if status in RELEVANT_STATUSES:
                    row = rec(cid)
                    queued += outbox.queue(conn, "CAMBIO_RECOMENDACION", f"st:{cid}:{status}", outbox.status_text(row, status, pnl, row["strategy_status"]), now)
            for incident in conn.execute("SELECT kind, symbol, detail FROM monitor_incidents WHERE moment = ? AND kind IN ('FALLO_PROVEEDOR', 'DATOS_DESACTUALIZADOS')",
                                         (utc(moment),)).fetchall():
                kind = "FALLO_DATOS" if incident["kind"] == "FALLO_PROVEEDOR" else "DATOS_DESACTUALIZADOS"
                queued += outbox.queue(conn, kind, f"{kind}:{utc(moment)}:{incident['symbol']}",
                                       outbox.data_text(kind, incident["symbol"], utc(moment) or "", incident["detail"]), now)
            for event in alert_events:
                queued += outbox.queue(conn, "ALERTA_PORTAFOLIO", f"al:{event.key}:{event.transition}:{utc(event.at)}",
                                       outbox.alert_text(event.transition, event.message), now)
        return queued


