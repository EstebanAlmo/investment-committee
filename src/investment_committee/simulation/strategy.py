"""Strategy "tendencia-momentum-volatilidad" v1.0.0 on EUR/USD 1-hour bars.

Deterministic and fully specified here - the same closed bars and the same
configuration always produce the same signal.

Data gates (any failure -> NO_TRADE, checked in this order):
  1. bar timestamps strictly increasing                -> INCONSISTENT_DATA
  2. at least StrategyParameters.min_bars closed bars  -> INSUFFICIENT_DATA
  3. FX market open at decision time                   -> MARKET_CLOSED
  4. last closed bar no older than max_data_age        -> STALE_DATA
Only *closed* bars are used: a bar opening at t is final at t + timeframe.

Rules, on the last closed bar (quote = its close, a mid price):
  trend      UP   if close > SMA50 and SMA20 > SMA50
             DOWN if close < SMA50 and SMA20 < SMA50, else SIDEWAYS
  momentum   LONG needs 50 < RSI14 < 70; SHORT needs 30 < RSI14 < 50
  volatility 3 <= ATR14 (in pips) <= 40
  LONG  = trend UP   + momentum + volatility
  SHORT = trend DOWN + momentum + volatility

Plan for LONG (SHORT mirrors it):
  entry  = quote + spread/2 + slippage       (simulated ask fill)
  stop   = entry - 1.5 x ATR14
  target = entry + 3.0 x ATR14
  units  = (capital x risk_fraction) / (entry - stop + exit slippage),
           rounded down to the lot step
Cost/sizing gates after a direction is chosen: fewer units than the
minimum -> POSITION_TOO_SMALL; margin (notional / leverage) above
(1 - reserve) x capital -> INSUFFICIENT_MARGIN; total costs above 25 % of
the risk -> COSTS_TOO_HIGH; net reward/risk below 1.5 ->
REWARD_RISK_TOO_LOW.

The plan is a REFERENCE computed at the signal bar's close. A simulated
execution (journal outcome, backtest) fills at the next bar's open instead
(simulation/execution.py); a live reader cannot trade at a past close.
`capital` is the account's equity when the backtest calls this.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal
from typing import Sequence

from investment_committee.domain import PriceBar

from .models import (
    AccountAssumptions,
    InstrumentSpec,
    MarketReading,
    SignalReason,
    StrategyParameters,
    TradeDirection,
    TradePlan,
    TradeSignal,
    TradingCosts,
    Trend,
)
from .technical import atr, closed_bars, is_forex_market_open, recent_range, rsi, sma

_EMPTY_READING = MarketReading(None, None, None, None, None, None, None, None, None)
_HUNDRED_THOUSAND = Decimal("100000")


def lookback_bars(parameters: StrategyParameters) -> int:
    """How many trailing bars evaluate_signal ever looks at. A caller may
    pass only this many (the backtest does, for speed) without changing the
    result."""
    return parameters.min_bars + 1


def evaluate_signal(
    bars: Sequence[PriceBar],
    as_of: datetime,
    *,
    signal_id: str,
    instrument: InstrumentSpec,
    parameters: StrategyParameters,
    costs: TradingCosts,
    account: AccountAssumptions,
) -> TradeSignal:
    """Evaluate the strategy at `as_of` using only bars closed by then."""
    closed = closed_bars(bars, parameters.timeframe, as_of)[-lookback_bars(parameters):]

    def no_trade(reasons: list[SignalReason], reading: MarketReading) -> TradeSignal:
        return _signal(signal_id, as_of, instrument, parameters, costs, account, closed, TradeDirection.NO_TRADE, reasons, reading, None)

    if any(later.timestamp <= earlier.timestamp for earlier, later in zip(closed, closed[1:])):
        return no_trade([SignalReason.INCONSISTENT_DATA], _EMPTY_READING)

    reading = _read_market(closed, instrument, parameters)
    if len(closed) < parameters.min_bars:
        return no_trade([SignalReason.INSUFFICIENT_DATA], reading)
    if not is_forex_market_open(as_of):
        return no_trade([SignalReason.MARKET_CLOSED], reading)
    if as_of - (closed[-1].timestamp + parameters.timeframe) > parameters.max_data_age:
        return no_trade([SignalReason.STALE_DATA], reading)

    direction, reasons = _apply_rules(reading, instrument, parameters)
    if direction is TradeDirection.NO_TRADE:
        return no_trade(reasons, reading)

    plan, rejection = _plan(direction, reading, closed[-1], instrument, parameters, costs, account)
    if rejection is not None:
        return no_trade([*reasons, rejection], reading)
    return _signal(signal_id, as_of, instrument, parameters, costs, account, closed, direction, reasons, reading, plan)


def _read_market(closed: Sequence[PriceBar], instrument: InstrumentSpec, parameters: StrategyParameters) -> MarketReading:
    if not closed:
        return _EMPTY_READING
    closes = [bar.close for bar in closed]
    quote = closes[-1]
    fast = sma(closes, parameters.sma_fast)
    slow = sma(closes, parameters.sma_slow)
    average_true_range = atr(closed, parameters.atr_period)
    price_range = recent_range(closed, parameters.range_lookback)
    trend = None
    if fast is not None and slow is not None:
        if quote > slow and fast > slow:
            trend = Trend.UP
        elif quote < slow and fast < slow:
            trend = Trend.DOWN
        else:
            trend = Trend.SIDEWAYS
    return MarketReading(
        quote_price=quote,
        sma_fast=fast,
        sma_slow=slow,
        rsi=rsi(closes, parameters.rsi_period),
        atr=average_true_range,
        atr_pips=None if average_true_range is None else average_true_range / instrument.pip_size,
        support=None if price_range is None else price_range[0],
        resistance=None if price_range is None else price_range[1],
        trend=trend,
    )


def _apply_rules(
    reading: MarketReading, instrument: InstrumentSpec, parameters: StrategyParameters
) -> tuple[TradeDirection, list[SignalReason]]:
    assert reading.trend is not None and reading.rsi is not None and reading.atr_pips is not None
    reasons: list[SignalReason] = []
    candidate = TradeDirection.NO_TRADE
    if reading.trend is Trend.UP:
        reasons.append(SignalReason.TREND_UP)
        candidate = TradeDirection.LONG
        momentum_ok = parameters.rsi_long_min < reading.rsi < parameters.rsi_long_max
    elif reading.trend is Trend.DOWN:
        reasons.append(SignalReason.TREND_DOWN)
        candidate = TradeDirection.SHORT
        momentum_ok = parameters.rsi_short_min < reading.rsi < parameters.rsi_short_max
    else:
        reasons.append(SignalReason.TREND_SIDEWAYS)
        momentum_ok = False

    if candidate is not TradeDirection.NO_TRADE:
        reasons.append(SignalReason.MOMENTUM_CONFIRMS if momentum_ok else SignalReason.MOMENTUM_NOT_CONFIRMING)

    if reading.atr_pips < parameters.atr_min_pips:
        reasons.append(SignalReason.VOLATILITY_TOO_LOW)
        volatility_ok = False
    elif reading.atr_pips > parameters.atr_max_pips:
        reasons.append(SignalReason.VOLATILITY_TOO_HIGH)
        volatility_ok = False
    else:
        reasons.append(SignalReason.VOLATILITY_OK)
        volatility_ok = True

    if candidate is TradeDirection.NO_TRADE or not momentum_ok or not volatility_ok:
        return TradeDirection.NO_TRADE, reasons
    return candidate, reasons


def _plan(
    direction: TradeDirection,
    reading: MarketReading,
    last_bar: PriceBar,
    instrument: InstrumentSpec,
    parameters: StrategyParameters,
    costs: TradingCosts,
    account: AccountAssumptions,
) -> tuple[TradePlan | None, SignalReason | None]:
    assert reading.quote_price is not None and reading.atr is not None
    pip = instrument.pip_size
    half_spread = costs.spread_pips * pip / 2
    slippage = costs.slippage_pips * pip
    sign = Decimal("1") if direction is TradeDirection.LONG else Decimal("-1")

    quote = reading.quote_price
    entry = _price(quote + sign * (half_spread + slippage), instrument)
    stop = _price(entry - sign * parameters.stop_atr_multiple * reading.atr, instrument)
    target = _price(entry + sign * parameters.target_atr_multiple * reading.atr, instrument)

    risk_per_unit = abs(entry - stop) + slippage  # a stop fills with adverse slippage
    reward_per_unit = abs(target - entry)  # a target is a limit: no slippage
    budget = account.capital * account.risk_fraction
    step = Decimal(account.lot_step_units)
    units = int((budget / risk_per_unit / step).to_integral_value(rounding=ROUND_DOWN) * step)
    if units < max(account.min_units, account.lot_step_units):
        return None, SignalReason.POSITION_TOO_SMALL

    unit_count = Decimal(units)
    commission = _money(costs.commission_per_100k * unit_count / _HUNDRED_THOUSAND)
    spread_cost = _money(unit_count * costs.spread_pips * pip)
    slippage_cost = _money(unit_count * slippage * 2)
    risk_amount = _money(unit_count * risk_per_unit + commission)
    reward_amount = _money(unit_count * reward_per_unit - commission)
    net_reward_risk = (reward_amount / risk_amount).quantize(Decimal("0.01"))

    notional = _money(unit_count * entry)
    if notional / account.leverage > (1 - account.margin_reserve_fraction) * account.capital:
        return None, SignalReason.INSUFFICIENT_MARGIN
    if (spread_cost + slippage_cost + commission) > parameters.max_cost_fraction_of_risk * risk_amount:
        return None, SignalReason.COSTS_TOO_HIGH
    if net_reward_risk < parameters.min_net_reward_risk:
        return None, SignalReason.REWARD_RISK_TOO_LOW

    data_as_of = last_bar.timestamp + parameters.timeframe
    return (
        TradePlan(
            quote_price=quote,
            entry_price=entry,
            stop_loss=stop,
            take_profit=target,
            risk_pips=(risk_per_unit / pip).quantize(Decimal("0.1")),
            reward_pips=(reward_per_unit / pip).quantize(Decimal("0.1")),
            gross_reward_risk=(abs(target - entry) / abs(entry - stop)).quantize(Decimal("0.01")),
            net_reward_risk=net_reward_risk,
            units=units,
            risk_amount=risk_amount,
            reward_amount=reward_amount,
            spread_cost=spread_cost,
            slippage_cost=slippage_cost,
            commission=commission,
            notional=notional,
            margin_required=_money(notional / account.leverage),
            leverage=account.leverage,
            expires_at=data_as_of + parameters.expiry_bars * parameters.timeframe,
        ),
        None,
    )


def _signal(
    signal_id: str,
    as_of: datetime,
    instrument: InstrumentSpec,
    parameters: StrategyParameters,
    costs: TradingCosts,
    account: AccountAssumptions,
    closed: Sequence[PriceBar],
    direction: TradeDirection,
    reasons: list[SignalReason],
    reading: MarketReading,
    plan: TradePlan | None,
) -> TradeSignal:
    return TradeSignal(
        signal_id=signal_id,
        created_at=as_of,
        instrument=instrument,
        timeframe=parameters.timeframe,
        data_as_of=closed[-1].timestamp + parameters.timeframe if closed else None,
        last_bar_open_time=closed[-1].timestamp if closed else None,
        direction=direction,
        reasons=reasons,
        reading=reading,
        plan=plan,
        costs=costs,
        parameters=parameters,
        account=account,
        bars_used=len(closed),
    )


def _price(value: Decimal, instrument: InstrumentSpec) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-instrument.price_places), rounding=ROUND_HALF_EVEN)


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
