"""Trade proposal of the experimental Forex strategies (ema-rsi-atr-forex
v0.1.0 and ema-3-9-20-50-120-forex v0.1.0): from a confirmed signal and a
quote to concrete entry, stop, target and size - and the arithmetic of how
such a HYPOTHETICAL trade settles. The signal is any evaluation that meets
the contract signals.TradeSignal; the proposal keeps the signal's own
identifier, so the two strategies never share a proposal.

EXPERIMENTAL, NOT VALIDATED. Nothing here is an order: no broker, no
execution, no monitor, no backtest loop. `build_proposal` and `settle` are
pure functions; walking prices to find the exit belongs to later deliveries.

All prices are per unit of the base currency, in the quote currency (USD
for EUR/USD and GBP/USD, the only instruments supported: P&L is in USD with
no conversion). sign = +1 for LONG, -1 for SHORT.

  slip   adverse slippage per market fill (assumption, per instrument)
  c      round-trip commission per unit = commission_per_100k / 100 000
  phi    financing per unit per night   = E x annual_rate / days_per_year
  N      financing nights of the planned horizon (ema_cross_sessions):
         the rollovers crossed if the position lasted its maximum - the
         conservative case; F = N x phi

  Entry    E  = ask + slip (LONG) | bid - slip (SHORT), rounded adversely to the tick
  Stop     SL = E - sign x 1.5 x ATR14, rounded to the nearest tick; D = |E - SL|
           A signal that carries a `structural_stop` (signals.StructuralStop,
           ema-pullback-forex) replaces that line: SL = its reference level,
           moved out to floor x ATR14 from E if it is nearer, and no trade
           (RETROCESO_DEMASIADO_PROFUNDO) if it is further than cap x ATR14.
           Everything below is the same for every strategy.
           triggers on the exit side (LONG: bid <= SL; SHORT: ask >= SL)
           fills at SL -/+ slip, or beyond it if the market gapped through
  Loss     l  = D + slip + c + F          "estimated loss per unit at the stop"
  Target   T  = smallest tick multiple with T >= 3 x (D + slip) + 4 x (c + F)
           TP = E + sign x T; triggers on the exit side, fills exactly at TP
  Gain     g  = T - c - F                 so that g >= 3 x l
  Units    floor(risk_fraction x equity / l / lot_step) x lot_step

No cost is counted twice: the spread is paid only through the prices (in
at the ask, out at the bid, or the reverse) and is reported as information,
never subtracted; the entry slippage is inside E; the stop's slippage is
the only slippage in l; a target fills without slippage; commission and
financing are the only amounts subtracted outside prices - once from the
gain and once added to the loss, hence the factor 4.

Three different money figures, never to be confused:
  risk_budget             risk_fraction x equity: what may be put at risk;
  estimated_loss_at_stop  units x l: the loss if the stop fills normally -
                          at most the budget, after rounding units down;
  realized loss           TradeResult.net_pnl: what actually happens. A gap
                          or extraordinary slippage can make it LARGER than
                          both (loss_beyond_estimate, exceeded_budget). A
                          stop does not cap the loss.

Financing is configurable and carries its own status:
  NO_VERIFICADA          no rate given: F = 0, the proposal is flagged
                         COSTO_FINANCIACION_NO_VERIFICADO and makes no claim
                         about a net ratio after financing;
  ESCENARIO_ILUSTRATIVO  a rate given for illustration: used in the numbers,
                         same flag, still no claim about real net 3R;
  VERIFICADA             a rate with a documented source, explicitly marked
                         verified by whoever authorizes it.
The target is fixed when the proposal is made, with the planned (maximum)
nights. It is never moved afterwards: a trade that closes sooner pays fewer
nights, and its realized result reflects the nights actually crossed.

Provenance: a proposal's kind is the weakest of its signal's and its
quote's (OBSERVADA > ESTIMADA > SINTETICA). A proposal that is not OBSERVADA
is flagged PRECIO_ESTIMADO_NO_EJECUTABLE (or DATOS_SINTETICOS): a simulation
on estimated prices, never an executable quote. Even an OBSERVADA proposal
is hypothetical - `hypothetical` is always True.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from enum import Enum
from typing import Sequence

from investment_committee.market_intelligence.quotes import Quote, QuoteKind, to_utc

from .ema_cross_sessions import entry_block, horizon_end, rollover_nights
from .opportunities import AccountState, RiskLimits, daily_loss_budget
from .signals import Outcome, StructuralStop, TradeSignal

_CENT = Decimal("0.01")
_ZERO = Decimal("0")
_HUNDRED_THOUSAND = Decimal("100000")
_KIND_STRENGTH = {QuoteKind.OBSERVED: 2, QuoteKind.ESTIMATED: 1, QuoteKind.SYNTHETIC: 0}
FINANCING_NOT_VERIFIED = "COSTO_FINANCIACION_NO_VERIFICADO"
ESTIMATED_PRICE = "PRECIO_ESTIMADO_NO_EJECUTABLE"
SYNTHETIC_DATA = "DATOS_SINTETICOS"


@dataclass(frozen=True)
class InstrumentRules:
    """An instrument and its ASSUMED execution values (no bid/ask history
    has been measured yet)."""

    symbol: str  # six letters, e.g. "EURUSD"
    display_name: str
    base_currency: str
    quote_currency: str
    tick: Decimal = Decimal("0.00001")
    pip: Decimal = Decimal("0.0001")
    slippage: Decimal = Decimal("0.00002")  # per market fill, adverse
    max_spread: Decimal = Decimal("0.00025")  # wider than this: no entry
    assumed_spread: Decimal = Decimal("0.00010")  # only to build ESTIMATED quotes from a mid price
    lot_step: int = 1000
    min_units: int = 1000


EURUSD_RULES = InstrumentRules("EURUSD", "EUR/USD", "EUR", "USD")
GBPUSD_RULES = InstrumentRules("GBPUSD", "GBP/USD", "GBP", "USD", slippage=Decimal("0.00003"), max_spread=Decimal("0.00030"),
                               assumed_spread=Decimal("0.00015"))
INSTRUMENTS = {rules.symbol: rules for rules in (EURUSD_RULES, GBPUSD_RULES)}


def instrument_for(symbol: str) -> InstrumentRules | None:
    """Rules for "EURUSD", "EURUSD=X", "EUR/USD" or "EUR_USD"; None if unsupported."""
    return INSTRUMENTS.get(symbol.upper().replace("=X", "").replace("/", "").replace("_", ""))


@dataclass(frozen=True)
class ExecutionParameters:
    """Hypotheses fixed a priori; any change is a new strategy version."""

    stop_atr_multiple: Decimal = Decimal("1.5")
    min_net_reward_risk: Decimal = Decimal("3")
    max_holding_bars: int = 72
    max_quote_delay: timedelta = timedelta(minutes=5)  # quote later than this after the candle's close: signal expired
    max_quote_age: timedelta = timedelta(seconds=30)  # between the quote's own time and its reception
    max_drift_atr: Decimal = Decimal("0.25")  # quote mid further than this from the signal candle's close: discarded
    commission_per_100k: Decimal = Decimal("0")  # quote currency, round trip


class FinancingStatus(str, Enum):
    NOT_VERIFIED = "NO_VERIFICADA"
    ILLUSTRATIVE = "ESCENARIO_ILUSTRATIVO"
    VERIFIED = "VERIFICADA"


@dataclass(frozen=True)
class FinancingAssumption:
    """A cost-only overnight financing rate (both sides pay, nothing is
    credited). Default: no rate, because none has a verifiable source yet."""

    annual_rate: Decimal | None = None
    source: str = ""
    verified: bool = False
    days_per_year: int = 360

    def __post_init__(self) -> None:
        if self.annual_rate is not None and self.annual_rate < 0:
            raise ValueError("the financing rate is a cost: it must not be negative")
        if self.annual_rate is not None and not self.source.strip():
            raise ValueError("a financing rate needs a source, even if only 'escenario ilustrativo'")
        if self.verified and self.annual_rate is None:
            raise ValueError("a verified financing assumption needs a rate")

    @property
    def status(self) -> FinancingStatus:
        if self.annual_rate is None:
            return FinancingStatus.NOT_VERIFIED
        return FinancingStatus.VERIFIED if self.verified else FinancingStatus.ILLUSTRATIVE

    def per_unit_night(self, entry_price: Decimal) -> Decimal:
        return _ZERO if self.annual_rate is None else entry_price * self.annual_rate / Decimal(self.days_per_year)


class ProposalStatus(str, Enum):
    ACCEPTED = "ACEPTADA"
    REJECTED = "RECHAZADA"


class Rejection(str, Enum):
    NOT_A_SIGNAL = "SIN_SEÑAL_CONFIRMADA"
    UNSUPPORTED_INSTRUMENT = "INSTRUMENTO_NO_SOPORTADO"
    NO_QUOTE = "COTIZACION_AUSENTE"
    WRONG_INSTRUMENT = "COTIZACION_DE_OTRO_INSTRUMENTO"
    INVALID_QUOTE = "COTIZACION_INVALIDA"
    QUOTE_BEFORE_CLOSE = "COTIZACION_ANTERIOR_AL_CIERRE"
    SIGNAL_EXPIRED = "SEÑAL_EXPIRADA"
    STALE_QUOTE = "COTIZACION_DESACTUALIZADA"
    MARKET_CLOSED = "MERCADO_CERRADO"
    BLOCKED_FRIDAY = "ENTRADA_BLOQUEADA_VIERNES"
    BLOCKED_ROLLOVER = "ENTRADA_BLOQUEADA_ROLLOVER"
    SPREAD_TOO_WIDE = "SPREAD_EXCESIVO"
    PRICE_MOVED = "PRECIO_DESPLAZADO"
    RATIO_NOT_COMPUTABLE = "RELACION_NO_CALCULABLE"
    POSITION_OPEN = "POSICION_ABIERTA"
    POSITION_TOO_SMALL = "TAMAÑO_INSUFICIENTE"
    INSUFFICIENT_MARGIN = "MARGEN_INSUFICIENTE"
    DAILY_LOSS_LIMIT = "LIMITE_PERDIDA_DIARIA"
    USD_EXPOSURE = "EXPOSICION_USD"
    STOP_TOO_FAR = "RETROCESO_DEMASIADO_PROFUNDO"  # a structural stop further from the entry than its cap


@dataclass(frozen=True)
class TradeProposal:
    proposal_id: str  # the signal's evaluation key: one proposal per strategy, instrument and candle
    signal_id: str | None
    symbol: str
    direction: str | None  # "LONG" | "SHORT"
    status: ProposalStatus
    rejections: Sequence[Rejection]
    detail: str
    flags: Sequence[str]
    data_kind: QuoteKind  # the weakest of the signal's and the quote's
    hypothetical: bool
    candle_close: datetime
    # the quote
    quote_time: datetime | None = None
    quote_received_at: datetime | None = None
    quote_source: str | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    spread: Decimal | None = None
    atr: Decimal | None = None
    # levels
    entry: Decimal | None = None
    stop: Decimal | None = None
    target: Decimal | None = None
    stop_distance: Decimal | None = None  # D
    target_distance: Decimal | None = None  # T
    stop_distance_pips: Decimal | None = None
    target_distance_pips: Decimal | None = None
    target_distance_atr: Decimal | None = None
    # costs per unit
    slippage: Decimal | None = None
    commission_per_unit: Decimal | None = None
    financing_status: FinancingStatus = FinancingStatus.NOT_VERIFIED
    financing_per_unit_night: Decimal | None = None
    planned_nights: int | None = None
    planned_financing_per_unit: Decimal | None = None
    horizon_end: datetime | None = None  # the latest moment the position can still be open
    # risk and reward per unit
    loss_per_unit: Decimal | None = None  # l
    gain_per_unit: Decimal | None = None  # g
    gross_reward_risk: Decimal | None = None  # T / D
    net_reward_risk_before_financing: Decimal | None = None
    net_reward_risk: Decimal | None = None  # with the configured financing; None when no rate was given
    # size and money (quote currency = USD)
    equity: Decimal | None = None
    risk_budget: Decimal | None = None
    units: int | None = None
    estimated_loss_at_stop: Decimal | None = None
    expected_net_profit: Decimal | None = None
    planned_financing_cost: Decimal | None = None
    commission_cost: Decimal | None = None
    stop_slippage_cost: Decimal | None = None
    entry_slippage_cost: Decimal | None = None  # information: already inside the entry price
    spread_cost: Decimal | None = None  # information: already inside the prices
    notional: Decimal | None = None
    margin_required: Decimal | None = None

    @property
    def accepted(self) -> bool:
        return self.status is ProposalStatus.ACCEPTED

    @property
    def sign(self) -> Decimal:
        return Decimal("1") if self.direction == "LONG" else Decimal("-1")

    @property
    def net_3r_verified(self) -> bool:
        """True only when the ratio after financing rests on a VERIFIED rate."""
        return self.accepted and self.financing_status is FinancingStatus.VERIFIED and self.net_reward_risk is not None


def weakest(*kinds: QuoteKind) -> QuoteKind:
    return min(kinds, key=lambda k: _KIND_STRENGTH[k])


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_EVEN)


def _to_tick(value: Decimal, tick: Decimal, rounding: str) -> Decimal:
    return (value / tick).to_integral_value(rounding=rounding) * tick


def _adverse(value: Decimal, tick: Decimal, buying: bool) -> Decimal:
    """Round a fill to the tick against the trader: up when buying, down when selling."""
    return _to_tick(value, tick, ROUND_CEILING if buying else ROUND_FLOOR)


def build_proposal(
    signal: TradeSignal,
    quote: Quote | None,
    received_at: datetime | None,
    *,
    account: AccountState,
    execution: ExecutionParameters | None = None,
    limits: RiskLimits | None = None,
    financing: FinancingAssumption | None = None,
    rules: InstrumentRules | None = None,
) -> TradeProposal:
    """The concrete proposal for `signal` at `quote`, or a rejection with its
    reasons. `received_at` is when the quote reached us (for a simulation on
    estimated prices, the moment the estimate refers to). `rules` replaces
    the instrument's assumed execution values (cost-sensitivity scenarios);
    it must be for the signal's instrument."""
    execution, limits, financing = execution or ExecutionParameters(), limits or RiskLimits(), financing or FinancingAssumption()
    known = instrument_for(signal.symbol)
    if rules is not None and (known is None or rules.symbol != known.symbol):
        raise ValueError("rules must be for the signal's instrument")
    rules = rules or known
    kind = signal.data_kind if quote is None else weakest(signal.data_kind, quote.kind)
    flags = [] if kind is QuoteKind.OBSERVED else [ESTIMATED_PRICE if kind is QuoteKind.ESTIMATED else SYNTHETIC_DATA]
    base = dict(
        proposal_id=signal.evaluation_key, signal_id=signal.signal_id, symbol=signal.symbol,
        direction=signal.outcome.value if signal.is_signal else None, data_kind=kind, hypothetical=True, candle_close=signal.candle_close,
    )

    def reject(reasons: Sequence[Rejection], detail: str, **values: object) -> TradeProposal:
        return TradeProposal(status=ProposalStatus.REJECTED, rejections=tuple(reasons), detail=detail, flags=tuple(flags), **base, **values)  # type: ignore[arg-type]

    if not signal.is_signal or signal.outcome not in (Outcome.LONG, Outcome.SHORT):
        return reject([Rejection.NOT_A_SIGNAL], f"la evaluación es {signal.outcome.value}")
    if rules is None or rules.quote_currency != "USD":
        return reject([Rejection.UNSUPPORTED_INSTRUMENT], f"{signal.symbol}: solo EUR/USD y GBP/USD en esta versión")
    if quote is None or received_at is None:
        return reject([Rejection.NO_QUOTE], "no hay una cotización válida para simular la entrada")
    received = to_utc(received_at, "received_at")
    seen = dict(quote_time=quote.timestamp, quote_received_at=received, quote_source=quote.source, bid=quote.bid, ask=quote.ask,
                spread=quote.spread, atr=signal.atr)
    if instrument_for(quote.symbol) is not known:
        return reject([Rejection.WRONG_INSTRUMENT], f"cotización de {quote.symbol} para una señal de {signal.symbol}", **seen)
    if quote.bid <= 0 or quote.bid > quote.ask:
        return reject([Rejection.INVALID_QUOTE], f"bid {quote.bid}, ask {quote.ask}", **seen)
    if quote.timestamp < signal.candle_close:
        return reject([Rejection.QUOTE_BEFORE_CLOSE], "la cotización es anterior al cierre de la vela de la señal", **seen)
    if quote.timestamp - signal.candle_close > execution.max_quote_delay:
        return reject([Rejection.SIGNAL_EXPIRED], f"la cotización llegó {quote.timestamp - signal.candle_close} después del cierre", **seen)
    age = received - quote.timestamp
    if age < timedelta(0) or age > execution.max_quote_age:
        return reject([Rejection.STALE_QUOTE], f"entre la cotización y su recepción pasaron {age}", **seen)
    blocked = entry_block(quote.timestamp)
    if blocked is not None:
        return reject([Rejection(blocked)], "el horario no admite entradas nuevas", **seen)
    if quote.spread > rules.max_spread:
        return reject([Rejection.SPREAD_TOO_WIDE], f"spread {quote.spread} > {rules.max_spread}", **seen)
    atr, last_close = signal.atr, signal.last_close
    if atr is None or last_close is None or atr <= 0:
        return reject([Rejection.RATIO_NOT_COMPUTABLE], "no hay un ATR positivo", **seen)
    if abs(quote.mid - last_close) > execution.max_drift_atr * atr:
        return reject([Rejection.PRICE_MOVED], f"el precio se desplazó {abs(quote.mid - last_close)} desde el cierre (máximo {execution.max_drift_atr} ATR)", **seen)

    long_ = signal.outcome is Outcome.LONG
    sign = Decimal("1") if long_ else Decimal("-1")
    slip, tick = rules.slippage, rules.tick
    entry = _adverse((quote.ask + slip) if long_ else (quote.bid - slip), tick, buying=long_)
    structural: StructuralStop | None = getattr(signal, "structural_stop", None)
    if structural is None:
        stop = _to_tick(entry - sign * execution.stop_atr_multiple * atr, tick, ROUND_HALF_EVEN)
    else:  # the stop is the signal's level, never nearer to the entry than its floor
        stop = _to_tick(structural.reference, tick, ROUND_HALF_EVEN)
        if (entry - stop) * sign < structural.floor_atr * atr:
            stop = _to_tick(entry - sign * structural.floor_atr * atr, tick, ROUND_HALF_EVEN)
    stop_distance = abs(entry - stop)
    commission = execution.commission_per_100k / _HUNDRED_THOUSAND
    end = horizon_end(quote.timestamp, execution.max_holding_bars)
    nights = rollover_nights(quote.timestamp, end)
    per_night = financing.per_unit_night(entry)
    planned_financing = nights * per_night
    ratio = execution.min_net_reward_risk
    loss = stop_distance + slip + commission + planned_financing
    target_distance = _to_tick(ratio * (stop_distance + slip) + (ratio + 1) * (commission + planned_financing), tick, ROUND_CEILING)
    target = entry + sign * target_distance
    gain = target_distance - commission - planned_financing
    if financing.status is not FinancingStatus.VERIFIED:
        flags.append(FINANCING_NOT_VERIFIED)
    levels = dict(
        **seen, entry=entry, stop=stop, target=target, stop_distance=stop_distance, target_distance=target_distance,
        stop_distance_pips=stop_distance / rules.pip, target_distance_pips=target_distance / rules.pip, target_distance_atr=target_distance / atr,
        slippage=slip, commission_per_unit=commission, financing_status=financing.status,
        financing_per_unit_night=None if financing.annual_rate is None else per_night, planned_nights=nights,
        planned_financing_per_unit=None if financing.annual_rate is None else planned_financing, horizon_end=end,
        loss_per_unit=loss, gain_per_unit=gain,
    )
    if stop_distance <= 0 or stop <= 0 or target <= 0:
        return reject([Rejection.RATIO_NOT_COMPUTABLE], "no se puede situar un stop y un objetivo válidos", **levels)
    if structural is not None and stop_distance > structural.cap_atr * atr:
        return reject([Rejection.STOP_TOO_FAR], f"el stop estructural queda a {stop_distance} de la entrada (máximo {structural.cap_atr} ATR = "
                      f"{structural.cap_atr * atr})", **levels)
    levels.update(
        gross_reward_risk=target_distance / stop_distance,
        net_reward_risk_before_financing=(target_distance - commission) / (stop_distance + slip + commission),
        net_reward_risk=None if financing.annual_rate is None else gain / loss,
    )

    budget = limits.risk_per_trade * account.equity
    step = Decimal(rules.lot_step)
    units = int((budget / loss / step).to_integral_value(rounding=ROUND_FLOOR) * step)
    count = Decimal(units)
    estimated_loss = _money(count * loss)
    notional = _money(count * entry)
    margin = _money(count * entry / limits.leverage)
    money = dict(
        equity=account.equity, risk_budget=_money(budget), units=units, estimated_loss_at_stop=estimated_loss, expected_net_profit=_money(count * gain),
        planned_financing_cost=None if financing.annual_rate is None else _money(count * planned_financing),
        commission_cost=_money(count * commission), stop_slippage_cost=_money(count * slip), entry_slippage_cost=_money(count * slip),
        spread_cost=_money(count * quote.spread), notional=notional, margin_required=margin,
    )

    reasons: list[Rejection] = []
    notes: list[str] = []
    if any(instrument_for(position.symbol) is known for position in account.open_positions):
        reasons.append(Rejection.POSITION_OPEN)
        notes.append(f"ya hay una posición abierta en {rules.display_name}")
    if units < max(rules.min_units, rules.lot_step):
        reasons.append(Rejection.POSITION_TOO_SMALL)
        notes.append(f"{units} unidades; el mínimo es {rules.min_units}")
    if account.used_margin + margin > (1 - limits.margin_reserve_fraction) * account.equity:
        reasons.append(Rejection.INSUFFICIENT_MARGIN)
        notes.append(f"margen {account.used_margin + margin} sobre un máximo de {(1 - limits.margin_reserve_fraction) * account.equity}")
    if account.open_risk + count * loss > daily_loss_budget(limits, account):
        reasons.append(Rejection.DAILY_LOSS_LIMIT)
        notes.append(f"riesgo abierto {account.open_risk} más {estimated_loss} supera lo que queda del límite diario")
    usd_side = -int(sign)  # the quote currency is USD: a LONG sells dollars
    shared = sum((p.risk_amount for p in account.open_positions if p.usd_direction in (usd_side, 0)), _ZERO)
    if shared + count * loss > limits.max_usd_direction_risk * account.equity:
        reasons.append(Rejection.USD_EXPOSURE)
        notes.append(f"riesgo en la misma dirección del USD {shared} más {estimated_loss}")
    if reasons:
        return reject(reasons, "; ".join(notes), **levels, **money)
    return TradeProposal(status=ProposalStatus.ACCEPTED, rejections=(), detail="", flags=tuple(flags), **base, **levels, **money)  # type: ignore[arg-type]


