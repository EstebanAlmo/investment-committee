"""Scripted signals and made-up hourly bars for the forex_backtest tests.
Not collected by pytest.

The engine's account pass only needs something that meets
signals.TradeSignal, so the tests decide exactly when a signal appears and
with which ATR, and write every bar by hand. Bars are labelled by their
OPEN time, like the Yahoo snapshot. Nothing here is market data.

The base case, computed by hand (EUR/USD, spread 1.0 pip, slippage 0.2 pip,
no commission, no financing, 10 000 USD, ATR 10 pips, open 1.08495):
  bid 1.08490, ask 1.08500
  LONG   E 1.08502   SL 1.08352   l = 0.00150 + 0.00002 = 0.00152
         T = 3 x 0.00152 = 0.00456   TP 1.08958
         units = floor(50 / 0.00152 / 1000) x 1000 = 32 000
         stop: fill 1.08350, -48.64 USD    target: +145.92 USD (3.0000 R)
         exit side = bar price - 0.00005: stop touched when low <= 1.08357,
         target when high >= 1.08963
  SHORT  E 1.08488   SL 1.08638   TP 1.08032   32 000 units
         stop: fill 1.08640, -48.64 USD    target: +145.92 USD
         exit side = bar price + 0.00005: stop touched when high >= 1.08633,
         target when low <= 1.08027
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Mapping, Sequence

from investment_committee.domain import PriceBar
from investment_committee.market_intelligence.quotes import QuoteKind
from investment_committee.simulation.forex_backtest import BacktestRun, Scenario, run_account
from investment_committee.simulation.opportunities import RiskLimits
from investment_committee.simulation.signals import Outcome

UTC = timezone.utc
HOUR = timedelta(hours=1)
D = Decimal
T0 = datetime(2026, 12, 1, 14, 0, tzinfo=UTC)  # Tuesday 09:00 New York (winter); 72 bars end Friday 14:00 UTC
MID = D("1.08495")
GBP_MID = D("1.27000")
STRATEGY = "prueba@0.0.0"
SHA = "0" * 64


@dataclass(frozen=True)
class Scripted:
    symbol: str
    outcome: Outcome
    candle_close: datetime
    last_close: Decimal | None
    atr: Decimal | None
    data_kind: QuoteKind = QuoteKind.SYNTHETIC
    strategy_id: str = STRATEGY
    reasons: Sequence[object] = ()

    @property
    def evaluation_key(self) -> str:
        return f"{self.strategy_id}|{self.symbol}|{self.candle_close.isoformat()}"

    @property
    def is_signal(self) -> bool:
        return self.outcome in (Outcome.LONG, Outcome.SHORT)

    @property
    def signal_id(self) -> str | None:
        return self.evaluation_key if self.is_signal else None


def sig(offset: int, outcome: Outcome = Outcome.LONG, symbol: str = "EURUSD", atr: str = "0.00100", last_close: Decimal | None = None,
        origin: datetime = T0) -> Scripted:
    """A signal whose candle closes `offset` hours after `origin`."""
    close = MID if symbol == "EURUSD" else GBP_MID
    return Scripted(symbol, outcome, origin + offset * HOUR, close if last_close is None else last_close, D(atr))


def bar(label: datetime, open_: str | Decimal, high: str | Decimal, low: str | Decimal, close: str | Decimal) -> PriceBar:
    return PriceBar(label, D(open_), D(high), D(low), D(close), 0)


def tape(overrides: Mapping[int, tuple[str, str, str, str]] | None = None, *, missing: Sequence[int] = (), first: int = -5, last: int = 110,
         mid: Decimal = MID, origin: datetime = T0) -> list[PriceBar]:
    """Quiet bars (open = close = mid, a 1-pip wick on each side) labelled
    origin + first .. origin + last hours; `overrides` replaces the bar at an
    offset by (open, high, low, close); `missing` offsets have no bar."""
    overrides, wick, bars = overrides or {}, D("0.00010"), []
    for offset in range(first, last + 1):
        if offset in missing:
            continue
        label = origin + offset * HOUR
        bars.append(bar(label, *overrides[offset]) if offset in overrides else bar(label, mid, mid + wick, mid - wick, mid))
    return bars


def play(signals: Sequence[Scripted], bars: Mapping[str, Sequence[PriceBar]] | Sequence[PriceBar], *, first: int = -4, last: int = 100,
         origin: datetime = T0, scenario: Scenario | None = None, limits: RiskLimits | None = None, **options: object) -> BacktestRun:
    """The account pass over `signals`, from origin + first to origin + last hours."""
    by_symbol: dict[str, dict[datetime, Scripted]] = {}
    for signal in signals:
        by_symbol.setdefault(signal.symbol, {})[signal.candle_close] = signal
    data = bars if isinstance(bars, Mapping) else {"EURUSD": bars}
    return run_account(STRATEGY, SHA, by_symbol, data, start=origin + first * HOUR, end=origin + last * HOUR,
                       scenario=scenario or Scenario(), limits=limits or RiskLimits(), **options)  # type: ignore[arg-type]
