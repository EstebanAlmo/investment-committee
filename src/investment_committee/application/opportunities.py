"""OpportunityScannerService: /oportunidades - deterministic day-trading scan.

For each configured instrument: fetch 1-hour bars (market_intelligence),
keep closed bars only, run the strategy, convert money to USD, apply the
Python risk controls, rank, and record the scan (memory). The AI
committee is OPTIONAL and OFF by default; when enabled it may only object
to ranked candidates, at most AIReviewSettings.max_reviews paid analyses
per scan. Nothing here opens a simulated trade or contacts a broker.

A failing instrument (no data, malformed data, unexpected error) is
recorded as a partial error; the rest of the scan continues.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Mapping, Sequence

from investment_committee.market_intelligence import MarketDataError, MarketIntelligenceService
from investment_committee.simulation import (
    DEFAULT_UNIVERSE,
    NOT_VALIDATED,
    SCANNER_VERSION,
    AccountState,
    AIReview,
    AIReviewSettings,
    Category,
    HistoricalValidation,
    InstrumentProfile,
    InstrumentScan,
    OpenPosition,
    OpportunityLedgerError,
    OpportunityScan,
    OpportunityScanLedger,
    RiskLimits,
    ScanSettings,
    ScanStrategy,
    TradeDirection,
    TradeJournal,
    TrendMomentumVolatilityStrategy,
    apply_ai_objection,
    assess_instrument,
    prepare_bars,
    rank_and_allocate,
)

from investment_committee.simulation.tracker import TrackerLedger, TrackerLedgerError, candidates_from_scan

from .ai_committee import AICommitteeService
from .errors import ApplicationServiceError

_logger = logging.getLogger(__name__)

DATA_SOURCE = "Yahoo Finance (yfinance) · velas de 1 hora · precios medios indicativos, sin bid/ask"


class OpportunityScanRecordingError(ApplicationServiceError):
    """The scan ran but could not be written to its audit ledger."""


@dataclass(frozen=True)
class ScannerConfig:
    universe: Sequence[InstrumentProfile] = DEFAULT_UNIVERSE
    limits: RiskLimits = field(default_factory=RiskLimits)
    settings: ScanSettings = field(default_factory=ScanSettings)
    ai: AIReviewSettings = field(default_factory=AIReviewSettings)
    # (strategy name, version, symbol) -> evidence; empty: nothing validated
    validations: Mapping[tuple[str, str, str], HistoricalValidation] = field(default_factory=dict)
    data_source: str = DATA_SOURCE

    def __post_init__(self) -> None:
        symbols = [p.symbol for p in self.universe]
        if not symbols:
            raise ValueError("the universe must not be empty")
        if len(set(symbols)) != len(symbols):
            raise ValueError("the universe has a repeated symbol")
        if len(symbols) > self.settings.max_instruments:
            raise ValueError(f"the universe has {len(symbols)} instruments; the limit is {self.settings.max_instruments}")


AccountProvider = Callable[[datetime], AccountState]


class OpportunityScannerService:
    def __init__(
        self,
        market_intelligence_service: MarketIntelligenceService,
        ledger: OpportunityScanLedger,
        config: ScannerConfig | None = None,
        strategy: ScanStrategy | None = None,
        account_provider: AccountProvider | None = None,
        ai_committee: AICommitteeService | None = None,
        tracker: TrackerLedger | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
        id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self._market = market_intelligence_service
        self._ledger = ledger
        self._config = config or ScannerConfig()
        self._strategy = strategy or TrendMomentumVolatilityStrategy()
        self._account_provider = account_provider or (lambda now: AccountState.fresh(self._config.limits.capital))
        self._ai = ai_committee
        self._tracker = tracker
        self._clock = clock
        self._monotonic = monotonic
        self._id_factory = id_factory

    @property
    def config(self) -> ScannerConfig:
        return self._config

    def scan(self, *, ai_reviews: int | None = None) -> OpportunityScan:
        """`ai_reviews` overrides config.ai.max_reviews for this scan (0 =
        no AI); it can never exceed AIReviewSettings' own cap."""
        max_reviews = AIReviewSettings(self._config.ai.max_reviews if ai_reviews is None else ai_reviews).max_reviews
        now = self._clock()
        started = self._monotonic()
        scan_id = self._id_factory()
        account = self._account_provider(now)
        limits, settings, strategy = self._config.limits, self._config.settings, self._strategy

        errors: list[str] = []
        scans: list[InstrumentScan] = []
        for index, profile in enumerate(self._config.universe):
            validation = self._config.validations.get((strategy.name, strategy.version, profile.symbol), NOT_VALIDATED)
            if self._monotonic() - started > settings.time_budget.total_seconds():
                message = f"{profile.spec.display_name}: omitido, se agotó el tiempo máximo del escaneo"
                errors.append(message)
                scans.append(_failed(profile, validation, message))
                continue
            try:
                snapshot = self._market.get_intraday_history(profile.symbol, interval=strategy.interval_code, period=strategy.history_period)
                bars = list(snapshot.price_bars)[-settings.max_bars_per_instrument:]
                closed, data = prepare_bars(bars, strategy.timeframe, now, self._config.data_source)
                scans.append(
                    assess_instrument(
                        profile, closed, data, now, strategy=strategy, limits=limits, account=account,
                        validation=validation, signal_id=f"{scan_id}-{index + 1}",
                    )
                )
            except MarketDataError as exc:
                message = f"{profile.spec.display_name}: sin datos de mercado utilizables ({_short(exc)})"
                errors.append(message)
                scans.append(_failed(profile, validation, message))
            except Exception as exc:  # noqa: BLE001 - one instrument must not take the whole scan down
                _logger.exception("opportunity scan: unexpected failure on %s", profile.symbol)
                message = f"{profile.spec.display_name}: error inesperado ({type(exc).__name__})"
                errors.append(message)
                scans.append(_failed(profile, validation, message))

        scans = rank_and_allocate(scans, limits, account)
        used = 0
        if max_reviews > 0:
            scans, used, ai_errors = self._ai_review(scans, max_reviews, limits, account)
            errors.extend(ai_errors)

        result = OpportunityScan(
            scan_id=scan_id,
            started_at=now,
            finished_at=self._clock(),
            scanner_version=SCANNER_VERSION,
            strategy_name=strategy.name,
            strategy_version=strategy.version,
            timeframe=strategy.timeframe,
            data_source=self._config.data_source,
            universe=[p.symbol for p in self._config.universe],
            limits=limits,
            account=account,
            instruments=scans,
            errors=errors,
            ai_review_enabled=max_reviews > 0,
            ai_reviews_used=used,
        )
        try:
            self._ledger.append(result)
        except OpportunityLedgerError as exc:
            raise OpportunityScanRecordingError(f"failed to record opportunity scan {scan_id}: {exc}") from exc
        if self._tracker is not None:
            # Observed for prospective follow-up (/seguimiento); never a trade.
            try:
                self._tracker.observe(candidates_from_scan(result), now)
            except TrackerLedgerError:
                _logger.exception("opportunity scan %s: candidates could not be added to the tracker", scan_id)
        _logger.info(
            "opportunity scan %s: instruments=%d signals=%d candidates=%d ranked=%d errors=%d ai_reviews=%d",
            scan_id, len(scans), result.technical_signals, len(result.candidates), len(result.ranked), len(errors), used,
        )
        return result

    def _ai_review(
        self, scans: list[InstrumentScan], max_reviews: int, limits: RiskLimits, account: AccountState
    ) -> tuple[list[InstrumentScan], int, list[str]]:
        """Review ranked candidates only, best first, at most `max_reviews`.
        An objection discards; the rest is re-ranked without new calls."""
        if self._ai is None or not self._ai.available:
            reason = self._ai.unavailable_reason if self._ai is not None else "no está configurado"
            return scans, 0, [f"revisión del comité de IA pedida pero no disponible: {reason}"]
        by_symbol = {s.symbol: s for s in scans}
        used, errors = 0, []
        for candidate in sorted((s for s in scans if s.rank is not None), key=lambda s: s.rank or 0)[:max_reviews]:
            try:
                report = self._ai.analyze(candidate.symbol)
            except ApplicationServiceError as exc:
                errors.append(f"{candidate.display_name}: el comité de IA no pudo revisar la candidata ({type(exc).__name__})")
                continue
            used += 1
            by_symbol[candidate.symbol] = apply_ai_objection(candidate, _review(candidate, report))
        reviewed = [replace(by_symbol[s.symbol], rank=None) for s in scans]
        return rank_and_allocate(reviewed, limits, account), used, errors


