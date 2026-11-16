"""Opportunity Scanner V1: deterministic search for day-trading candidates.

Pure functions over closed bars - no I/O. application/opportunities.py
fetches the data, calls these, and records the result.

Four categories, never mixed:
  1. technical signal      - the strategy's trend + momentum + volatility
                             rules point LONG or SHORT;
  2. operational candidate - that signal also passes every Python control
                             below (costs, per-trade risk, daily loss,
                             aggregate risk, margin, open positions);
  3. validated opportunity - a candidate whose strategy/instrument pair has
                             out-of-sample backtest evidence of positive net
                             expectancy (HistoricalValidation VALIDADA);
  4. simulated trade       - an entry in the trade journal. The scanner
                             NEVER opens one (V1): every scan records
                             simulated_trade_opened = False.
With no validated evidence (the case today: the EUR/USD backtests of
tendencia-momentum-volatilidad v1.0.0 are negative and inconsistent), a
candidate is EXPLORATORY and is never presented as an opportunity.

Money. The strategy sizes a position in the instrument's quote currency
(capital x risk_fraction / risk per unit). The account is in USD, so:
  - quote currency == USD (EUR/USD, GBP/USD): every figure is already USD;
  - base currency == USD (USD/JPY): the strategy runs on the equity
    expressed in JPY, and JPY amounts are converted back at the pair's own
    price, conservatively (the loss at the stop is converted at the lower of
    entry and stop, the gain at the target at the higher of entry and
    target, so neither is overstated in our favour). The capital in JPY
    uses that same conservative rate, so the converted loss at the stop
    stays within the per-trade limit;
  - neither (a cross such as EUR/GBP): NOT ELIGIBLE - no cross rate is
    fetched, and a wrong figure is worse than none.
A stop-loss does not cap the loss exactly at the planned amount: gaps and
slippage beyond the assumed one can make it larger.

Ranking. Only operational candidates are ranked, by a points score whose
components are shown to the user (data freshness, strategy/instrument
fit, costs relative to risk, room to the recent extreme, reward/risk after
costs, margin use, historical evidence). It is a priority order, not a
probability, and there are no expected returns: without validated
evidence the net expectancy is NOT VALIDATED and is not used.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from enum import Enum
from typing import Sequence

from investment_committee.domain import PriceBar

from .models import (
    EURUSD,
    STRATEGY_NAME,
    STRATEGY_VERSION,
    AccountAssumptions,
    InstrumentSpec,
    SegmentedBacktest,
    SignalReason,
    StrategyParameters,
    TradeDirection,
    TradeOutcome,
    TradePlan,
    TradeSignal,
    TradingCosts,
)
from .outcome import resolve_trade
from .strategy import evaluate_signal
from .technical import closed_bars

SCANNER_VERSION = "opportunity-scanner-1.0.0"
ACCOUNT_CURRENCY = "USD"
_CENT = Decimal("0.01")
_TOLERANCE = Decimal("0.01")  # one cent, for rounding in the per-trade check


def fmt(value: Decimal, places: int = 2) -> str:
    """es-CO number: thousands with ".", decimals with ","."""
    return f"{value:,.{places}f}".replace(",", "_").replace(".", ",").replace("_", ".")


# --- instruments ---


@dataclass(frozen=True)
class InstrumentProfile:
    """An instrument as the scanner sees it. `costs` are assumptions (Yahoo
    FX bars carry no bid/ask), set per pair because spreads differ.
    `calibrated` is True only where the strategy was designed and
    backtested (EUR/USD)."""

    spec: InstrumentSpec
    base_currency: str
    costs: TradingCosts
    calibrated: bool = False

    @property
    def symbol(self) -> str:
        return self.spec.symbol

    @property
    def quote_currency(self) -> str:
        return self.spec.quote_currency


GBPUSD = InstrumentSpec(symbol="GBPUSD=X", display_name="GBP/USD", pip_size=Decimal("0.0001"), quote_currency="USD")
USDJPY = InstrumentSpec(symbol="USDJPY=X", display_name="USD/JPY", pip_size=Decimal("0.01"), quote_currency="JPY", price_places=3)

EURUSD_PROFILE = InstrumentProfile(EURUSD, "EUR", TradingCosts(), calibrated=True)
GBPUSD_PROFILE = InstrumentProfile(GBPUSD, "GBP", TradingCosts(spread_pips=Decimal("1.5"), slippage_pips=Decimal("0.3")))
USDJPY_PROFILE = InstrumentProfile(USDJPY, "USD", TradingCosts(spread_pips=Decimal("1.2"), slippage_pips=Decimal("0.3")))
DEFAULT_UNIVERSE: tuple[InstrumentProfile, ...] = (EURUSD_PROFILE, GBPUSD_PROFILE, USDJPY_PROFILE)


class Conversion(str, Enum):
    DIRECT = "DIRECT"  # quote currency is the account currency
    INVERSE = "INVERSE"  # base currency is the account currency: divide by the pair's price


def usd_direction(profile: InstrumentProfile, direction: TradeDirection) -> int:
    """+1 if the position is long USD, -1 if short USD, 0 if USD is not in
    the pair. LONG EUR/USD sells USD (-1); LONG USD/JPY buys USD (+1)."""
    sign = {TradeDirection.LONG: 1, TradeDirection.SHORT: -1}.get(direction, 0)
    if profile.base_currency == "USD":
        return sign
    if profile.quote_currency == "USD":
        return -sign
    return 0


def conversion_for(profile: InstrumentProfile, account_currency: str = ACCOUNT_CURRENCY) -> Conversion | None:
    if profile.quote_currency == account_currency:
        return Conversion.DIRECT
    if profile.base_currency == account_currency:
        return Conversion.INVERSE
    return None


# --- strategies ---


class ScanStrategy(ABC):
    """A deterministic strategy the scanner can run. A new strategy is a new
    subclass; its parameters are fixed per version (no tuning here)."""

    name: str
    version: str
    timeframe: timedelta
    interval_code: str
    history_period: str
    min_reward_risk: Decimal  # net reward/risk the strategy requires after costs
    # Instruments the strategy was designed and tested on (scoring only).
    calibrated_symbols: frozenset[str] = frozenset()

    @abstractmethod
    def unsupported_reason(self, profile: InstrumentProfile) -> str | None:
        """Why this strategy cannot run correctly on `profile`, or None."""

    @abstractmethod
    def evaluate(self, bars: Sequence[PriceBar], as_of: datetime, *, signal_id: str, profile: InstrumentProfile, account: AccountAssumptions) -> TradeSignal:
        """The signal at `as_of` from bars closed by then."""


class TrendMomentumVolatilityStrategy(ScanStrategy):
    """tendencia-momentum-volatilidad v1.0.0, unchanged: same rules, same
    parameters as /trade and /backtest. An EXPERIMENTAL baseline - its
    backtests are negative."""

    name = STRATEGY_NAME
    version = STRATEGY_VERSION
    calibrated_symbols = frozenset({EURUSD.symbol})

    def __init__(self, parameters: StrategyParameters | None = None) -> None:
        self.parameters = parameters or StrategyParameters()
        self.timeframe = self.parameters.timeframe
        self.interval_code = self.parameters.interval_code
        self.history_period = "30d"
        self.min_reward_risk = self.parameters.min_net_reward_risk

    def unsupported_reason(self, profile: InstrumentProfile) -> str | None:
        if conversion_for(profile) is None:
            return (
                f"ni la divisa base ({profile.base_currency}) ni la de cotización ({profile.quote_currency}) es USD: "
                "haría falta un tipo de cambio cruzado que el scanner no descarga"
            )
        return None

    def evaluate(self, bars: Sequence[PriceBar], as_of: datetime, *, signal_id: str, profile: InstrumentProfile, account: AccountAssumptions) -> TradeSignal:
        return evaluate_signal(
            bars, as_of, signal_id=signal_id, instrument=profile.spec, parameters=self.parameters, costs=profile.costs, account=account
        )


# --- risk configuration and account state ---


@dataclass(frozen=True)
class RiskLimits:
    """Python-only controls; nothing an LLM returns can change them."""

    capital: Decimal = Decimal("10000")  # hypothetical account, USD
    risk_per_trade: Decimal = Decimal("0.005")  # of equity, to the stop, costs included
    max_daily_loss: Decimal = Decimal("0.015")  # of the day's reference equity
    # Shared USD exposure: positions that are all long USD (or all short USD)
    # move together; their combined risk is capped (EUR/USD and GBP/USD
    # LONG are two bets against the same dollar).
    max_usd_direction_risk: Decimal = Decimal("0.01")  # of equity
    max_visible: int = 3
    leverage: Decimal = Decimal("30")
    margin_reserve_fraction: Decimal = Decimal("0.25")
    lot_step_units: int = 1000
    min_units: int = 1000

    def __post_init__(self) -> None:
        if self.capital <= 0:
            raise ValueError("capital must be positive")
        if not (Decimal("0") < self.risk_per_trade <= Decimal("0.02")):
            raise ValueError("risk_per_trade must be in (0, 2 %]")
        if not (self.risk_per_trade <= self.max_daily_loss <= Decimal("0.05")):
            raise ValueError("max_daily_loss must be between risk_per_trade and 5 %")
        if not (self.risk_per_trade <= self.max_usd_direction_risk <= self.max_daily_loss):
            raise ValueError("max_usd_direction_risk must be between risk_per_trade and max_daily_loss")
        if not (1 <= self.max_visible <= 3):
            raise ValueError("max_visible must be between 1 and 3")

    def account_in(self, capital: Decimal) -> AccountAssumptions:
        """The strategy's AccountAssumptions for a capital already expressed
        in the instrument's quote currency."""
        return AccountAssumptions(
            capital=capital,
            risk_fraction=self.risk_per_trade,
            leverage=self.leverage,
            lot_step_units=self.lot_step_units,
            min_units=self.min_units,
            margin_reserve_fraction=self.margin_reserve_fraction,
        )


