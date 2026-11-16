"""LearningService: beginner lessons grounded in recorded analyses.

Builds education.LessonContext values from an already-recorded trading
signal or committee decision - never from a new fetch or calculation - and
asks education/ for the lesson text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from decimal import Decimal
from typing import Sequence

from investment_committee.education import (
    TRADE_LESSON_KEYS,
    Lesson,
    LessonContext,
    build_lesson,
    concept_titles,
    find_concept,
)
from investment_committee.memory import DecisionRecord, DecisionRecordId
from investment_committee.simulation import SegmentedBacktest, TradeSignal

from .decision_query import DecisionQueryService
from .errors import ApplicationServiceError
from .trading import TradingService

# Concepts that only the committee's daily analysis computes.
_COMMITTEE_FIRST = frozenset({"confianza", "drawdown"})


@dataclass(frozen=True)
class LessonCatalog:
    """The list of available concepts - returned for an empty or unknown
    /aprender query instead of guessing which concept was meant."""

    entries: Sequence[tuple[str, str]]  # (key, title)
    unknown_query: str | None


@dataclass(frozen=True)
class TradeExplanation:
    signal: TradeSignal
    lessons: Sequence[Lesson]


class TradeSignalNotFoundError(ApplicationServiceError):
    """No simulated-trading signal has been recorded yet."""


class LearningService:
    def __init__(self, trading_service: TradingService, decision_query_service: DecisionQueryService) -> None:
        self._trading = trading_service
        self._decisions = decision_query_service

    def lesson(
        self,
        query: str,
        trade_signal_id: str | None = None,
        record_id: DecisionRecordId | None = None,
    ) -> Lesson | LessonCatalog:
        key = find_concept(query) if query.strip() else None
        if key is None:
            return LessonCatalog(entries=concept_titles(), unknown_query=query.strip() or None)

        contexts: list[LessonContext] = []
        signal = self._trading.get_signal(trade_signal_id) if trade_signal_id else None
        record = self._record(record_id)
        if signal is not None:
            contexts.append(signal_context(signal))
        if record is not None:
            contexts.append(record_context(record))
        backtest = self._trading.last_backtest()
        if backtest is not None and key == "drawdown":
            contexts.append(backtest_context(backtest))
        if key in _COMMITTEE_FIRST:
            contexts.reverse()

        for context in contexts:
            lesson = build_lesson(key, context)
            if lesson.current_value is not None:
                return lesson
        return build_lesson(key, contexts[0] if contexts else None)

    def explain_trade(self, signal_id: str | None = None) -> TradeExplanation:
        signal = self._trading.get_signal(signal_id) if signal_id else None
        if signal is None:
            signal = self._trading.latest_signal()
        if signal is None:
            raise TradeSignalNotFoundError("no simulated trading signal has been recorded yet")
        context = signal_context(signal)
        return TradeExplanation(signal=signal, lessons=tuple(build_lesson(key, context) for key in TRADE_LESSON_KEYS))

    def _record(self, record_id: DecisionRecordId | None) -> DecisionRecord | None:
        if record_id is None:
            return None
        try:
            return self._decisions.get(record_id)
        except ApplicationServiceError:
            return None


def signal_context(signal: TradeSignal) -> LessonContext:
    reading, plan = signal.reading, signal.plan
    values: dict[str, Decimal | str] = {
        "direction": signal.direction.value,
        "spread_pips": signal.costs.spread_pips,
        "pip_size": signal.instrument.pip_size,
        "capital": signal.account.capital,
        "timeframe": "1 hora" if signal.timeframe.total_seconds() == 3600 else str(signal.timeframe),
    }
    optional = {
        "price": reading.quote_price,
        "sma20": reading.sma_fast,
        "sma50": reading.sma_slow,
        "rsi14": reading.rsi,
        "atr_pips": reading.atr_pips,
        "support": reading.support,
        "resistance": reading.resistance,
        "trend": reading.trend.value if reading.trend is not None else None,
    }
    if plan is not None:
        optional.update(
            entry=plan.entry_price,
            stop_loss=plan.stop_loss,
            take_profit=plan.take_profit,
            risk_pips=plan.risk_pips,
            reward_pips=plan.reward_pips,
            gross_reward_risk=plan.gross_reward_risk,
            net_reward_risk=plan.net_reward_risk,
            units=Decimal(plan.units),
            notional=plan.notional,
            margin=plan.margin_required,
            leverage=plan.leverage,
            risk_amount=plan.risk_amount,
            reward_amount=plan.reward_amount,
        )
    values.update({k: v for k, v in optional.items() if v is not None})
    as_of = f" con datos hasta {signal.data_as_of.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC" if signal.data_as_of else ""
    return LessonContext(
        asset_label=signal.instrument.display_name,
        source=f"la señal de trading simulado de {signal.instrument.display_name}{as_of}",
        values=values,
    )


def record_context(record: DecisionRecord) -> LessonContext:
    snapshot = record.indicator_snapshot
    optional = {
        "price": snapshot.latest_close,
        "sma20": snapshot.sma_20,
        "sma50": snapshot.sma_50,
        "sma200": snapshot.sma_200,
        "rsi14": snapshot.rsi_14,
        "annualized_volatility": snapshot.annualized_volatility,
        "max_drawdown": snapshot.maximum_drawdown,
        "committee_confidence": record.decision.confidence,
    }
    values: dict[str, Decimal | str] = {k: v for k, v in optional.items() if v is not None}
    values["timeframe"] = "1 día"
    return LessonContext(
        asset_label=record.asset.ticker,
        source=f"el análisis del comité de {record.asset.ticker} del {record.recorded_at:%Y-%m-%d %H:%M} UTC",
        values=values,
    )


def backtest_context(segmented: SegmentedBacktest) -> LessonContext:
    result = segmented.full
    return LessonContext(
        asset_label=result.instrument.display_name,
        source=f"el backtest de {result.instrument.display_name} del {result.generated_at.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC",
        values={"backtest_max_drawdown": result.metrics.max_drawdown},
    )
