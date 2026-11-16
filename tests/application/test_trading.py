"""Tests for application.trading.TradingService and application.learning.LearningService.

Market data is a fake MarketDataProvider - no network.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Sequence

import pytest

from investment_committee.application import (
    BacktestUnavailableError,
    DecisionQueryService,
    LearningService,
    LessonCatalog,
    MarketDataUnavailableError,
    TradeSignalNotFoundError,
    TradingNotAvailableError,
    TradingService,
)
from investment_committee.domain import Asset, AssetType, MarketSnapshot, PriceBar
from investment_committee.education import Lesson
from investment_committee.market_intelligence import MarketDataError, MarketIntelligenceService
from investment_committee.market_intelligence.providers.base import MarketDataProvider
from investment_committee.memory import InMemoryDecisionLedger
from investment_committee.simulation import (
    InMemoryTradeJournal,
    JsonLinesTradeJournal,
    TradeDirection,
    TradeStatus,
)

from tests.simulation._support import bars_from_moves, closing_time, regime_series, sideways, uptrend


class FakeIntradayProvider(MarketDataProvider):
    def __init__(self, bars: Sequence[PriceBar], fail: bool = False) -> None:
        self.bars = list(bars)
        self.fail = fail
        self.requests: list[tuple[str, str, str]] = []

    def get_daily_history(self, ticker: str) -> MarketSnapshot:
        raise AssertionError("trading never asks for daily bars")

    def get_intraday_history(self, ticker: str, interval: str, period: str) -> MarketSnapshot:
        self.requests.append((ticker, interval, period))
        if self.fail:
            raise MarketDataError("provider down")
        asset = Asset(ticker=ticker, name=None, asset_type=AssetType.STOCK, exchange=None, currency=None)
        return MarketSnapshot(asset=asset, generated_at=self.bars[-1].timestamp, price_bars=self.bars)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _service(bars: Sequence[PriceBar], now: datetime, journal=None, fail: bool = False):  # type: ignore[no-untyped-def]
    provider = FakeIntradayProvider(bars, fail=fail)
    ids = iter(f"sig-{i}" for i in range(1, 100))
    clock = Clock(now)
    service = TradingService(
        MarketIntelligenceService(provider),
        journal if journal is not None else InMemoryTradeJournal(),
        clock=clock,
        id_factory=lambda: next(ids),
    )
    return service, provider, clock


def test_propose_trade_records_a_long_signal_from_hourly_bars() -> None:
    bars = uptrend()
    service, provider, _ = _service(bars, closing_time(bars))

    signal = service.propose_trade("EURUSD=X")

    assert signal.direction is TradeDirection.LONG
    assert provider.requests == [("EURUSD=X", "1h", "30d")]
    assert service.latest_signal() == signal


def test_no_trade_signals_are_recorded_too() -> None:
    bars = sideways()
    service, _, _ = _service(bars, closing_time(bars))

    signal = service.propose_trade("EURUSD=X")
    view = service.list_trades()

    assert signal.direction is TradeDirection.NO_TRADE
    assert view.trades == () and view.no_trade_signals == 1


def test_only_configured_instruments_can_be_traded() -> None:
    service, provider, _ = _service(uptrend(), closing_time(uptrend()))

    with pytest.raises(TradingNotAvailableError) as raised:
        service.propose_trade("AAPL")
    assert raised.value.supported == ("EUR/USD",)
    assert provider.requests == []


def test_provider_failure_becomes_market_data_unavailable() -> None:
    service, _, _ = _service(uptrend(), closing_time(uptrend()), fail=True)

    with pytest.raises(MarketDataUnavailableError):
        service.propose_trade("EURUSD=X")


def test_list_trades_resolves_open_trades_once_bars_arrive_and_records_the_outcome(tmp_path: Path) -> None:
    history = uptrend()
    journal = JsonLinesTradeJournal(tmp_path / "trades.jsonl")
    service, provider, clock = _service(history, closing_time(history), journal=journal)
    signal = service.propose_trade("EURUSD=X")

    assert service.list_trades().trades[0].outcome.status is TradeStatus.OPEN  # type: ignore[union-attr]

    rally = bars_from_moves(["0.0010"] * 6, start=closing_time(history), first_close=str(history[-1].close))
    provider.bars = history + rally
    clock.now = closing_time(rally)
    view = service.list_trades()

    outcome = view.trades[0].outcome
    assert outcome is not None and outcome.status is TradeStatus.TAKE_PROFIT
    assert view.refreshed
    assert JsonLinesTradeJournal(tmp_path / "trades.jsonl").final_outcomes()[signal.signal_id] == outcome


def test_list_trades_still_lists_when_data_is_unavailable() -> None:
    history = uptrend()
    service, provider, _ = _service(history, closing_time(history))
    service.propose_trade("EURUSD=X")
    provider.fail = True

    view = service.list_trades()

    assert len(view.trades) == 1 and view.trades[0].outcome is None
    assert view.refreshed is False


def test_backtest_uses_two_years_of_hourly_bars() -> None:
    bars = regime_series()
    service, provider, _ = _service(bars, closing_time(bars))

    result = service.backtest("EURUSD=X")

    assert provider.requests == [("EURUSD=X", "1h", "730d")]
    assert result.full.metrics.trades > 0
    assert [segment.label for segment in result.segments] == ["desarrollo", "validación", "prueba final"]
    assert service.last_backtest() == result


def test_backtest_with_too_little_data_is_reported() -> None:
    bars = uptrend(30)
    service, _, _ = _service(bars, closing_time(bars))

    with pytest.raises(BacktestUnavailableError):
        service.backtest("EURUSD=X")


# --- LearningService ---


def _learning(bars: Sequence[PriceBar]) -> tuple[LearningService, TradingService]:
    trading, _, _ = _service(bars, closing_time(bars))
    return LearningService(trading, DecisionQueryService(InMemoryDecisionLedger())), trading


def test_lesson_uses_the_recorded_signal_values() -> None:
    learning, trading = _learning(uptrend())
    signal = trading.propose_trade("EURUSD=X")

    lesson = learning.lesson("RSI", trade_signal_id=signal.signal_id)

    assert isinstance(lesson, Lesson)
    assert lesson.concept_key == "rsi"
    assert lesson.current_value is not None and "RSI14 = 55,6" in lesson.current_value
    assert lesson.asset_label == "EUR/USD"


def test_lesson_without_any_analysis_is_general_and_marked_unavailable() -> None:
    learning, _ = _learning(uptrend())

    lesson = learning.lesson("stop-loss")

    assert isinstance(lesson, Lesson) and lesson.concept_key == "entrada_stop_take"
    assert lesson.current_value is None and lesson.for_this_asset is None


@pytest.mark.parametrize("query", ["", "astrologia"])
def test_unknown_or_empty_concept_returns_the_catalog(query: str) -> None:
    learning, _ = _learning(uptrend())

    catalog = learning.lesson(query)

    assert isinstance(catalog, LessonCatalog)
    assert len(catalog.entries) == 12
    assert catalog.unknown_query == (query or None)


def test_explain_trade_covers_the_trading_concepts_with_values() -> None:
    learning, trading = _learning(uptrend())
    trading.propose_trade("EURUSD=X")

    explanation = learning.explain_trade()

    keys = [lesson.concept_key for lesson in explanation.lessons]
    assert keys[:3] == ["tendencia", "medias_moviles", "rsi"]
    assert all(lesson.current_value is not None for lesson in explanation.lessons)


def test_explain_trade_without_signals_raises() -> None:
    learning, _ = _learning(uptrend())

    with pytest.raises(TradeSignalNotFoundError):
        learning.explain_trade()


def test_trading_never_touches_the_committee_ledger() -> None:
    ledger = InMemoryDecisionLedger()
    trading, _, _ = _service(uptrend(), closing_time(uptrend()))
    LearningService(trading, DecisionQueryService(ledger))

    trading.propose_trade("EURUSD=X")

    assert ledger.list_all() == ()