@dataclass(frozen=True)
class OpenPosition:
    """An open simulated position, in account currency."""

    symbol: str
    risk_amount: Decimal
    margin: Decimal
    source: str = "diario de operaciones simuladas"
    usd_direction: int = 0  # +1 long USD, -1 short USD, 0 unknown or no USD


@dataclass(frozen=True)
class AccountState:
    """The hypothetical account at scan time. `realized_pnl_today` is P&L
    already closed since 00:00 UTC (negative = loss)."""

    equity: Decimal
    day_start_equity: Decimal
    realized_pnl_today: Decimal = Decimal("0")
    open_positions: Sequence[OpenPosition] = ()
    notes: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "open_positions", tuple(self.open_positions))
        object.__setattr__(self, "notes", tuple(self.notes))

    @classmethod
    def fresh(cls, capital: Decimal) -> "AccountState":
        return cls(equity=capital, day_start_equity=capital)

    @property
    def open_risk(self) -> Decimal:
        return sum((p.risk_amount for p in self.open_positions), Decimal("0"))

    @property
    def used_margin(self) -> Decimal:
        return sum((p.margin for p in self.open_positions), Decimal("0"))


def daily_loss_budget(limits: RiskLimits, account: AccountState) -> Decimal:
    """Loss still allowed today: the daily limit minus today's realized
    losses (gains do not enlarge it)."""
    limit = limits.max_daily_loss * account.day_start_equity
    return limit - max(Decimal("0"), -account.realized_pnl_today)


