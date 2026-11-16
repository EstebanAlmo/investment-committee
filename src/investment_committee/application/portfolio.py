"""PortfolioGuardianService: /portafolio and /alertas.

Monitors positions; never sends an order and never closes anything at a
broker (there is none). Positions: those registered by hand (PositionStore)
plus open trades of the simulated-trade journal. Prices: the last CLOSED
1-hour bar for Forex (and gold futures, only if allowed as a proxy); the
last daily bar for stocks and ETFs. check() advances the alerts' state
machines and persists them; it runs only when asked (/alertas or
scripts/portfolio.py alertas) - nothing is scheduled by default.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Sequence

from investment_committee.market_intelligence import MarketDataError, MarketIntelligenceService
from investment_committee.portfolio.guardian import (
    AlertEvent,
    AlertRules,
    AlertState,
    AssetKind,
    PortfolioPosition,
    Quote,
    Side,
    Valuation,
    check_alerts,
    usd_rates,
    value_position,
)
from investment_committee.portfolio.store import AlertStore, PortfolioStoreError, PositionStore
from investment_committee.simulation import TradeJournal, closed_bars
from investment_committee.simulation.sessions import is_week_open

from .errors import ApplicationServiceError

_logger = logging.getLogger(__name__)
HOUR = timedelta(hours=1)
GOLD_SYMBOLS = ("XAUUSD", "XAUUSD=X", "XAU/USD")
GOLD_PROXY = "GC=F"


class PortfolioUnavailableError(ApplicationServiceError):
    """The portfolio or alert store could not be read or written."""


@dataclass(frozen=True)
class GuardianSettings:
    rules: AlertRules = field(default_factory=AlertRules)
    allow_gold_futures_proxy: bool = False
    fx_max_age: timedelta = timedelta(hours=3)
    equity_max_age: timedelta = timedelta(days=4)


@dataclass(frozen=True)
class PortfolioView:
    valuations: Sequence[Valuation]
    total_exposure_usd: Decimal
    total_pnl_usd: Decimal
    unavailable: int
    as_of: datetime
    market_open: bool


@dataclass(frozen=True)
class AlertCheck:
    view: PortfolioView
    events: Sequence[AlertEvent]
    states: Sequence[AlertState]
    rules: AlertRules


class PortfolioGuardianService:
    def __init__(
        self,
        market_intelligence_service: MarketIntelligenceService,
        positions: PositionStore,
        alerts: AlertStore,
        journal: TradeJournal | None = None,
        settings: GuardianSettings | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._market = market_intelligence_service
        self._positions = positions
        self._alerts = alerts
        self._journal = journal
        self._settings = settings or GuardianSettings()
        self._clock = clock

    def positions(self) -> list[PortfolioPosition]:
        try:
            manual = self._positions.open_positions()
        except PortfolioStoreError as exc:
            raise PortfolioUnavailableError(str(exc)) from exc
        return [*manual, *self._simulated()]

    def view(self) -> PortfolioView:
        now = self._clock()
        positions = self.positions()
        quotes = self._quotes(positions, now)
        rates = usd_rates(quotes)
        valuations = []
        for p in positions:
            max_age = self._settings.equity_max_age if p.kind is AssetKind.EQUITY else self._settings.fx_max_age
            quote = quotes.get(_price_symbol(p))
            valuation = value_position(p, quote, rates, now, max_age)
            if p.kind is AssetKind.METAL and quote is None and not self._settings.allow_gold_futures_proxy:
                valuation = Valuation(p, False, "Yahoo no publica XAU/USD spot; GC=F es un futuro distinto y su uso como aproximación está desactivado")
            if p.kind is not AssetKind.EQUITY and valuation.stale and not is_week_open(now):
                valuation = Valuation(**{**valuation.__dict__, "stale": False})  # weekend: old prices are expected
            valuations.append(valuation)
        available = [v for v in valuations if v.available]
        return PortfolioView(
            valuations=valuations,
            total_exposure_usd=sum((v.exposure_usd or Decimal("0") for v in available), Decimal("0")),
            total_pnl_usd=sum((v.pnl_usd or Decimal("0") for v in available), Decimal("0")),
            unavailable=len(valuations) - len(available),
            as_of=now,
            market_open=is_week_open(now),
        )

    def check(self) -> AlertCheck:
        view = self.view()
        try:
            states = self._alerts.load()
            events, new_states = check_alerts(view.valuations, self._settings.rules, states, view.as_of)
            self._alerts.save(new_states, events)
        except PortfolioStoreError as exc:
            raise PortfolioUnavailableError(str(exc)) from exc
        return AlertCheck(view, events, sorted(new_states.values(), key=lambda s: s.key), self._settings.rules)

    def _simulated(self) -> list[PortfolioPosition]:
        """Open trades of the simulated journal, at their reference entry."""
        if self._journal is None:
            return []
        finals = self._journal.final_outcomes()
        found = []
        for signal in self._journal.signals():
            if signal.plan is None or signal.signal_id in finals or signal.data_as_of is None:
                continue
            spec = signal.instrument
            letters = spec.symbol.replace("=X", "")
            found.append(PortfolioPosition(
                position_id=f"sim-{signal.signal_id[:8]}", instrument=spec.symbol, kind=AssetKind.FOREX,
                side=Side.LONG if signal.direction.value == "LONG" else Side.SHORT, quantity=Decimal(signal.plan.units),
                entry_price=signal.plan.entry_price, currency=spec.quote_currency, opened_at=signal.data_as_of,
                stop=signal.plan.stop_loss, leverage=signal.plan.leverage, source="SIMULADA", base_currency=letters[:3],
            ))
        return found

    def _quotes(self, positions: Sequence[PortfolioPosition], now: datetime) -> dict[str, Quote]:
        wanted = {_price_symbol(p): p.kind for p in positions}
        # rates for non-USD currencies, through the USD pair Yahoo lists
        for p in positions:
            if p.currency != "USD" and not (p.kind is AssetKind.FOREX and p.base_currency == "USD"):
                wanted.setdefault(f"USD{p.currency}=X", AssetKind.FOREX)
        quotes: dict[str, Quote] = {}
        for symbol, kind in wanted.items():
            if symbol in GOLD_SYMBOLS:
                if not self._settings.allow_gold_futures_proxy:
                    continue
                quote = self._intraday(GOLD_PROXY, now)
                if quote is not None:
                    quotes[symbol] = Quote(symbol, quote.price, quote.as_of, quote.source, "precio de futuros COMEX GC=F, no spot")
                continue
            quote = self._daily(symbol) if kind is AssetKind.EQUITY else self._intraday(symbol, now)
            if quote is not None:
                quotes[symbol] = quote
        return quotes

    def _intraday(self, symbol: str, now: datetime) -> Quote | None:
        try:
            bars = closed_bars(self._market.get_intraday_history(symbol, interval="1h", period="5d").price_bars, HOUR, now)
        except MarketDataError:
            return None
        if not bars:
            return None
        return Quote(symbol, bars[-1].close, bars[-1].timestamp + HOUR, "Yahoo Finance · última vela de 1 hora cerrada")

    def _daily(self, symbol: str) -> Quote | None:
        try:
            snapshot = self._market.get_daily_history(symbol)
        except MarketDataError:
            return None
        last = snapshot.latest_price_bar
        return Quote(symbol, last.close, last.timestamp, "Yahoo Finance · último precio diario (puede ser la sesión en curso)")


def _price_symbol(position: PortfolioPosition) -> str:
    return position.instrument
