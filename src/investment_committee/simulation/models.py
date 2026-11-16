"""Simulated-trading models: configuration, signals, outcomes, backtests.

Everything here is hypothetical. No type in this module represents an order
sent to a broker, and none ever will (CLAUDE.md). Prices are Decimals in the
instrument's quote currency (USD for EUR/USD); quantities are units of the
base currency.

Price vocabulary, kept distinct on purpose:
- quote (mid) price: what the data provider reports (Yahoo FX bars);
- simulated execution price: the quote adjusted by half the configured
  spread and by slippage, i.e. the price a buyer (ask) or seller (bid)
  would plausibly get;
- costs: spread, slippage, and commission, each reported separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Mapping, Sequence

STRATEGY_NAME = "tendencia-momentum-volatilidad"
STRATEGY_VERSION = "1.0.0"
# Backtest engine (execution and account model), versioned apart from the
# strategy rules: 2.0.0 enters at the next bar's open and runs a capital
# account with margin, stop-out, and trading suspension.
BACKTEST_ENGINE_VERSION = "2.0.0"


class TradeDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NO_TRADE = "NO_TRADE"


class Trend(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    SIDEWAYS = "SIDEWAYS"


class SignalReason(str, Enum):
    """Why a signal is (or is not) a trade - stable codes the presentation
    layer translates."""

    # Data and market gates - checked before any rule.
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    STALE_DATA = "STALE_DATA"
    INCONSISTENT_DATA = "INCONSISTENT_DATA"
    MARKET_CLOSED = "MARKET_CLOSED"
    # Strategy rules.
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    TREND_SIDEWAYS = "TREND_SIDEWAYS"
    MOMENTUM_CONFIRMS = "MOMENTUM_CONFIRMS"
    MOMENTUM_NOT_CONFIRMING = "MOMENTUM_NOT_CONFIRMING"
    VOLATILITY_OK = "VOLATILITY_OK"
    VOLATILITY_TOO_LOW = "VOLATILITY_TOO_LOW"
    VOLATILITY_TOO_HIGH = "VOLATILITY_TOO_HIGH"
    # Cost and sizing gates - checked once a direction is chosen.
    COSTS_TOO_HIGH = "COSTS_TOO_HIGH"
    REWARD_RISK_TOO_LOW = "REWARD_RISK_TOO_LOW"
    POSITION_TOO_SMALL = "POSITION_TOO_SMALL"
    INSUFFICIENT_MARGIN = "INSUFFICIENT_MARGIN"
    # Experimental V2 strategies (simulation/strategies.py) - v1.0.0 never emits these.
    SETUP_LONG = "SETUP_LONG"  # the strategy's rules point LONG (before the cost/size gates)
    SETUP_SHORT = "SETUP_SHORT"
    HTF_CONFIRMS = "HTF_CONFIRMS"
    HTF_NOT_CONFIRMING = "HTF_NOT_CONFIRMING"
    RSI_IN_BAND = "RSI_IN_BAND"
    RSI_OUT_OF_BAND = "RSI_OUT_OF_BAND"
    OUTSIDE_TRADING_HOURS = "OUTSIDE_TRADING_HOURS"
    RANGE_TOO_NARROW = "RANGE_TOO_NARROW"
    RANGE_TOO_WIDE = "RANGE_TOO_WIDE"
    RANGE_COMPRESSED = "RANGE_COMPRESSED"
    BREAKOUT_UP = "BREAKOUT_UP"
    BREAKOUT_DOWN = "BREAKOUT_DOWN"
    NO_BREAKOUT = "NO_BREAKOUT"
    MARKET_RANGING = "MARKET_RANGING"
    MARKET_TRENDING = "MARKET_TRENDING"
    BAND_EXTREME_LOW = "BAND_EXTREME_LOW"
    BAND_EXTREME_HIGH = "BAND_EXTREME_HIGH"
    NO_EXTREME = "NO_EXTREME"


class TradeStatus(str, Enum):
    OPEN = "OPEN"  # waiting for its entry bar, or entered and still running
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    EXPIRED = "EXPIRED"
    NOT_FILLED = "NOT_FILLED"  # no eligible entry bar soon enough after the signal
    LIQUIDATED = "LIQUIDATED"  # closed by the margin stop-out
    END_OF_DATA = "END_OF_DATA"  # backtest only: closed when its period ended


class AccountStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED_DRAWDOWN = "SUSPENDED_DRAWDOWN"  # loss limit reached: no new entries
    CAPITAL_EXHAUSTED = "CAPITAL_EXHAUSTED"  # equity cannot fund the minimum position
    NEGATIVE_BALANCE = "NEGATIVE_BALANCE"  # losses beyond the balance: simulated debt


class RejectionReason(str, Enum):
    INSUFFICIENT_MARGIN = "INSUFFICIENT_MARGIN"
    POSITION_TOO_SMALL = "POSITION_TOO_SMALL"
    ENTRY_NOT_AVAILABLE = "ENTRY_NOT_AVAILABLE"  # next bar too late (gap, weekend) or market closed
    TRADING_SUSPENDED = "TRADING_SUSPENDED"


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str  # canonical provider symbol, e.g. "EURUSD=X"
    display_name: str  # e.g. "EUR/USD"
    pip_size: Decimal  # 0.0001 for EUR/USD
    quote_currency: str  # P&L currency, e.g. "USD"
    price_places: int = 5


EURUSD = InstrumentSpec(symbol="EURUSD=X", display_name="EUR/USD", pip_size=Decimal("0.0001"), quote_currency="USD")


@dataclass(frozen=True)
class TradingCosts:
    """Configurable cost assumptions. Yahoo's FX bars carry no bid/ask, so
    the spread is an assumption, not observed data."""

    spread_pips: Decimal = Decimal("1.0")
    slippage_pips: Decimal = Decimal("0.2")  # per fill, always adverse
    commission_per_100k: Decimal = Decimal("0")  # quote currency, per round trip


@dataclass(frozen=True)
class StrategyParameters:
    timeframe: timedelta = timedelta(hours=1)
    interval_code: str = "1h"  # provider interval matching `timeframe`
    sma_fast: int = 20
    sma_slow: int = 50
    rsi_period: int = 14
    atr_period: int = 14
    range_lookback: int = 24  # bars used for recent support/resistance
    rsi_long_min: Decimal = Decimal("50")
    rsi_long_max: Decimal = Decimal("70")
    rsi_short_min: Decimal = Decimal("30")
    rsi_short_max: Decimal = Decimal("50")
    atr_min_pips: Decimal = Decimal("3")
    atr_max_pips: Decimal = Decimal("40")
    stop_atr_multiple: Decimal = Decimal("1.5")
    target_atr_multiple: Decimal = Decimal("3.0")
    min_net_reward_risk: Decimal = Decimal("1.5")
    max_cost_fraction_of_risk: Decimal = Decimal("0.25")
    expiry_bars: int = 24
    max_data_age: timedelta = timedelta(hours=3)
    # Execution: a signal fills at the open of the first bar after it, only
    # if that bar opens within this delay and while the market is open.
    max_entry_delay: timedelta = timedelta(hours=2)

    @property
    def min_bars(self) -> int:
        """Bars needed before any rule can be evaluated."""
        return max(self.sma_slow, self.rsi_period + 1, self.atr_period + 1, self.range_lookback) + 10


@dataclass(frozen=True)
class AccountAssumptions:
    """A hypothetical account and its risk limits. Live /trade signals size
    against `capital`; the backtest starts from it and then sizes against
    the account's equity at each decision.

    - risk_fraction: money at risk per trade (to the stop, costs included),
      as a fraction of equity.
    - leverage: maximum leverage; initial margin = notional / leverage.
    - margin_reserve_fraction: after opening, free margin must still be at
      least this fraction of equity (used margin <= (1 - reserve) x equity).
    - stop_out_level: margin level (equity / used margin) at or below which
      an open position is liquidated.
    - max_drawdown_halt: drawdown from peak equity that suspends new entries.
    - min_units / lot_step_units: smallest position and size increment.
    """

    capital: Decimal = Decimal("10000")
    risk_fraction: Decimal = Decimal("0.01")
    leverage: Decimal = Decimal("30")
    lot_step_units: int = 1000  # micro lot
    min_units: int = 1000
    margin_reserve_fraction: Decimal = Decimal("0.25")
    stop_out_level: Decimal = Decimal("0.5")
    max_drawdown_halt: Decimal = Decimal("0.30")


@dataclass(frozen=True)
class MarketReading:
    """Indicator values the strategy computed from closed bars only. Any
    value is None when there was not enough data to compute it."""

    quote_price: Decimal | None
    sma_fast: Decimal | None
    sma_slow: Decimal | None
    rsi: Decimal | None
    atr: Decimal | None
    atr_pips: Decimal | None
    support: Decimal | None
    resistance: Decimal | None
    trend: Trend | None


@dataclass(frozen=True)
class TradePlan:
    """Levels, costs, and sizing for a LONG/SHORT signal."""

    quote_price: Decimal  # mid price of the last closed bar
    entry_price: Decimal  # simulated execution price (ask for LONG, bid for SHORT)
    stop_loss: Decimal
    take_profit: Decimal
    risk_pips: Decimal
    reward_pips: Decimal
    gross_reward_risk: Decimal
    net_reward_risk: Decimal
    units: int
    risk_amount: Decimal  # loss at stop, costs included
    reward_amount: Decimal  # gain at target, costs included
    spread_cost: Decimal
    slippage_cost: Decimal
    commission: Decimal
    notional: Decimal
    margin_required: Decimal
    leverage: Decimal
    expires_at: datetime


@dataclass(frozen=True)
class TradeSignal:
    signal_id: str
    created_at: datetime
    instrument: InstrumentSpec
    timeframe: timedelta
    data_as_of: datetime | None  # close time of the last closed bar used
    last_bar_open_time: datetime | None
    direction: TradeDirection
    reasons: Sequence[SignalReason]
    reading: MarketReading
    plan: TradePlan | None
    costs: TradingCosts
    parameters: StrategyParameters
    account: AccountAssumptions
    bars_used: int
    strategy_name: str = STRATEGY_NAME
    strategy_version: str = STRATEGY_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", tuple(self.reasons))
        if (self.direction is TradeDirection.NO_TRADE) != (self.plan is None):
            raise ValueError("a trade plan exists exactly when direction is LONG or SHORT")

    @property
    def is_trade(self) -> bool:
        return self.plan is not None


@dataclass(frozen=True)
class TradeOutcome:
    """How a simulated trade actually went. Entry fields describe the
    simulated fill at the next bar's open (they can differ from the signal's
    reference plan, which was computed at the signal bar's close)."""

    signal_id: str
    status: TradeStatus
    evaluated_at: datetime
    bars_observed: int
    exit_time: datetime | None = None
    exit_price: Decimal | None = None
    pnl_pips: Decimal | None = None
    pnl_amount: Decimal | None = None  # quote currency: price P&L minus commission
    same_bar_conflict: bool = False  # stop and target both reachable in one bar
    entry_time: datetime | None = None
    entry_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    units: int | None = None