# --- historical evidence ---


class ValidationStatus(str, Enum):
    VALIDADA = "VALIDADA"
    NO_VALIDADA = "NO_VALIDADA"


@dataclass(frozen=True)
class HistoricalValidation:
    status: ValidationStatus
    detail: str
    source: str | None = None

    @property
    def validated(self) -> bool:
        return self.status is ValidationStatus.VALIDADA


NOT_VALIDATED = HistoricalValidation(
    ValidationStatus.NO_VALIDADA,
    "no hay un backtest fuera de muestra con expectativa neta positiva registrado para esta estrategia y este "
    "instrumento; los backtests de EUR/USD de la estrategia fueron negativos e inconsistentes",
)


def assess_segmented_backtest(result: SegmentedBacktest, min_trades: int = 30) -> HistoricalValidation:
    """VALIDADA only if EVERY out-of-sample segment (all but the first,
    development, segment) has at least `min_trades` trades, a positive net
    expectancy and a profit factor above 1. Nothing is tuned on any segment."""
    source = f"backtest {result.full.strategy_name} v{result.full.strategy_version} en {result.full.instrument.display_name}"
    out_of_sample = list(result.segments[1:])
    if not out_of_sample:
        return HistoricalValidation(ValidationStatus.NO_VALIDADA, "el backtest no tiene segmentos fuera de muestra", source)
    for segment in out_of_sample:
        metrics = segment.metrics
        if metrics.trades < min_trades:
            return HistoricalValidation(
                ValidationStatus.NO_VALIDADA, f"el segmento de {segment.label} tiene {metrics.trades} operaciones (mínimo {min_trades})", source
            )
        if metrics.expectancy is None or metrics.expectancy <= 0:
            return HistoricalValidation(ValidationStatus.NO_VALIDADA, f"la expectativa neta del segmento de {segment.label} no es positiva", source)
        if metrics.profit_factor is None or metrics.profit_factor <= 1:
            return HistoricalValidation(ValidationStatus.NO_VALIDADA, f"el factor de beneficio del segmento de {segment.label} no supera 1", source)
    return HistoricalValidation(ValidationStatus.VALIDADA, "todos los segmentos fuera de muestra tienen expectativa neta positiva", source)


