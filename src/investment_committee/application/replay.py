"""HistoricalReplayService: /simular_mes - replay the Opportunity Scanner over
a past period and compare selection policies A, B and C.

Fetches 1-hour bars once per instrument (market_intelligence), then runs
simulation.replay, which only ever shows the scanner bars closed by each
decision moment. Evidence for policy C comes only from DatedValidation
records supplied by the caller (none exist today, so C does not trade).
Blocking objections come only from AI committee ledger records written AT
the time (contemporaneous); the replay never re-asks DeepSeek and never
claims to reproduce what it would have said.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from investment_committee.market_intelligence import MarketDataError, MarketIntelligenceService
from investment_committee.simulation import DEFAULT_UNIVERSE, InstrumentProfile, RiskLimits, TrendMomentumVolatilityStrategy
from investment_committee.simulation.replay import ContemporaneousObjection, DatedValidation, ReplayConfig, ReplayReport, run_replay

from .errors import ApplicationServiceError

_logger = logging.getLogger(__name__)
YAHOO_1H_MAX_DAYS = 730
WARMUP = timedelta(days=10)  # enough closed bars for the strategy's 61-bar lookback, weekends included


class ReplayUnavailableError(ApplicationServiceError):
    """The period cannot be replayed (too old for 1-hour data, in the future,
    or no usable bars)."""


def previous_month(today: date) -> tuple[datetime, datetime]:
    """The last complete calendar month before `today`, in UTC."""
    first_this = today.replace(day=1)
    last_month_end = first_this - timedelta(days=1)
    start = last_month_end.replace(day=1)
    return datetime(start.year, start.month, 1, tzinfo=timezone.utc), datetime(first_this.year, first_this.month, 1, tzinfo=timezone.utc)


def objections_from_ai_ledger(records: Sequence[Mapping[str, Any]]) -> list[ContemporaneousObjection]:
    """AI committee records that, AT THE TIME they were written, said not to
    trade (trading stance NO_OPERAR). Only their own timestamps are used."""
    found = []
    for record in records:
        try:
            stance = record["decision"]["trading_stance"]
            symbol = record["evidence"]["instrument"]
            recorded_at = datetime.fromisoformat(record["created_at"])
        except (KeyError, TypeError, ValueError):
            continue
        if stance == "NO_OPERAR":
            found.append(ContemporaneousObjection(symbol, recorded_at, f"comité de IA {record.get('analysis_id', '?')}: postura NO OPERAR"))
    return found


class HistoricalReplayService:
    def __init__(
        self,
        market_intelligence_service: MarketIntelligenceService,
        universe: Sequence[InstrumentProfile] = DEFAULT_UNIVERSE,
        limits: RiskLimits | None = None,
        validations: Sequence[DatedValidation] = (),
        ai_records: Callable[[], Sequence[Mapping[str, Any]]] = lambda: (),
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._market = market_intelligence_service
        self._universe = tuple(universe)
        self._limits = limits or RiskLimits()
        self._validations = tuple(validations)
        self._ai_records = ai_records
        self._clock = clock

    def replay(self, start: datetime | None = None, end: datetime | None = None) -> ReplayReport:
        now = self._clock()
        if start is None or end is None:
            start, end = previous_month(now.date())
        if end > now:
            raise ReplayUnavailableError("el periodo termina en el futuro: solo se reproduce historia ya cerrada")
        days = (now - start + WARMUP).days + 1
        if days > YAHOO_1H_MAX_DAYS:
            raise ReplayUnavailableError(f"Yahoo solo ofrece velas de 1 hora de los últimos {YAHOO_1H_MAX_DAYS} días")
        strategy = TrendMomentumVolatilityStrategy()
        bars = {}
        for profile in self._universe:
            try:
                snapshot = self._market.get_intraday_history(profile.symbol, interval=strategy.interval_code, period=f"{days}d")
            except MarketDataError as exc:
                _logger.warning("replay: no data for %s: %s", profile.symbol, exc)
                bars[profile.symbol] = []
                continue
            bars[profile.symbol] = [b for b in snapshot.price_bars if start - WARMUP <= b.timestamp and b.timestamp + strategy.timeframe <= end]
        if not any(bars.values()):
            raise ReplayUnavailableError("el proveedor no devolvió velas de 1 hora para el periodo")
        config = ReplayConfig(
            start=start, end=end, universe=self._universe, limits=self._limits, validations=self._validations,
            objections=objections_from_ai_ledger(self._ai_records()),
        )
        return run_replay(bars, config, strategy, generated_at=now)