@dataclass(frozen=True)
class SimulatedTrade:
    """A recorded signal with its latest known outcome (None: not a trade)."""

    signal: TradeSignal
    outcome: TradeOutcome | None


@dataclass(frozen=True)
class BacktestTrade:
    """One executed backtest trade with its money breakdown. gross_pnl is the
    price move times units, from the simulated fills - spread and slippage
    are already inside it; net_pnl = gross_pnl - commission."""

    signal: TradeSignal
    outcome: TradeOutcome
    units: int
    margin_used: Decimal
    equity_at_decision: Decimal
    risk_amount: Decimal
    gross_pnl: Decimal
    commission: Decimal
    net_pnl: Decimal
    spread_cost: Decimal  # included in gross_pnl, reported for transparency
    slippage_cost: Decimal  # included in gross_pnl, reported for transparency


@dataclass(frozen=True)
class Rejection:
    """A strategy signal the account did not execute, and why."""

    signal_id: str
    decided_at: datetime
    reason: RejectionReason
    equity: Decimal
    required_margin: Decimal | None = None
    available_margin: Decimal | None = None


@dataclass(frozen=True)
class AccountPoint:
    """Account state at a bar's close (an open position is marked at the
    exit side of that close)."""

    timestamp: datetime
    balance: Decimal
    equity: Decimal
    used_margin: Decimal