# --- data checks ---


@dataclass(frozen=True)
class DataCheck:
    source: str
    fetched_at: datetime
    bars_received: int
    future_bars_dropped: int  # stamped after the scan moment
    forming_bars_excluded: int  # opened but not yet closed at the scan moment
    closed_bars: int
    first_bar_open: datetime | None
    last_closed_bar_close: datetime | None
    irregular_bars_dropped: int = 0  # not aligned to the timeframe (e.g. a provider "live" row)


def is_aligned(moment: datetime, timeframe: timedelta) -> bool:
    """A bar label on the timeframe's grid in UTC (e.g. 13:00:00 for 1h).
    Yahoo labels 1-hour bars by their OPEN time on that grid."""
    return int(moment.timestamp()) % int(timeframe.total_seconds()) == 0 and moment.microsecond == 0


def prepare_bars(bars: Sequence[PriceBar], timeframe: timedelta, now: datetime, source: str) -> tuple[list[PriceBar], DataCheck]:
    """Only bars fully closed by `now`: a bar labelled t (its open) is final
    at t + timeframe. Bars stamped in the future are counted and dropped, so
    is a bar still forming, and so is any bar off the timeframe's grid (a
    provider's partial "live" row), whose duration is unknown."""
    aligned = [bar for bar in bars if is_aligned(bar.timestamp, timeframe)]
    present = [bar for bar in aligned if bar.timestamp <= now]
    closed = closed_bars(present, timeframe, now)
    return closed, DataCheck(
        source=source,
        fetched_at=now,
        bars_received=len(bars),
        future_bars_dropped=len(aligned) - len(present),
        forming_bars_excluded=len(present) - len(closed),
        closed_bars=len(closed),
        first_bar_open=closed[0].timestamp if closed else None,
        last_closed_bar_close=closed[-1].timestamp + timeframe if closed else None,
        irregular_bars_dropped=len(bars) - len(aligned),
    )


# --- money in account currency ---


@dataclass(frozen=True)
class AccountMoney:
    """A plan's money in account currency (USD)."""

    risk_amount: Decimal
    reward_amount: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    commission: Decimal
    margin_required: Decimal
    notional: Decimal
    conversion: Conversion
    conversion_note: str

    @property
    def total_costs(self) -> Decimal:
        return self.spread_cost + self.slippage_cost + self.commission

    @property
    def net_reward_risk(self) -> Decimal:
        return (self.reward_amount / self.risk_amount).quantize(_CENT, rounding=ROUND_HALF_EVEN)


def plan_in_account_currency(plan: TradePlan, conversion: Conversion) -> AccountMoney:
    if conversion is Conversion.DIRECT:
        return AccountMoney(
            plan.risk_amount, plan.reward_amount, plan.spread_cost, plan.slippage_cost, plan.commission,
            plan.margin_required, plan.notional, conversion, "importes ya en USD (la divisa de cotización es USD)",
        )
    loss_rate = min(plan.entry_price, plan.stop_loss)
    gain_rate = max(plan.entry_price, plan.take_profit)
    cost_rate = min(plan.entry_price, plan.stop_loss)

    def usd(amount: Decimal, rate: Decimal) -> Decimal:
        return (amount / rate).quantize(_CENT, rounding=ROUND_HALF_EVEN)

    return AccountMoney(
        risk_amount=usd(plan.risk_amount, loss_rate),
        reward_amount=usd(plan.reward_amount, gain_rate),
        spread_cost=usd(plan.spread_cost, cost_rate),
        slippage_cost=usd(plan.slippage_cost, cost_rate),
        commission=usd(plan.commission, cost_rate),
        margin_required=usd(plan.margin_required, plan.entry_price),
        notional=usd(plan.notional, plan.entry_price),
        conversion=conversion,
        conversion_note=(
            "importes convertidos de la divisa de cotización a USD al precio del par: la pérdida al stop al menor de "
            "entrada y stop, la ganancia al objetivo al mayor de entrada y objetivo (conversión conservadora)"
        ),
    )


