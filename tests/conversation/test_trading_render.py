"""Tests for conversation.trading_render presentation details."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta, timezone

from investment_committee.conversation.trading_render import render_backtest, render_trade_signal
from decimal import Decimal

from investment_committee.application import SegmentedBacktest
from investment_committee.simulation import (
    EURUSD,
    AccountAssumptions,
    AccountStatus,
    RejectionReason,
    StrategyParameters,
    TradingCosts,
    run_segmented_backtest,
)

from tests.simulation._support import closing_time, regime_series, signal_for, uptrend


def test_times_are_shown_in_utc_even_when_the_provider_uses_another_offset() -> None:
    london = timezone(timedelta(hours=1))
    bars = [replace(bar, timestamp=bar.timestamp.astimezone(london)) for bar in uptrend()]

    text = render_trade_signal(signal_for(bars))

    expected = f"Datos hasta: {closing_time(uptrend()):%Y-%m-%d %H:%M} UTC"
    assert expected in text


def _segmented() -> SegmentedBacktest:
    bars = regime_series()
    return run_segmented_backtest(
        bars, instrument=EURUSD, parameters=StrategyParameters(), costs=TradingCosts(),
        account=AccountAssumptions(), generated_at=closing_time(bars),
    )


def test_backtest_summary_shows_capital_costs_and_periods() -> None:
    text = render_backtest(_segmented())

    for fragment in (
        "Capital inicial: 10.000,00 USD → saldo final:", "rentabilidad neta", "Estado final de la cuenta: activa",
        "Operaciones:", "ganadoras", "perdedoras", "Factor de beneficio:", "expectativa por operación",
        "Drawdown máximo:", "Costos:", "ya incluidos en el resultado", "Por periodos",
        "- Desarrollo (60 %", "- Validación (20 %", "- Prueba final (20 %", "muestra insuficiente",
        "Drawdown:", "Quedarse sin margen", "Un backtest rentable tampoco garantiza nada",
        "entrada en la apertura de la siguiente", "Esto NO son ganancias reales",
    ):
        assert fragment in text, fragment


def test_backtest_reports_suspension_rejections_and_debt() -> None:
    segmented = _segmented()
    full = segmented.full
    broke = replace(
        full,
        status=AccountStatus.NEGATIVE_BALANCE,
        final_balance=Decimal("-250.00"),
        suspended_at=full.data_end,
        metrics=replace(full.metrics, rejections={RejectionReason.INSUFFICIENT_MARGIN: 3, RejectionReason.TRADING_SUSPENDED: 7}),
    )

    text = render_backtest(replace(segmented, full=broke))

    assert "saldo negativo: las pérdidas superaron el dinero de la cuenta" in text
    assert "Deuda simulada: 250,00 USD" in text
    assert "Señales no ejecutadas: 3 margen insuficiente · 7 velas con señal mientras el trading estaba suspendido." in text