@dataclass(frozen=True)
class BacktestMetrics:
    trades: int
    wins: int
    losses: int
    take_profits: int
    stop_losses: int
    expired: int
    liquidations: int
    closed_at_end: int
    win_rate: Decimal | None
    gross_profit: Decimal  # sum of winning net P&L
    gross_loss: Decimal  # sum of losing net P&L (<= 0)
    profit_factor: Decimal | None
    average_win: Decimal | None
    average_loss: Decimal | None
    expectancy: Decimal | None  # net P&L per trade
    net_pnl: Decimal
    net_pnl_pips: Decimal
    net_return: Decimal  # (final equity - initial capital) / initial capital
    max_drawdown: Decimal  # USD, <= 0, on the bar-by-bar equity curve
    max_drawdown_fraction: Decimal  # of peak equity, <= 0
    spread_cost: Decimal
    slippage_cost: Decimal
    commission_cost: Decimal
    total_costs: Decimal
    same_bar_conflicts: int
    signals: int  # LONG/SHORT signals produced by the strategy
    rejections: Mapping[RejectionReason, int]


@dataclass(frozen=True)
class BacktestResult:
    instrument: InstrumentSpec
    timeframe: timedelta
    label: str  # e.g. "completo", "desarrollo"
    data_start: datetime  # first bar the period may trade on
    data_end: datetime
    bars: int  # bars inside the period
    warmup_bars: int  # earlier bars used only to initialise indicators
    initial_capital: Decimal
    final_balance: Decimal
    final_equity: Decimal
    lowest_equity: Decimal
    status: AccountStatus
    suspended_at: datetime | None
    trades: Sequence[BacktestTrade]
    rejections: Sequence[Rejection]
    metrics: BacktestMetrics
    costs: TradingCosts
    parameters: StrategyParameters
    account: AccountAssumptions
    generated_at: datetime
    strategy_name: str = STRATEGY_NAME
    strategy_version: str = STRATEGY_VERSION
    engine_version: str = BACKTEST_ENGINE_VERSION
    account_curve: Sequence[AccountPoint] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "trades", tuple(self.trades))
        object.__setattr__(self, "rejections", tuple(self.rejections))
        object.__setattr__(self, "account_curve", tuple(self.account_curve))

    @property
    def debt(self) -> Decimal:
        """Simulated amount owed when losses exceeded the balance (0 otherwise)."""
        return -self.final_balance if self.final_balance < 0 else Decimal("0")


@dataclass(frozen=True)
class SegmentedBacktest:
    """The full period plus chronological development / validation / final
    test segments, each traded from a fresh account."""

    full: BacktestResult
    segments: Sequence[BacktestResult]
    min_trades_for_confidence: int = 30

    def __post_init__(self) -> None:
        object.__setattr__(self, "segments", tuple(self.segments))