# --- per-instrument assessment ---


class Category(str, Enum):
    DATA_ERROR = "ERROR_DE_DATOS"
    NOT_ELIGIBLE = "INSTRUMENTO_NO_ELEGIBLE"
    NO_SIGNAL = "SIN_SEÑAL"
    SIGNAL_REJECTED = "SEÑAL_DESCARTADA"  # technical signal that failed a control
    EXPLORATORY = "CANDIDATA_EXPLORATORIA"  # passes the controls, no validated evidence
    VALIDATED = "OPORTUNIDAD_VALIDADA"  # passes the controls and has validated evidence


@dataclass(frozen=True)
class ControlCheck:
    name: str
    passed: bool
    detail: str  # Spanish, with the numbers Python computed


@dataclass(frozen=True)
class ScoreComponent:
    label: str
    points: int
    max_points: int
    detail: str


@dataclass(frozen=True)
class RankScore:
    components: Sequence[ScoreComponent]

    def __post_init__(self) -> None:
        object.__setattr__(self, "components", tuple(self.components))

    @property
    def total(self) -> int:
        return sum(c.points for c in self.components)

    @property
    def maximum(self) -> int:
        return sum(c.max_points for c in self.components)


@dataclass(frozen=True)
class AIReview:
    """The AI committee's review of a candidate. It can only object (remove
    a candidate); it never authorizes or changes anything Python decided."""

    analysis_id: str
    objected: bool
    detail: str


@dataclass(frozen=True)
class InstrumentScan:
    symbol: str
    display_name: str
    category: Category
    technical_direction: TradeDirection  # what the rules pointed to, before controls
    validation: HistoricalValidation
    signal: TradeSignal | None = None
    money: AccountMoney | None = None
    checks: Sequence[ControlCheck] = ()
    notes: Sequence[str] = ()  # Spanish: why selected, discarded, or not eligible
    score: RankScore | None = None
    rank: int | None = None
    data: DataCheck | None = None
    error: str | None = None
    ai_review: AIReview | None = None
    usd_direction: int = 0  # of the candidate position: +1 long USD, -1 short USD

    def __post_init__(self) -> None:
        object.__setattr__(self, "checks", tuple(self.checks))
        object.__setattr__(self, "notes", tuple(self.notes))

    @property
    def is_candidate(self) -> bool:
        return self.category in (Category.EXPLORATORY, Category.VALIDATED)

    @property
    def technical_signal(self) -> bool:
        return self.technical_direction is not TradeDirection.NO_TRADE


def technical_direction(signal: TradeSignal) -> TradeDirection:
    """The direction the rules (trend + momentum + volatility) point to,
    even when the strategy's own cost or sizing gate then said NO_TRADE."""
    if signal.direction is not TradeDirection.NO_TRADE:
        return signal.direction
    reasons = set(signal.reasons)
    if SignalReason.SETUP_LONG in reasons:
        return TradeDirection.LONG
    if SignalReason.SETUP_SHORT in reasons:
        return TradeDirection.SHORT
    if SignalReason.MOMENTUM_CONFIRMS in reasons and SignalReason.VOLATILITY_OK in reasons:
        if SignalReason.TREND_UP in reasons:
            return TradeDirection.LONG
        if SignalReason.TREND_DOWN in reasons:
            return TradeDirection.SHORT
    return TradeDirection.NO_TRADE


