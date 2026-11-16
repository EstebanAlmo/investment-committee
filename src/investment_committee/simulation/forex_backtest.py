"""Backtest engine of the experimental Forex strategies, ema-forex-backtest
1.0.0: closed candle -> signal -> proposal -> hypothetical entry -> exit ->
result, hour by hour, for ONE strategy and its own hypothetical account.

EXPERIMENTAL. A retrospective simulation under an ESTIMATED execution model:
no order, no broker, no executable price. It reuses, unchanged, the signals
(ema_cross.py, ema_trend_cross.py), build_proposal / settle / indeterminate
(trade_proposal.py) and the session rules (ema_cross_sessions.py). The
engines of the frozen strategies (backtest.py, replay.py, execution.py,
outcome.py) are neither used nor touched: their rules are different.

TIME. Bars are labelled by their OPEN time (verified for the Yahoo snapshot,
simulation/sessions.py): the bar labelled L covers [L, L + 1h) and is closed
at L + 1h. At an instant t the engine can read exactly two things, through
_Tape: the bar that closed at t (labelled t - 1h), whole, and the OPEN of
the bar labelled t. Nothing else of the bar labelled t, and nothing later.

Two passes:
  evaluate_signals  each strategy evaluation at t receives only the bars
                    closed by t. It knows no account and no cost, so it is
                    computed once and shared by every scenario.
  run_account       walks the instants start..end. At each instant t:
    1. the bar that closed at t resolves open positions (levels touched
       inside it), or is found missing;
    2. the accounting day rolls at 00:00 UTC;
    3. with the open of the bar labelled t: an uncertainty is resolved, a
       level already crossed at that open fills, a position due leaves;
    4. the signals whose candle closed at t become proposals, EUR/USD before
       GBP/USD; an accepted one opens its position at t;
    5. the account is valued.
  So signal_candle_close_at = quote_available_at = entry_at = t for an
  entry: the estimated quote IS the open of the bar labelled t, the first
  price after the signal candle closed. No bar labelled t: no quote, and
  the proposal is rejected (COTIZACION_AUSENTE); a signal is never retried.
  The entry bar's high, low and close are read one hour later, at step 1,
  and only for exits: the open came first.

EXITS. Levels are compared with the estimated EXIT side: price - half the
assumed spread for a LONG (it sells at the bid), price + half for a SHORT.
The spread is therefore paid once, through the prices. Touching a level
exactly triggers it.
  open beyond the stop       STOP at that open less slippage (a gap), at L
  open beyond the target     TARGET at its level, no improvement, at L
  only the stop inside       STOP at its level less slippage, at L + 1h
  only the target inside     TARGET at its level, at L + 1h
  both inside                INDETERMINATE (order unknown), booked at L + 1h
  due (72 bars / weekly close, proposal.horizon_end), levels untouched
                             market exit at the open of the bar labelled
                             then, exit side less slippage; without that bar,
                             at the close of the bar that just ended - the
                             same instant, not a stale price
An exit inside a bar is dated at the bar's close: if the bar crosses 17:00
New York, that night's financing is charged (the conservative choice).

UNCERTAINTY. Nothing is assumed about an hour that was not observed.
  - Stop and target inside one bar: INDETERMINATE at that bar's close.
  - An hour the calendar expects has no bar while a position is open (or a
    position is due and no price exists): the position is not assumed to
    have survived. It stays unresolved - holding its pair, its risk and its
    margin - until the first bar that exists again, labelled r. At r, with
    nothing but that bar's open, it becomes INDETERMINATE: conservative =
    the stop, or the open at r if that is worse; optimistic = the target.
    No exit instant is invented (exit_at is None); the account books the
    result at r, the first moment it could know. If prices never return, the
    trade stays open at the end with a stale valuation.
  An indeterminate trade is not a valid closed trade for the metrics. The
  account books its conservative result (the optimistic one only under the
  separate OPTIMISTA scenario, a different run with its own account), so
  later sizing and risk limits never benefit from an unknown.

ACCOUNT. One run, one account: `limits.capital` (10 000 USD) and nothing
shared between runs. Sizing and limits use the REALIZED balance (initial +
booked results); unrealized gains never enlarge a position. The accounting
day is the UTC date; a result dated exactly 00:00 belongs to the day that
ended if it comes from the bar that closed then, to the new day otherwise.
The estimated equity adds each open position marked at the exit side of the
last close (less commission and the financing nights so far); when the bar
that should have just closed is missing, the mark is the last known close
and the point is flagged stale - not a current valuation. Hourly marks can
miss what happened inside the hour: drawdown is understated.

COSTS (Scenario). Assumptions, none verified: spread and slippage per
instrument (trade_proposal.InstrumentRules), commission 0, no financing
rate. cost_multiple scales spread, slippage and commission; the proposal
engine then recomputes entry, stop, target and size by its own formulas.
The maximum-spread rule is a rule, not a cost: it is not scaled.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from enum import Enum
from typing import Callable, Mapping, Sequence

from investment_committee.domain import PriceBar
from investment_committee.market_intelligence.quality import Calendar
from investment_committee.market_intelligence.quotes import QuoteKind, estimated_from_mid, to_utc

from . import ema_cross, ema_pullback, ema_trend_cross
from .ema_cross_sessions import rollover_nights, time_exit_due, weekly_close
from .opportunities import AccountState, OpenPosition, RiskLimits
from .sessions import is_week_open
from .signals import Outcome, TradeSignal
from .trade_proposal import (
    ExecutionParameters,
    ExitReason,
    FinancingAssumption,
    IndeterminateResult,
    InstrumentRules,
    TradeProposal,
    TradeResult,
    build_proposal,
    indeterminate,
    instrument_for,
    settle,
)

ENGINE_ID = "ema-forex-backtest@1.0.0"
HOUR = timedelta(hours=1)
_INSTANT = timedelta(microseconds=1)
_CENT = Decimal("0.01")
_ZERO = Decimal("0")
CONSERVATIVE, OPTIMISTIC_POLICY = "CONSERVADORA", "OPTIMISTA"
SAME_BAR = "STOP_Y_OBJETIVO_EN_LA_MISMA_VELA"
DATA_HOLE = "HUECO_DE_DATOS"
AT_LEVEL, AT_OPEN, AT_PREVIOUS_CLOSE = "NIVEL", "APERTURA", "CIERRE_VELA_ANTERIOR"
QUOTE_SOURCE = "apertura de la vela H1 siguiente al cierre de la señal"


# --- what is run ---


@dataclass(frozen=True)
class Strategy:
    strategy_id: str
    parameters_sha256: str
    evaluate: Callable[..., TradeSignal]
    lookback_hours: int  # each evaluation is handed the bars of this many hours back: more than its window can reach.
    # Counted in hours, not in bars: bars outside the calendar (a weekend bar) must not use up the history.
    # A window of N expected hours spans N x 168 / 118 clock hours in the tightest calendar used (Yahoo's week); 2 N covers it.


STRATEGY_A = Strategy(ema_cross.STRATEGY_ID, ema_cross.fingerprint(), ema_cross.evaluate, 2 * ema_cross.EmaCrossParameters().window_bars)
STRATEGY_B = Strategy(ema_trend_cross.STRATEGY_ID, ema_trend_cross.fingerprint(), ema_trend_cross.evaluate,
                      2 * ema_trend_cross.EmaTrendCrossParameters().window_bars)
STRATEGY_C = Strategy(ema_pullback.STRATEGY_ID, ema_pullback.fingerprint(), ema_pullback.evaluate, 2 * ema_pullback.EmaPullbackParameters().window_bars)
# Execution hypotheses that belong to a strategy and differ from the defaults of trade_proposal.ExecutionParameters
# (which are A's and B's, unchanged). C: a target of 2 R instead of 3 R; its stop travels with its signal.
STRATEGY_EXECUTION: dict[str, ExecutionParameters] = {
    ema_pullback.STRATEGY_ID: ExecutionParameters(min_net_reward_risk=ema_pullback.EmaPullbackParameters().net_reward_risk),
}


@dataclass(frozen=True)
class Scenario:
    """Cost assumptions and the policy for unknowns. None is a verified cost."""

    name: str = "BASE"
    cost_multiple: Decimal = Decimal("1")  # on spread, slippage and commission
    commission_per_100k: Decimal = Decimal("0")  # USD, round trip; 0 is an assumption, not a verified commission
    financing: FinancingAssumption = FinancingAssumption()
    indeterminate_policy: str = CONSERVATIVE  # which result of an indeterminate trade the account books

    def __post_init__(self) -> None:
        if self.cost_multiple <= 0 or self.commission_per_100k < 0:
            raise ValueError("cost_multiple must be positive and the commission not negative")
        if self.indeterminate_policy not in (CONSERVATIVE, OPTIMISTIC_POLICY):
            raise ValueError("indeterminate_policy must be CONSERVADORA or OPTIMISTA")

    def rules(self, symbol: str) -> InstrumentRules:
        base = instrument_for(symbol)
        if base is None:
            raise ValueError(f"{symbol}: solo EUR/USD y GBP/USD")
        return replace(base, assumed_spread=base.assumed_spread * self.cost_multiple, slippage=base.slippage * self.cost_multiple)

    def execution(self, strategy_id: str | None = None) -> ExecutionParameters:
        """The execution parameters of a run: the strategy's own (the
        defaults for A, B and any unknown strategy) with this scenario's
        commission."""
        base = STRATEGY_EXECUTION.get(strategy_id or "", ExecutionParameters())
        return replace(base, commission_per_100k=self.commission_per_100k * self.cost_multiple)


BASE = Scenario()
COSTS_X1_5 = Scenario("COSTOS_X1_5", cost_multiple=Decimal("1.5"))
COSTS_X2 = Scenario("COSTOS_X2", cost_multiple=Decimal("2"))
ILLUSTRATIVE_FINANCING = Scenario("FINANCIACION_ILUSTRATIVA_3_POR_CIENTO", financing=FinancingAssumption(
    Decimal("0.03"), "escenario ilustrativo: 3 % anual, no es la tasa de ningún broker"))
OPTIMISTIC = Scenario("INDETERMINADAS_OPTIMISTAS", indeterminate_policy=OPTIMISTIC_POLICY)


def hypothetical_commission(per_100k: Decimal) -> Scenario:
    """Base costs plus a made-up round-trip commission, to see its effect."""
    return Scenario(f"COMISION_HIPOTETICA_{per_100k}_USD_POR_100K", commission_per_100k=per_100k)


# --- what comes out ---


class TradeStatus(str, Enum):
    CLOSED = "CERRADA"
    INDETERMINATE = "INDETERMINADA"
    OPEN_AT_END = "ABIERTA_AL_FINAL"


@dataclass(frozen=True)
class Decision:
    """A confirmed signal and what the proposal engine made of it."""

    order: int  # 1, 2, ... in the order the engine processed them
    at: datetime
    symbol: str
    direction: str
    signal_id: str | None
    signal_candle_close_at: datetime
    quote_available_at: datetime | None  # None: the bar after the signal candle does not exist
    proposal: TradeProposal


@dataclass(frozen=True)
class Uncertainty:
    kind: str  # SAME_BAR | DATA_HOLE
    start: datetime  # the interval in which the outcome is unknown
    end: datetime | None  # None: prices had not returned when the run ended
    unobserved_hours: Sequence[datetime] = ()  # labels of the expected bars that do not exist
    last_price_before: Decimal | None = None  # the last close observed before the hole ...
    last_price_before_at: datetime | None = None  # ... and when that bar closed
    first_price_after: Decimal | None = None  # the open of the first bar that exists again
    detail: str = ""


@dataclass(frozen=True)
class Trade:
    order: int  # its decision's
    strategy_id: str
    symbol: str
    direction: str
    proposal: TradeProposal
    signal_candle_close_at: datetime
    quote_available_at: datetime
    entry_at: datetime
    status: TradeStatus
    bars_held: int  # bars observed from the entry's own bar on
    result: TradeResult | None = None  # CLOSED only
    indeterminate: IndeterminateResult | None = None  # INDETERMINATE only
    exit_at: datetime | None = None  # None unless CLOSED: an unknown exit has no instant
    recorded_at: datetime | None = None  # when the account booked it
    exit_price_source: str | None = None  # NIVEL | APERTURA | CIERRE_VELA_ANTERIOR
    uncertainty: Uncertainty | None = None
    booked_pnl: Decimal | None = None  # what the account booked; None while open
    valuation_price: Decimal | None = None  # OPEN_AT_END: the last close known ...
    valuation_at: datetime | None = None  # ... when that bar closed ...
    valuation_stale: bool = False  # ... and whether it was no longer current when the run ended
    unrealized_pnl: Decimal | None = None


@dataclass(frozen=True)
class EquityPoint:
    at: datetime
    balance: Decimal  # realized
    equity: Decimal  # balance + open positions at the last close known
    open_positions: int
    stale: bool  # an open position is valued at a price that is not current


@dataclass(frozen=True)
class BacktestRun:
    engine: str
    strategy_id: str
    parameters_sha256: str
    scenario: Scenario
    limits: RiskLimits
    start: datetime
    end: datetime
    symbols: Sequence[str]
    coverage: Mapping[str, Mapping[str, int]]  # per symbol: evaluations by outcome (data rejections by their reason)
    decisions: Sequence[Decision]
    trades: Sequence[Trade]
    equity: Sequence[EquityPoint]
    final_balance: Decimal


# --- the tape: the only door to prices ---


class _Tape:
    """Bars of one instrument by label (their open time, UTC)."""

    def __init__(self, bars: Sequence[PriceBar]) -> None:
        self._bars = {to_utc(b.timestamp): b for b in bars}

    def closed_at(self, moment: datetime) -> PriceBar | None:
        """The bar that closed at `moment`: the one labelled an hour before."""
        return self._bars.get(moment - HOUR)

    def open_at(self, moment: datetime) -> Decimal | None:
        """The open of the bar labelled `moment` - its first price, and the
        only part of that bar known at `moment`."""
        found = self._bars.get(moment)
        return None if found is None else found.open


def _grid(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    start, end = to_utc(start, "start"), to_utc(end, "end")
    if start != start.replace(minute=0, second=0, microsecond=0) or end < start:
        raise ValueError("start must be a full hour and end must not be before it")
    return start, end


def evaluate_signals(
    strategy: Strategy, bars: Mapping[str, Sequence[PriceBar]], *, start: datetime, end: datetime, data_kind: QuoteKind, data_source: str,
    calendar: Calendar = is_week_open,
) -> dict[str, dict[datetime, TradeSignal]]:
    """The strategy's evaluation of every instrument at every full hour from
    `start` to `end`. The evaluation at t is handed only bars closed by t."""
    start, end = _grid(start, end)
    out: dict[str, dict[datetime, TradeSignal]] = {}
    for symbol, series in bars.items():
        ordered = sorted(series, key=lambda b: to_utc(b.timestamp))
        labels = [to_utc(b.timestamp) for b in ordered]
        evaluations: dict[datetime, TradeSignal] = {}
        moment = start
        while moment <= end:
            closed = bisect_right(labels, moment - HOUR)  # label + 1h <= moment
            oldest = bisect_left(labels, moment - strategy.lookback_hours * HOUR)
            evaluations[moment] = strategy.evaluate(ordered[oldest:closed], moment, symbol=symbol, data_kind=data_kind, data_source=data_source,
                                                    is_open=calendar)
            moment += HOUR
        out[symbol] = evaluations
    return out


# --- the account pass ---


@dataclass
class _Position:
    order: int
    proposal: TradeProposal
    entry_at: datetime
    due: datetime
    due_reason: ExitReason
    bars_held: int = 0
    unobserved: list[datetime] = field(default_factory=list)
    last_price: Decimal | None = None
    last_price_at: datetime | None = None

    @property
    def half_spread(self) -> Decimal:
        assert self.proposal.spread is not None
        return self.proposal.spread / 2

    def exit_side(self, price: Decimal) -> Decimal:
        """What the position could leave at when the bar price is `price`:
        the estimated bid for a LONG, the estimated ask for a SHORT."""
        return price - self.proposal.sign * self.half_spread

    def beyond_stop(self, exit_price: Decimal) -> bool:
        assert self.proposal.stop is not None
        return (exit_price - self.proposal.stop) * self.proposal.sign <= 0

    def beyond_target(self, exit_price: Decimal) -> bool:
        assert self.proposal.target is not None
        return (exit_price - self.proposal.target) * self.proposal.sign >= 0


def _label(signal: TradeSignal) -> str:
    reasons = getattr(signal, "reasons", ())
    if signal.outcome is Outcome.DATA_REJECTED and reasons:
        return str(getattr(reasons[0], "value", reasons[0]))
    return signal.outcome.value


def run_account(
    strategy_id: str,
    parameters_sha256: str,
    signals: Mapping[str, Mapping[datetime, TradeSignal]],
    bars: Mapping[str, Sequence[PriceBar]],
    *,
    start: datetime,
    end: datetime,
    scenario: Scenario = BASE,
    limits: RiskLimits | None = None,
    calendar: Calendar = is_week_open,
) -> BacktestRun:
    """One strategy's account from `start` to `end` (full hours, inclusive)
    over precomputed `signals` (per instrument, keyed by the instant their
    candle closed). `calendar` says which hours are expected to have a bar."""
    start, end = _grid(start, end)
    limits = limits or RiskLimits()
    known = {}
    for name in list(bars) + list(signals):
        rules = instrument_for(name)
        if rules is None:
            raise ValueError(f"{name}: solo EUR/USD y GBP/USD")
        known[rules.symbol] = name
    symbols = sorted(known)  # EURUSD before GBPUSD
    tapes = {symbol: _Tape(bars.get(known[symbol], ())) for symbol in symbols}
    pending = {symbol: signals.get(known[symbol], {}) for symbol in symbols}
    for symbol in symbols:
        for moment, signal in pending[symbol].items():
            if signal.candle_close != moment:
                raise ValueError("each signal must be keyed by the instant its candle closed")
    execution, financing = scenario.execution(strategy_id), scenario.financing

    balance, day_start, realized_today = limits.capital, limits.capital, _ZERO
    day: date = start.date()
    positions: dict[str, _Position] = {}
    coverage: dict[str, Counter[str]] = {symbol: Counter() for symbol in symbols}
    decisions: list[Decision] = []
    trades: list[Trade] = []
    equity: list[EquityPoint] = []

    def expected(label: datetime) -> bool:
        return calendar(label) and calendar(label + HOUR - _INSTANT)

    def book(symbol: str, position: _Position, moment: datetime, **values: object) -> None:
        nonlocal balance, realized_today
        result, unknown = values.get("result"), values.get("indeterminate")
        if isinstance(result, TradeResult):
            pnl, status = result.net_pnl, TradeStatus.CLOSED
        else:
            assert isinstance(unknown, IndeterminateResult)
            pnl = unknown.equity_pnl if scenario.indeterminate_policy == CONSERVATIVE else unknown.optimistic.net_pnl
            status = TradeStatus.INDETERMINATE
        balance += pnl
        realized_today += pnl
        del positions[symbol]
        trades.append(_trade(strategy_id, symbol, position, status, recorded_at=moment, booked_pnl=pnl, **values))

    def hole(position: _Position, moment: datetime, detail: str) -> Uncertainty:
        return Uncertainty(DATA_HOLE, position.unobserved[0], moment, tuple(position.unobserved), position.last_price, position.last_price_at, detail=detail)

    moment = start
    while moment <= end:
        # 1. the bar that closed now
        for symbol in symbols:
            position = positions.get(symbol)
            if position is None or position.entry_at >= moment:
                continue
            closed = tapes[symbol].closed_at(moment)
            if closed is None:
                if (position.unobserved or expected(moment - HOUR)) and (moment - HOUR) not in position.unobserved:
                    position.unobserved.append(moment - HOUR)
                continue
            p = position.proposal
            position.bars_held += 1
            position.last_price, position.last_price_at = closed.close, moment
            stop = position.beyond_stop(position.exit_side(closed.low if p.direction == "LONG" else closed.high))
            target = position.beyond_target(position.exit_side(closed.high if p.direction == "LONG" else closed.low))
            if stop and target:
                detail = f"la vela de {(moment - HOUR).isoformat()} alcanzó el stop y el objetivo; su orden no es observable"
                book(symbol, position, moment, indeterminate=indeterminate(p, moment, detail, financing=financing),
                     uncertainty=Uncertainty(SAME_BAR, moment - HOUR, moment, detail=detail))
            elif stop or target:
                book(symbol, position, moment, result=settle(p, ExitReason.STOP if stop else ExitReason.TARGET, moment, financing=financing),
                     exit_at=moment, exit_price_source=AT_LEVEL)

        # 2. the accounting day
        if moment.date() != day:
            day, day_start, realized_today = moment.date(), balance, _ZERO

        # 3. with the open of the bar that starts now
        for symbol in symbols:
            position = positions.get(symbol)
            if position is None or position.entry_at >= moment:
                continue
            p, opening = position.proposal, tapes[symbol].open_at(moment)
            if position.unobserved:
                if opening is not None:
                    detail = (f"{len(position.unobserved)} horas sin observación desde {position.unobserved[0].isoformat()}; "
                              "no se sabe si se alcanzó el stop, el objetivo o una salida obligatoria")
                    unknown = IndeterminateResult(
                        proposal_id=p.proposal_id, exit_time=moment, detail=detail,
                        conservative=settle(p, ExitReason.STOP, moment, market_price=position.exit_side(opening), financing=financing),
                        optimistic=settle(p, ExitReason.TARGET, moment, financing=financing))
                    book(symbol, position, moment, indeterminate=unknown, uncertainty=replace(hole(position, moment, detail), first_price_after=opening))
                continue
            if opening is not None and position.beyond_stop(position.exit_side(opening)):
                book(symbol, position, moment, result=settle(p, ExitReason.STOP, moment, market_price=position.exit_side(opening), financing=financing),
                     exit_at=moment, exit_price_source=AT_OPEN)
            elif opening is not None and position.beyond_target(position.exit_side(opening)):
                book(symbol, position, moment, result=settle(p, ExitReason.TARGET, moment, financing=financing), exit_at=moment, exit_price_source=AT_LEVEL)
            elif moment >= position.due:
                closed = tapes[symbol].closed_at(moment)
                price, source = (opening, AT_OPEN) if opening is not None else (None if closed is None else closed.close, AT_PREVIOUS_CLOSE)
                if price is None:
                    position.unobserved.append(moment)  # due, and no price to leave at
                else:
                    book(symbol, position, moment, result=settle(p, position.due_reason, moment, market_price=position.exit_side(price), financing=financing),
                         exit_at=moment, exit_price_source=source)

        # 4. entries
        for symbol in symbols:
            signal = pending[symbol].get(moment)
            if signal is None:
                continue
            coverage[symbol][_label(signal)] += 1
            if not signal.is_signal:
                continue
            rules, opening = scenario.rules(symbol), tapes[symbol].open_at(moment)
            quote = None if opening is None else estimated_from_mid(rules.symbol, moment, opening, rules.assumed_spread, QUOTE_SOURCE)
            account = AccountState(equity=balance, day_start_equity=day_start, realized_pnl_today=realized_today, open_positions=[
                OpenPosition(s, p.proposal.estimated_loss_at_stop or _ZERO, p.proposal.margin_required or _ZERO, "backtest", -int(p.proposal.sign))
                for s, p in sorted(positions.items())])
            proposal = build_proposal(signal, quote, moment, account=account, execution=execution, limits=limits, financing=financing, rules=rules)
            order = len(decisions) + 1
            decisions.append(Decision(order, moment, symbol, signal.outcome.value, signal.signal_id, signal.candle_close,
                                      None if quote is None else moment, proposal))
            if proposal.accepted:
                weekly, timed = weekly_close(moment), time_exit_due(moment, execution.max_holding_bars)
                positions[symbol] = _Position(order, proposal, moment, min(weekly, timed), ExitReason.WEEKLY_CLOSE if weekly <= timed else ExitReason.TIME)

        # 5. valuation
        value, stale = balance, False
        for symbol, position in positions.items():
            closed = tapes[symbol].closed_at(moment)
            if closed is not None:
                position.last_price, position.last_price_at = closed.close, moment
            stale = stale or closed is None
            value += _unrealized(position, moment, financing)
        equity.append(EquityPoint(moment, balance, value, len(positions), stale))
        moment += HOUR

    for symbol, position in sorted(positions.items()):
        still = None if not position.unobserved else replace(hole(position, end, "los precios no habían vuelto al terminar el período"), end=None)
        trades.append(_trade(strategy_id, symbol, position, TradeStatus.OPEN_AT_END, uncertainty=still, valuation_price=position.last_price,
                             valuation_at=position.last_price_at, valuation_stale=position.last_price_at != end,
                             unrealized_pnl=_unrealized(position, end, financing)))
    return BacktestRun(
        engine=ENGINE_ID, strategy_id=strategy_id, parameters_sha256=parameters_sha256, scenario=scenario, limits=limits, start=start, end=end,
        symbols=tuple(symbols), coverage={symbol: dict(sorted(counts.items())) for symbol, counts in coverage.items()},
        decisions=tuple(decisions), trades=tuple(sorted(trades, key=lambda t: t.order)), equity=tuple(equity), final_balance=balance,
    )


def _unrealized(position: _Position, moment: datetime, financing: FinancingAssumption) -> Decimal:
    """The open position at the exit side of the last close known, less its
    commission and the financing nights crossed so far. No exit slippage."""
    p = position.proposal
    if position.last_price is None:
        return _ZERO
    assert p.entry is not None and p.units is not None and p.commission_per_unit is not None
    per_unit = (position.exit_side(position.last_price) - p.entry) * p.sign - p.commission_per_unit \
        - rollover_nights(position.entry_at, moment) * financing.per_unit_night(p.entry)
    return (Decimal(p.units) * per_unit).quantize(_CENT, rounding=ROUND_HALF_EVEN)


def _trade(strategy_id: str, symbol: str, position: _Position, status: TradeStatus, **values: object) -> Trade:
    p = position.proposal
    assert p.direction is not None
    return Trade(order=position.order, strategy_id=strategy_id, symbol=symbol, direction=p.direction, proposal=p, signal_candle_close_at=p.candle_close,
                 quote_available_at=position.entry_at, entry_at=position.entry_at, status=status, bars_held=position.bars_held, **values)  # type: ignore[arg-type]


def run(
    strategy: Strategy, bars: Mapping[str, Sequence[PriceBar]], *, start: datetime, end: datetime, data_kind: QuoteKind, data_source: str,
    scenario: Scenario = BASE, limits: RiskLimits | None = None, calendar: Calendar = is_week_open,
    signals: Mapping[str, Mapping[datetime, TradeSignal]] | None = None,
) -> BacktestRun:
    """Both passes. Pass `signals` (from evaluate_signals) to reuse one
    signal pass across scenarios."""
    if signals is None:
        signals = evaluate_signals(strategy, bars, start=start, end=end, data_kind=data_kind, data_source=data_source, calendar=calendar)
    return run_account(strategy.strategy_id, strategy.parameters_sha256, signals, bars, start=start, end=end, scenario=scenario, limits=limits, calendar=calendar)