# --- settlement arithmetic ---


class ExitReason(str, Enum):
    TARGET = "OBJETIVO"
    STOP = "STOP"
    TIME = "TIEMPO"
    WEEKLY_CLOSE = "CIERRE_SEMANAL"


@dataclass(frozen=True)
class TradeResult:
    proposal_id: str
    reason: ExitReason
    gap: bool  # the stop filled beyond its level because the market gapped through it
    entry_time: datetime
    exit_time: datetime
    exit_price: Decimal
    units: int
    gross_pnl: Decimal  # units x (exit - entry) x sign: spread and slippage are inside the prices
    commission_cost: Decimal
    nights: int  # financing nights actually crossed
    financing_cost: Decimal  # 0 when no rate is configured (see financing_status)
    financing_status: FinancingStatus
    net_pnl: Decimal
    r_multiple: Decimal  # net_pnl / estimated_loss_at_stop
    planned_net_profit: Decimal
    estimated_loss_at_stop: Decimal
    risk_budget: Decimal
    loss_beyond_estimate: Decimal  # how much the realized loss exceeds the estimate (0 if it does not)
    exceeded_budget: bool
    data_kind: QuoteKind
    flags: Sequence[str] = field(default_factory=tuple)
    hypothetical: bool = True


def settle(
    proposal: TradeProposal, reason: ExitReason, exit_time: datetime, *, market_price: Decimal | None = None,
    financing: FinancingAssumption | None = None,
) -> TradeResult:
    """What an accepted proposal makes or loses when it leaves at
    `exit_time` for `reason`.

    `market_price` is the exit-side price observed at that moment (the bid
    for a LONG, the ask for a SHORT): required for TIME and WEEKLY_CLOSE
    (market exits, with slippage); optional for a STOP, where a price beyond
    the stop means the market gapped through it and the fill is that worse
    price (plus slippage); ignored for a TARGET, which fills at its level.
    Financing is charged for the nights actually crossed, at the rate of
    `financing` (the one the proposal was built with)."""
    if not proposal.accepted:
        raise ValueError("only an accepted proposal can be settled")
    assert proposal.entry is not None and proposal.stop is not None and proposal.target is not None and proposal.units is not None
    assert proposal.quote_time is not None and proposal.slippage is not None and proposal.commission_per_unit is not None
    financing = financing or FinancingAssumption()
    if financing.status is not proposal.financing_status:
        raise ValueError("settle with the financing assumption the proposal was built with")
    rules = instrument_for(proposal.symbol)
    assert rules is not None
    exit_at = to_utc(exit_time, "exit_time")
    if exit_at < proposal.quote_time:
        raise ValueError("a trade cannot leave before it entered")
    sign, slip, long_ = proposal.sign, proposal.slippage, proposal.direction == "LONG"
    gap = False
    if reason is ExitReason.TARGET:
        fill = proposal.target
    elif reason is ExitReason.STOP:
        level = proposal.stop
        if market_price is not None and (market_price - level) * sign < 0:
            level, gap = market_price, True
        fill = _adverse(level - sign * slip, rules.tick, buying=not long_)
    else:
        if market_price is None:
            raise ValueError("a market exit needs the exit-side price observed at that moment")
        fill = _adverse(market_price - sign * slip, rules.tick, buying=not long_)
    if fill <= 0:
        raise ValueError("the exit price must be positive")

    count = Decimal(proposal.units)
    gross = _money(count * (fill - proposal.entry) * sign)
    commission = _money(count * proposal.commission_per_unit)
    nights = rollover_nights(proposal.quote_time, exit_at)
    financing_cost = _money(count * nights * financing.per_unit_night(proposal.entry))
    net = gross - commission - financing_cost
    estimated, budget = proposal.estimated_loss_at_stop, proposal.risk_budget
    assert estimated is not None and budget is not None and proposal.expected_net_profit is not None
    return TradeResult(
        proposal_id=proposal.proposal_id, reason=reason, gap=gap, entry_time=proposal.quote_time, exit_time=exit_at, exit_price=fill,
        units=proposal.units, gross_pnl=gross, commission_cost=commission, nights=nights, financing_cost=financing_cost,
        financing_status=proposal.financing_status, net_pnl=net, r_multiple=(net / estimated).quantize(Decimal("0.0001")),
        planned_net_profit=proposal.expected_net_profit, estimated_loss_at_stop=estimated, risk_budget=budget,
        loss_beyond_estimate=max(_ZERO, -net - estimated), exceeded_budget=-net > budget, data_kind=proposal.data_kind, flags=tuple(proposal.flags),
    )


@dataclass(frozen=True)
class IndeterminateResult:
    """Stop and target could both have been reached and their order cannot
    be established from observed data (or no observed data cover the
    period). The trade is NOT a valid closed trade for the metrics; both
    possible results are kept."""

    proposal_id: str
    exit_time: datetime
    detail: str
    conservative: TradeResult  # as if the stop came first
    optimistic: TradeResult  # as if the target came first
    valid_for_metrics: bool = False

    @property
    def equity_pnl(self) -> Decimal:
        """What the hypothetical account books, so that later sizing never
        benefits from an unknown: the conservative result."""
        return self.conservative.net_pnl


def indeterminate(proposal: TradeProposal, exit_time: datetime, detail: str, *, financing: FinancingAssumption | None = None) -> IndeterminateResult:
    return IndeterminateResult(
        proposal_id=proposal.proposal_id, exit_time=to_utc(exit_time, "exit_time"), detail=detail,
        conservative=settle(proposal, ExitReason.STOP, exit_time, financing=financing),
        optimistic=settle(proposal, ExitReason.TARGET, exit_time, financing=financing),
    )