def assess_instrument(
    profile: InstrumentProfile,
    closed: Sequence[PriceBar],
    data: DataCheck,
    now: datetime,
    *,
    strategy: ScanStrategy,
    limits: RiskLimits,
    account: AccountState,
    validation: HistoricalValidation,
    signal_id: str,
) -> InstrumentScan:
    """Strategy, conversion and the per-trade controls for one instrument.
    Daily and aggregate limits depend on the other candidates and are
    applied by rank_and_allocate."""
    base = dict(symbol=profile.symbol, display_name=profile.spec.display_name, validation=validation, data=data)
    unsupported = strategy.unsupported_reason(profile)
    conversion = conversion_for(profile)
    if unsupported is not None or conversion is None:
        return InstrumentScan(
            category=Category.NOT_ELIGIBLE, technical_direction=TradeDirection.NO_TRADE,
            notes=(f"no elegible: {unsupported or 'conversión a USD no soportada'}",), **base,  # type: ignore[arg-type]
        )

    signal = _evaluate(strategy, profile, closed, now, signal_id, limits, account.equity, conversion)
    direction = technical_direction(signal)
    if direction is TradeDirection.NO_TRADE:
        return InstrumentScan(category=Category.NO_SIGNAL, technical_direction=direction, signal=signal, **base)  # type: ignore[arg-type]
    if signal.plan is None:
        return InstrumentScan(
            category=Category.SIGNAL_REJECTED, technical_direction=direction, signal=signal,
            notes=("la estrategia descartó la señal por costos o tamaño (ver motivos)",), **base,  # type: ignore[arg-type]
        )

    money = plan_in_account_currency(signal.plan, conversion)
    per_trade_limit = (limits.risk_per_trade * account.equity).quantize(_CENT)
    checks = [
        ControlCheck(
            "riesgo por operación",
            money.risk_amount <= per_trade_limit + _TOLERANCE,
            f"riesgo hasta el stop {fmt(money.risk_amount)} USD frente a un máximo de {fmt(per_trade_limit)} USD "
            f"({fmt(limits.risk_per_trade * 100)} % del equity)",
        ),
        ControlCheck(
            "relación riesgo/beneficio en USD",
            money.net_reward_risk >= strategy.min_reward_risk,
            f"{fmt(money.net_reward_risk)} tras costos y conversión (mínimo {fmt(strategy.min_reward_risk)})",
        ),
        ControlCheck(
            "posición abierta en el mismo instrumento",
            not any(p.symbol == profile.symbol for p in account.open_positions),
            "no se suma una segunda posición en un instrumento que ya tiene una abierta",
        ),
    ]
    failed = [c for c in checks if not c.passed]
    if failed:
        return InstrumentScan(
            category=Category.SIGNAL_REJECTED, technical_direction=direction, signal=signal, money=money, checks=checks,
            notes=tuple(f"descartada: {c.name} - {c.detail}" for c in failed), **base,  # type: ignore[arg-type]
        )
    category = Category.VALIDATED if validation.validated else Category.EXPLORATORY
    score = score_candidate(profile, signal, money, now, account, validation, calibrated=profile.symbol in strategy.calibrated_symbols)
    return InstrumentScan(
        category=category, technical_direction=direction, signal=signal, money=money, checks=checks, score=score,
        usd_direction=usd_direction(profile, direction), **base,  # type: ignore[arg-type]
    )


def _evaluate(
    strategy: ScanStrategy, profile: InstrumentProfile, closed: Sequence[PriceBar], now: datetime, signal_id: str,
    limits: RiskLimits, equity: Decimal, conversion: Conversion,
) -> TradeSignal:
    if conversion is Conversion.DIRECT:
        return strategy.evaluate(closed, now, signal_id=signal_id, profile=profile, account=limits.account_in(equity))
    # INVERSE: run on the equity in quote currency at the pair's price, then
    # again at the conservative rate the loss will be converted at, so the
    # converted risk stays within the limit. Levels do not depend on capital.
    first = strategy.evaluate(closed, now, signal_id=signal_id, profile=profile, account=limits.account_in(equity * closed[-1].close) if closed else limits.account_in(equity))
    if first.plan is None:
        return first
    rate = min(first.plan.entry_price, first.plan.stop_loss)
    return strategy.evaluate(closed, now, signal_id=signal_id, profile=profile, account=limits.account_in(equity * rate))


# --- ranking ---