def _review(candidate: InstrumentScan, report) -> AIReview:  # type: ignore[no-untyped-def]
    """The AI supports a candidate only with a directional conclusion that
    agrees with it (FAVORABLE for a LONG, DESFAVORABLE for a SHORT);
    anything else - NEUTRAL, NO_CONCLUYENTE, a contrary view - is an
    objection. It can only remove, never authorize."""
    conclusion = report.decision.conclusion
    agrees = (candidate.technical_direction is TradeDirection.LONG and conclusion == "FAVORABLE") or (
        candidate.technical_direction is TradeDirection.SHORT and conclusion == "DESFAVORABLE"
    )
    detail = f"conclusión {conclusion.replace('_', ' ')} (análisis {report.analysis_id})"
    return AIReview(analysis_id=report.analysis_id, objected=not agrees, detail=detail)


def _failed(profile: InstrumentProfile, validation: HistoricalValidation, message: str) -> InstrumentScan:
    return InstrumentScan(
        symbol=profile.symbol, display_name=profile.spec.display_name, category=Category.DATA_ERROR,
        technical_direction=TradeDirection.NO_TRADE, validation=validation, error=message,
    )


def _short(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text if len(text) <= 160 else text[:159] + "…"


def account_state_from_journal(journal: TradeJournal, capital: Decimal, now: datetime) -> AccountState:
    """The hypothetical account from the simulated-trade journal: closed
    outcomes give equity and today's realized P&L (UTC day); trades without
    a final outcome count as open risk at their full planned risk - also
    when past expiry and not yet refreshed (conservative; /operaciones
    refreshes them). Only USD-quoted journal entries are counted in USD."""
    day_start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    outcomes = journal.final_outcomes()
    realized_total = realized_today = Decimal("0")
    open_positions: list[OpenPosition] = []
    notes: list[str] = []
    for signal in journal.signals():
        if signal.plan is None:
            continue
        if signal.instrument.quote_currency != "USD":
            notes.append(f"{signal.instrument.display_name}: operación del diario no cotizada en USD, no se contabiliza")
            continue
        outcome = outcomes.get(signal.signal_id)
        if outcome is None:
            side = -1 if signal.direction.value == "LONG" else 1  # USD-quoted pair: LONG sells USD
            open_positions.append(OpenPosition(signal.instrument.symbol, signal.plan.risk_amount, signal.plan.margin_required, usd_direction=side))
            if signal.plan.expires_at <= now:
                notes.append(f"{signal.instrument.display_name}: operación vencida sin resultado registrado; se cuenta como riesgo abierto (usa /operaciones)")
            continue
        pnl = outcome.pnl_amount or Decimal("0")
        realized_total += pnl
        if outcome.exit_time is not None and outcome.exit_time >= day_start:
            realized_today += pnl
    equity = capital + realized_total
    return AccountState(equity=equity, day_start_equity=equity - realized_today, realized_pnl_today=realized_today, open_positions=open_positions, notes=notes)

