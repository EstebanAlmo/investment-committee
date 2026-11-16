"""A small synthetic study on disk, written by the real engine and the real
write_study, for the trade viewer tests. Made-up bars and made-up
strategies; nothing here is market data.

Strategy alfa (EUR/USD unless said; T0 = Tuesday 2026-12-01 14:00 UTC):
  T0      LONG   stopped inside bar T0+3                      PERDEDORA, dentro de la vela
  T0+1    LONG   GBP/USD, quiet: leaves after 72 bars         TIEMPO (open at the end in the short study)
  T0+5    SHORT  target inside bar T0+6                       GANADORA, dentro de la vela
  T0+6    LONG   rejected: POSICION_ABIERTA
  T0+10   LONG   stop and target inside bar T0+11             INDETERMINADA (misma vela)
  T0+13   LONG   bar T0+15 is missing                         INDETERMINADA (hueco de datos)
  T0+18   LONG   rejected: LIMITE_PERDIDA_DIARIA
  T0+36   LONG   bar T0+38 opens below the stop               STOP con gap, en la apertura
  T0+60   LONG   bar T0+62 opens above the target             OBJETIVO en la apertura
  T0+66   SHORT  quiet until Friday 16:00 New York            CIERRE_SEMANAL
  the evaluation at T0+30 is rejected for its data: the indicator lines break there
Strategy beta: SHORT at T0+5 (the same instant as alfa) and SHORT at T0+36 (alfa goes LONG).

Indicators are made up but only from closed bars: ema_fast = mean of the
last 2 closes, ema_slow = mean of the last 3, rsi = 40 + (hours from T0) mod 20.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Sequence

import pytest

from investment_committee.market_intelligence.quotes import QuoteKind
from investment_committee.market_intelligence.snapshots import write_snapshot
from investment_committee.simulation.forex_backtest import Strategy
from investment_committee.simulation.forex_backtest_metrics import dataset_sha256
from investment_committee.simulation.forex_backtest_study import YAHOO_CALENDAR, run_study, write_study, yahoo_week
from investment_committee.simulation.signals import Outcome

from tests.simulation._forex_backtest_support import GBP_MID, HOUR, T0, tape

L, S = Outcome.LONG, Outcome.SHORT
WHEN = datetime(2026, 11, 12, 9, 30, tzinfo=timezone.utc)
ALFA = {"EURUSD=X": {0: L, 5: S, 6: L, 10: L, 13: L, 18: L, 36: L, 60: L, 66: S}, "GBPUSD=X": {1: L}}
BETA = {"EURUSD=X": {5: S, 36: S}}
EUR_BARS = {3: ("1.08495", "1.08505", "1.08357", "1.08400"), 6: ("1.08495", "1.08505", "1.08027", "1.08100"),
            11: ("1.08495", "1.08963", "1.08357", "1.08495"), 38: ("1.08205", "1.08215", "1.08195", "1.08205"),
            62: ("1.09005", "1.09015", "1.08995", "1.09005")}


@dataclass(frozen=True)
class Evaluated:
    symbol: str
    outcome: Outcome
    candle_close: datetime
    last_close: Decimal | None
    atr: Decimal | None
    data_kind: QuoteKind
    strategy_id: str
    reasons: Sequence[str]
    ema_fast: Decimal | None = None
    ema_slow: Decimal | None = None
    rsi: Decimal | None = None

    @property
    def evaluation_key(self) -> str:
        return f"{self.strategy_id}|{self.symbol}|{self.candle_close.isoformat()}"

    @property
    def is_signal(self) -> bool:
        return self.outcome in (L, S)

    @property
    def signal_id(self) -> str | None:
        return self.evaluation_key if self.is_signal else None


def fake(name: str, warm_up: int, script: dict[str, dict[int, Outcome]], rejected: Sequence[int] = ()) -> Strategy:
    strategy_id = f"{name}@0.0.0"

    def evaluate(bars, as_of, *, symbol, data_kind, data_source, is_open):  # type: ignore[no-untyped-def]
        offset = int((as_of - T0) / HOUR)
        ordered = sorted(bars, key=lambda b: b.timestamp)
        closes = [b.close for b in ordered]
        if offset < warm_up or offset in rejected or len(closes) < 3 or ordered[-1].timestamp + HOUR != as_of:
            return Evaluated(symbol, Outcome.DATA_REJECTED, as_of, None, None, data_kind, strategy_id, ("DATOS_INCOMPLETOS",))
        outcome = script.get(symbol, {}).get(offset, Outcome.NO_CROSS)
        reasons = ("CRUCE_ALCISTA", "RSI_CONFIRMA") if outcome is L else ("CRUCE_BAJISTA", "RSI_CONFIRMA") if outcome is S else ("SIN_CRUCE",)
        return Evaluated(symbol, outcome, as_of, closes[-1], Decimal("0.00100"), data_kind, strategy_id, reasons,
                         sum(closes[-2:]) / 2, sum(closes[-3:]) / 3, Decimal(40 + offset % 20))

    return Strategy(strategy_id, name[0] * 64, evaluate, 5)


def make_study(root: Path, last: int = 80) -> tuple[Path, Path]:
    """(study folder, snapshot folder) under `root`."""
    bars = {"EURUSD=X": tape(EUR_BARS, missing=[15], last=last), "GBPUSD=X": tape(mid=GBP_MID, last=last)}
    snapshot = root / "snapshots" / "sintetico"
    write_snapshot(snapshot, bars, source="serie sintética de pruebas", interval="1h", requested_from=WHEN, requested_to=WHEN, downloaded_at=WHEN)
    study = run_study(bars, data_kind=QuoteKind.SYNTHETIC, data_source="serie sintética de pruebas", calendar=yahoo_week,
                      strategies=(fake("alfa", -2, ALFA, rejected=[30]), fake("beta", 4, BETA)))
    out = root / "backtests" / "estudio_sintetico"
    write_study(out, study, commit="abc1234", dataset=dataset_sha256(bars), dataset_description="snapshot sintetico: EURUSD=X, GBPUSD=X; serie sintética",
                calendar_description=YAHOO_CALENDAR, executed_at=WHEN)
    return out, snapshot


@pytest.fixture(scope="session")
def study_paths(tmp_path_factory) -> tuple[Path, Path]:  # type: ignore[no-untyped-def]
    return make_study(tmp_path_factory.mktemp("visor"))


@pytest.fixture(scope="session")
def short_study_paths(tmp_path_factory) -> tuple[Path, Path]:  # type: ignore[no-untyped-def]
    """The same story cut on Friday morning: positions are still open at the end."""
    return make_study(tmp_path_factory.mktemp("visor_corto"), last=68)