def score_candidate(
    profile: InstrumentProfile, signal: TradeSignal, money: AccountMoney, now: datetime, account: AccountState, validation: HistoricalValidation,
    calibrated: bool | None = None,
) -> RankScore:
    plan, reading = signal.plan, signal.reading
    assert plan is not None and signal.data_as_of is not None
    age_minutes = int((now - signal.data_as_of).total_seconds() // 60)
    freshness = 2 if age_minutes <= 60 else 1 if age_minutes <= 120 else 0

    if (profile.calibrated if calibrated is None else calibrated):
        fit, fit_detail = 2, "la estrategia se diseñó y se probó en este par, con resultados negativos"
    elif conversion_for(profile) is Conversion.DIRECT:
        fit, fit_detail = 1, "mismas reglas de pips y P&L en USD, pero la estrategia no se probó en este par"
    else:
        fit, fit_detail = 0, "no se probó en este par y su P&L requiere conversión de divisa"

    cost_share = money.total_costs / money.risk_amount
    costs = 2 if cost_share < Decimal("0.10") else 1 if cost_share < Decimal("0.20") else 0

    room_points, room_detail = 0, "sin datos de rango reciente"
    if reading.atr and reading.support is not None and reading.resistance is not None:
        room = (reading.resistance - plan.entry_price) if signal.direction is TradeDirection.LONG else (plan.entry_price - reading.support)
        room_atr = (room / reading.atr).quantize(Decimal("0.1"))
        room_points = 2 if room_atr >= 2 else 1 if room_atr >= 1 else 0
        extreme = "máximo" if signal.direction is TradeDirection.LONG else "mínimo"
        room_detail = f"{fmt(room_atr, 1)} ATR de espacio hasta el {extreme} de las últimas {signal.parameters.range_lookback} velas"

    reward_risk = 1 if money.net_reward_risk >= 2 else 0
    margin_share = (account.used_margin + money.margin_required) / account.equity
    margin = 1 if margin_share <= Decimal("0.20") else 0
    evidence = 3 if validation.validated else 0

    return RankScore(
        (
            ScoreComponent("frescura de datos", freshness, 2, f"última vela cerrada hace {age_minutes} min"),
            ScoreComponent("compatibilidad estrategia-instrumento", fit, 2, fit_detail),
            ScoreComponent("costos frente al riesgo", costs, 2, f"los costos son el {fmt(cost_share * 100, 1)} % del riesgo"),
            ScoreComponent("condición de entrada", room_points, 2, room_detail),
            ScoreComponent("riesgo/beneficio tras costos", reward_risk, 1, f"{fmt(money.net_reward_risk)}; 1 punto desde 2,00"),
            ScoreComponent("uso de margen de la cuenta", margin, 1, f"{fmt(margin_share * 100, 1)} % del equity con esta posición; 1 punto hasta 20 %"),
            ScoreComponent("evidencia histórica", evidence, 3, "VALIDADA" if validation.validated else "NO VALIDADA"),
        )
    )


def rank_and_allocate(scans: Sequence[InstrumentScan], limits: RiskLimits, account: AccountState) -> list[InstrumentScan]:
    """Order candidates (validated first, then by score, then by symbol -
    fully deterministic), then admit them one by one only while the daily
    loss budget and the margin still allow it. At most max_visible get a
    rank; any further candidate stays listed, unranked."""
    budget = daily_loss_budget(limits, account)
    max_margin = (Decimal("1") - limits.margin_reserve_fraction) * account.equity
    candidates = sorted(
        (s for s in scans if s.is_candidate),
        key=lambda s: (not s.validation.validated, -(s.score.total if s.score else 0), s.symbol),
    )
    committed_risk, committed_margin = account.open_risk, account.used_margin
    usd_cap = limits.max_usd_direction_risk * account.equity
    # An open position whose USD side is unknown (0) counts against both sides.
    usd_risk = {
        side: sum((p.risk_amount for p in account.open_positions if p.usd_direction in (side, 0)), Decimal("0")) for side in (1, -1)
    }
    decided: dict[str, InstrumentScan] = {}
    rank = 0
    for scan in candidates:
        assert scan.money is not None
        if budget <= 0:
            decided[scan.symbol] = _reject(scan, "límite de pérdida diaria", f"ya se alcanzó: quedan {fmt(max(budget, Decimal('0')))} USD de pérdida permitida hoy")
            continue
        if committed_risk + scan.money.risk_amount > budget:
            decided[scan.symbol] = _reject(
                scan, "riesgo agregado",
                f"riesgo abierto y aceptado {fmt(committed_risk)} USD + {fmt(scan.money.risk_amount)} USD superaría lo que queda "
                f"de la pérdida diaria permitida ({fmt(budget)} USD)",
            )
            continue
        if committed_margin + scan.money.margin_required > max_margin:
            decided[scan.symbol] = _reject(
                scan, "margen agregado",
                f"margen usado {fmt(committed_margin)} USD + {fmt(scan.money.margin_required)} USD superaría {fmt(max_margin)} USD",
            )
            continue
        side = scan.usd_direction
        if side and usd_risk[side] + scan.money.risk_amount > usd_cap:
            decided[scan.symbol] = _reject(
                scan, "exposición compartida al USD",
                f"las posiciones {'compradas' if side > 0 else 'vendidas'} en USD sumarían {fmt(usd_risk[side] + scan.money.risk_amount)} USD "
                f"de riesgo, por encima de {fmt(usd_cap)} USD ({fmt(limits.max_usd_direction_risk * 100)} % del equity)",
            )
            continue
        committed_risk += scan.money.risk_amount
        committed_margin += scan.money.margin_required
        if side:
            usd_risk[side] += scan.money.risk_amount
        if rank < limits.max_visible:
            rank += 1
            decided[scan.symbol] = replace(scan, rank=rank)
        else:
            decided[scan.symbol] = replace(scan, notes=(*scan.notes, f"supera los controles, pero solo se muestran {limits.max_visible} candidatas"))
    return [decided.get(s.symbol, s) for s in scans]


def _reject(scan: InstrumentScan, name: str, detail: str) -> InstrumentScan:
    return replace(
        scan,
        category=Category.SIGNAL_REJECTED,
        checks=(*scan.checks, ControlCheck(name, False, detail)),
        notes=(*scan.notes, f"descartada: {name} - {detail}"),
        rank=None,
    )


def apply_ai_objection(scan: InstrumentScan, review: AIReview) -> InstrumentScan:
    """Record the AI review; an objection discards the candidate. A lack of
    objection changes nothing - the AI never upgrades a category."""
    if not review.objected:
        return replace(scan, ai_review=review)
    return replace(
        scan,
        ai_review=review,
        category=Category.SIGNAL_REJECTED,
        rank=None,
        notes=(*scan.notes, f"descartada por objeción del comité de IA: {review.detail}"),
    )


# --- later evaluation (never counted as real P&L) ---


def hypothetical_outcome(scan: InstrumentScan, bars: Sequence[PriceBar], evaluated_at: datetime) -> TradeOutcome | None:
    """What would have happened to a candidate, using only bars after its
    signal and closed by `evaluated_at` (simulation.resolve_trade). The
    result is hypothetical - the candidate was never a simulated trade - and
    its P&L is in the instrument's quote currency."""
    if scan.signal is None or scan.signal.plan is None:
        return None
    return resolve_trade(scan.signal, bars, evaluated_at)


@dataclass(frozen=True)
class ScanSettings:
    """Limits on one scan's size and duration."""

    max_instruments: int = 5
    time_budget: timedelta = timedelta(seconds=60)
    max_bars_per_instrument: int = 2000

    def __post_init__(self) -> None:
        if not (1 <= self.max_instruments <= 10):
            raise ValueError("max_instruments must be between 1 and 10")
        if self.time_budget <= timedelta(0):
            raise ValueError("time_budget must be positive")


@dataclass(frozen=True)
class AIReviewSettings:
    """AI review is OFF by default: a market scan never triggers a paid call
    unless asked for, and then only for ranked candidates, at most
    `max_reviews` per scan."""

    max_reviews: int = 0

    def __post_init__(self) -> None:
        if not (0 <= self.max_reviews <= 3):
            raise ValueError("max_reviews must be between 0 and 3")



# --- one scan ---


@dataclass(frozen=True)
class OpportunityScan:
    """One complete scan, as recorded in the audit ledger."""

    scan_id: str
    started_at: datetime  # the analysis moment: no bar after it is used
    finished_at: datetime
    scanner_version: str
    strategy_name: str
    strategy_version: str
    timeframe: timedelta
    data_source: str
    universe: Sequence[str]
    limits: RiskLimits
    account: AccountState
    instruments: Sequence[InstrumentScan]
    errors: Sequence[str] = ()  # partial failures; the rest of the scan went on
    ai_review_enabled: bool = False
    ai_reviews_used: int = 0
    simulated_trade_opened: bool = False  # V1 never opens one

    def __post_init__(self) -> None:
        object.__setattr__(self, "universe", tuple(self.universe))
        object.__setattr__(self, "instruments", tuple(self.instruments))
        object.__setattr__(self, "errors", tuple(self.errors))
        if self.simulated_trade_opened:
            raise ValueError("Opportunity Scanner V1 never opens a simulated trade")

    @property
    def ranked(self) -> list[InstrumentScan]:
        return sorted((s for s in self.instruments if s.rank is not None), key=lambda s: s.rank or 0)

    @property
    def technical_signals(self) -> int:
        return sum(1 for s in self.instruments if s.technical_signal)

    @property
    def candidates(self) -> list[InstrumentScan]:
        return [s for s in self.instruments if s.is_candidate]

    @property
    def discarded(self) -> list[InstrumentScan]:
        return [s for s in self.instruments if s.technical_signal and not s.is_candidate]
