"""TradingService: simulated intraday trading - signals, outcomes, backtests.

Composes market_intelligence (intraday bars) with simulation/ (strategy,
fills, journal, backtest). Separate from ApplicationService on purpose: a
trading signal is not the investment committee's decision, and neither
ever influences the other. Nothing here sends an order to anyone.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Mapping, Sequence

from investment_committee.market_intelligence import MarketDataError, MarketIntelligenceService
from investment_committee.simulation import (
    EURUSD,
    AccountAssumptions,
    BacktestDataError,
    SegmentedBacktest,
    InstrumentSpec,
    SimulatedTrade,
    StrategyParameters,
    TradeJournal,
    TradeJournalError,
    TradeOutcome,
    TradeSignal,
    TradeStatus,
    TradingCosts,
    evaluate_signal,
    resolve_trade,
    run_segmented_backtest,
)
from investment_committee.domain import PriceBar

from .errors import ApplicationServiceError, MarketDataUnavailableError

Clock = Callable[[], datetime]


class TradingNotAvailableError(ApplicationServiceError):
    """The symbol has no simulated-trading configuration (only EUR/USD today)."""

    def __init__(self, symbol: str, supported: Sequence[str]) -> None:
        super().__init__(f"simulated trading is not configured for {symbol}")
        self.symbol = symbol
        self.supported = tuple(supported)


class TradeJournalFailedError(ApplicationServiceError):
    """Wraps simulation.TradeJournalError."""


class BacktestUnavailableError(ApplicationServiceError):
    """The provider returned too little usable history for a backtest."""


@dataclass(frozen=True)
class TradingConfig:
    instruments: Mapping[str, InstrumentSpec] = field(default_factory=lambda: {EURUSD.symbol: EURUSD})
    parameters: StrategyParameters = field(default_factory=StrategyParameters)
    costs: TradingCosts = field(default_factory=TradingCosts)
    account: AccountAssumptions = field(default_factory=AccountAssumptions)
    signal_period: str = "30d"  # history fetched for a live signal
    backtest_period: str = "730d"  # Yahoo's maximum for 1-hour bars


@dataclass(frozen=True)
class TradeJournalView:
    """Recorded LONG/SHORT trades (most recent first) with their latest
    outcome, plus how many signals were NO_TRADE. `refreshed` is False when
    open trades could not be re-evaluated (market data unavailable)."""

    trades: Sequence[SimulatedTrade]
    no_trade_signals: int
    refreshed: bool


class TradingService:
    def __init__(
        self,
        market_intelligence_service: MarketIntelligenceService,
        journal: TradeJournal,
        config: TradingConfig | None = None,
        clock: Clock = lambda: datetime.now(timezone.utc),
        id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self._market = market_intelligence_service
        self._journal = journal
        self._config = config if config is not None else TradingConfig()
        self._clock = clock
        self._id_factory = id_factory
        self._last_backtest: SegmentedBacktest | None = None

    @property
    def config(self) -> TradingConfig:
        return self._config

    @property
    def journal(self) -> TradeJournal:
        """The simulated-trade journal (read by the opportunity scanner for
        open risk; the scanner never writes to it)."""
        return self._journal

    def propose_trade(self, symbol: str) -> TradeSignal:
        """Evaluate the strategy on the latest closed bars and record the
        signal (NO_TRADE included) in the journal."""
        instrument = self._instrument(symbol)
        bars = self._bars(instrument, self._config.signal_period)
        signal = evaluate_signal(
            bars,
            self._clock(),
            signal_id=self._id_factory(),
            instrument=instrument,
            parameters=self._config.parameters,
            costs=self._config.costs,
            account=self._config.account,
        )
        try:
            self._journal.append_signal(signal)
        except TradeJournalError as exc:
            raise TradeJournalFailedError(str(exc)) from exc
        return signal

    def preview_signal(self, symbol: str) -> TradeSignal:
        """Evaluate the strategy now WITHOUT recording anything - used as
        evidence by the AI committee, so an AI opinion never becomes a
        simulated trade."""
        instrument = self._instrument(symbol)
        bars = self._bars(instrument, self._config.signal_period)
        return evaluate_signal(
            bars,
            self._clock(),
            signal_id=f"preview-{self._id_factory()}",
            instrument=instrument,
            parameters=self._config.parameters,
            costs=self._config.costs,
            account=self._config.account,
        )

    def get_signal(self, signal_id: str) -> TradeSignal | None:
        return self._journal.get_signal(signal_id)

    def latest_signal(self) -> TradeSignal | None:
        signals = self._journal.signals()
        return signals[-1] if signals else None

    def last_backtest(self) -> SegmentedBacktest | None:
        return self._last_backtest

    def list_trades(self, limit: int = 10) -> TradeJournalView:
        """Re-evaluate open trades against fresh bars (recording any final
        outcome), then return the most recent trades."""
        now = self._clock()
        signals = self._journal.signals()
        outcomes = self._journal.final_outcomes()
        trades = [s for s in signals if s.is_trade]
        open_trades = [s for s in trades if s.signal_id not in outcomes]

        current: dict[str, TradeOutcome] = dict(outcomes)
        refreshed = True
        for symbol in sorted({s.instrument.symbol for s in open_trades}):
            pending = [s for s in open_trades if s.instrument.symbol == symbol]
            try:
                bars = self._bars(pending[0].instrument, _period_covering(pending, now))
            except MarketDataUnavailableError:
                refreshed = False
                continue
            for signal in pending:
                outcome = resolve_trade(signal, bars, evaluated_at=now)
                current[signal.signal_id] = outcome
                if outcome.status is not TradeStatus.OPEN:
                    try:
                        self._journal.append_outcome(outcome)
                    except TradeJournalError as exc:
                        raise TradeJournalFailedError(str(exc)) from exc

        recent = list(reversed(trades))[:limit]
        return TradeJournalView(
            trades=tuple(SimulatedTrade(signal=s, outcome=current.get(s.signal_id)) for s in recent),
            no_trade_signals=len(signals) - len(trades),
            refreshed=refreshed,
        )

    def backtest(self, symbol: str) -> SegmentedBacktest:
        """Full period plus 60/20/20 development/validation/final-test
        segments, each from a fresh account. Strategy parameters are fixed
        (v1.0.0) - nothing is tuned on any segment."""
        instrument = self._instrument(symbol)
        bars = self._bars(instrument, self._config.backtest_period)
        try:
            result = run_segmented_backtest(
                bars,
                instrument=instrument,
                parameters=self._config.parameters,
                costs=self._config.costs,
                account=self._config.account,
                generated_at=self._clock(),
            )
        except BacktestDataError as exc:
            raise BacktestUnavailableError(str(exc)) from exc
        self._last_backtest = result
        return result

    def _instrument(self, symbol: str) -> InstrumentSpec:
        instrument = self._config.instruments.get(symbol.strip().upper())
        if instrument is None:
            supported = [spec.display_name for spec in self._config.instruments.values()]
            raise TradingNotAvailableError(symbol.strip().upper(), supported)
        return instrument

    def _bars(self, instrument: InstrumentSpec, period: str) -> Sequence[PriceBar]:
        try:
            snapshot = self._market.get_intraday_history(
                instrument.symbol, interval=self._config.parameters.interval_code, period=period
            )
        except MarketDataError as exc:
            raise MarketDataUnavailableError(str(exc)) from exc
        return snapshot.price_bars


def _period_covering(signals: Sequence[TradeSignal], now: datetime) -> str:
    oldest = min(s.data_as_of for s in signals if s.data_as_of is not None)
    days = min(max((now - oldest).days + 2, 2), 730)
    return f"{days}d"
