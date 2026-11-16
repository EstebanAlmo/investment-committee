"""Historical Replay: what following the Opportunity Scanner would have done.

A chronological, deterministic re-run of the scanner over past 1-hour bars,
for three selection policies, each with its own fresh hypothetical account:
  A - every candidate that passes the operational controls;
  B - only the best-ranked candidate at each decision;
  C - only candidates whose strategy/instrument evidence was VALIDATED by a
      record dated BEFORE the decision, and with no blocking objection
      recorded at that time. With no such evidence, C does not trade.
This is a RETROSPECTIVE BACKTEST on data that already exists - not
out-of-sample validation, and not prospective tracking. Comparing the
policies afterwards proves nothing about which one "works".

Timeline at each hourly decision moment t (UTC, on the bar grid):
  1. bars that closed at t (labelled t - 1h: Yahoo labels by OPEN time)
     update open positions: stop / target (both in one bar -> stop,
     conservative), then the session flat time;
  2. equity is marked to market at those closes (exit side, in USD);
  3. if the session allows a new entry at t, the scanner sees ONLY bars
     closed by t (prepare_bars) and the account as it is at t; the policy
     picks candidates; each one enters at the OPEN of the bar starting at t
     (the first executable price after the decision), with spread and
     slippage, stop and target re-derived from that fill. No bar at t means
     no entry - a price from before the decision is never used.
Execution reuses simulation/execution.py unchanged (the backtester's
rules). Day-trading rules come from ForexSessionRules: entries only before
15:00 New York, everything flat by 16:00 New York (daylight saving
applied). P&L in JPY is converted to USD at the exit price (USD/JPY is
itself the rate). Costs (spread, slippage) are inside the fill prices and
are reported separately for transparency. Liquidity is not assumed
perfect: every fill pays the assumed spread plus slippage, stops fill with
slippage or at a worse gap open, targets never fill better than the target.

Never used: DeepSeek or any AI decision, unless a contemporaneous,
verifiable record is supplied as a ContemporaneousObjection (policy C).
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal
from enum import Enum
from typing import Mapping, Sequence

from investment_committee.domain import PriceBar

from .execution import (
    ExitEvent,
    Position,
    check_exit,
    close_at_bar_close,
    close_at_bar_open,
    entry_fill,
    gross_pnl,
    half_spread,
    levels_for,
    quantize_price,
    slippage,
)
from .models import TradeDirection, TradeStatus
from .opportunities import (
    NOT_VALIDATED,
    AccountState,
    Category,
    Conversion,
    HistoricalValidation,
    InstrumentProfile,
    InstrumentScan,
    OpenPosition,
    RiskLimits,
    ScanStrategy,
    TrendMomentumVolatilityStrategy,
    assess_instrument,
    conversion_for,
    daily_loss_budget,
    prepare_bars,
    rank_and_allocate,
    usd_direction,
)
from .sessions import ForexSessionRules, is_week_open, session_day

REPLAY_VERSION = "historical-replay-1.0.0"
REPLAY_KIND = "BACKTEST_RETROSPECTIVO"
_CENT = Decimal("0.01")
_LOOKBACK = 200  # bars handed to the scanner at each decision (the strategy needs 61)


class Policy(str, Enum):
    ALL_ELIGIBLE = "A"
    BEST_RANKED = "B"
    VALIDATED_ONLY = "C"


POLICY_DESCRIPTIONS = {
    Policy.ALL_ELIGIBLE: "todas las candidatas que superan los controles operativos",
    Policy.BEST_RANKED: "solo la mejor candidata del ranking en cada decisión",
    Policy.VALIDATED_ONLY: "solo candidatas con evidencia histórica favorable anterior a la decisión y sin objeciones",
}


class ExitReason(str, Enum):
    STOP = "STOP"
    TARGET = "OBJETIVO"
    SESSION_CLOSE = "CIERRE_DE_SESIÓN"
    END_OF_DATA = "FIN_DE_DATOS"


@dataclass(frozen=True)
class DatedValidation:
    """Evidence about a strategy on an instrument, usable only from
    `available_from` on (when the record was actually produced)."""

    symbol: str
    strategy_name: str
    strategy_version: str
    available_from: datetime
    validation: HistoricalValidation


@dataclass(frozen=True)
class ContemporaneousObjection:
    """A blocking objection recorded AT `recorded_at` (e.g. an AI committee
    ledger entry made then). It blocks policy C for `window` afterwards."""

    symbol: str
    recorded_at: datetime
    detail: str


@dataclass(frozen=True)
class ReplayConfig:
    start: datetime  # first decision moment (inclusive)
    end: datetime  # no decision at or after this moment; open positions close by then
    universe: Sequence[InstrumentProfile]
    limits: RiskLimits = field(default_factory=RiskLimits)
    sessions: ForexSessionRules = field(default_factory=ForexSessionRules)
    policies: Sequence[Policy] = (Policy.ALL_ELIGIBLE, Policy.BEST_RANKED, Policy.VALIDATED_ONLY)
    validations: Sequence[DatedValidation] = ()
    objections: Sequence[ContemporaneousObjection] = ()
    objection_window: timedelta = timedelta(hours=1)

    def __post_init__(self) -> None:
        for name in ("start", "end"):
            moment = getattr(self, name)
            if moment.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if not self.universe:
            raise ValueError("the universe must not be empty")
        object.__setattr__(self, "universe", tuple(self.universe))
        object.__setattr__(self, "policies", tuple(self.policies))
        object.__setattr__(self, "validations", tuple(self.validations))
        object.__setattr__(self, "objections", tuple(self.objections))


@dataclass(frozen=True)
class ReplayTrade:
    policy: Policy
    symbol: str
    direction: TradeDirection
    signal_id: str
    decided_at: datetime
    entry_time: datetime
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    units: int
    risk_usd: Decimal  # at the fill, to the stop, conservative conversion
    margin_usd: Decimal
    exit_time: datetime
    exit_price: Decimal
    exit_reason: ExitReason
    same_bar_conflict: bool
    pnl_quote: Decimal  # in the instrument's quote currency
    net_pnl_usd: Decimal
    costs_usd: Decimal  # spread + slippage, already inside net_pnl_usd
    rank: int | None
    score: int | None
    strategy: str


@dataclass(frozen=True)
class ReplayRejection:
    decided_at: datetime
    symbol: str
    reason: str
    detail: str


@dataclass(frozen=True)
class EquityPoint:
    at: datetime
    balance: Decimal
    equity: Decimal
    open_positions: int
    open_risk: Decimal
    notional: Decimal


@dataclass(frozen=True)
class ReplayMetrics:
    trades: int
    wins: int  # net > 0
    losses: int  # net < 0; trades - wins - losses closed at exactly 0
    net_pnl: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    expectancy: Decimal | None  # net USD per trade over THIS period only - not a validated expectancy
    profit_factor: Decimal | None
    total_costs: Decimal
    same_bar_conflicts: int
    exits: Mapping[str, int]


@dataclass(frozen=True)
class PolicyResult:
    policy: Policy
    initial_capital: Decimal
    final_balance: Decimal
    net_return: Decimal  # fraction of initial capital
    metrics: ReplayMetrics
    max_drawdown: Decimal  # USD, <= 0
    max_drawdown_fraction: Decimal  # of peak equity, <= 0
    max_open_positions: int
    max_open_risk: Decimal
    max_notional: Decimal
    trades: Sequence[ReplayTrade]
    rejections: Sequence[ReplayRejection]
    equity_curve: Sequence[EquityPoint]
    daily: Mapping[date, Decimal]  # session day -> realized net P&L
    by_instrument: Mapping[str, ReplayMetrics]
    by_strategy: Mapping[str, ReplayMetrics]

    @property
    def rejection_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for rejection in self.rejections:
            counts[rejection.reason] = counts.get(rejection.reason, 0) + 1
        return counts


@dataclass(frozen=True)
class DataCoverage:
    symbol: str
    bars_in_period: int
    first_bar: datetime | None
    last_bar: datetime | None
    open_hours_without_bar: int


@dataclass(frozen=True)
class ReplayReport:
    kind: str
    version: str
    config: ReplayConfig
    strategy_name: str
    strategy_version: str
    decision_moments: int
    entry_decision_moments: int
    coverage: Sequence[DataCoverage]
    results: Sequence[PolicyResult]
    ai_decisions_used: int  # contemporaneous objection records actually applied
    generated_at: datetime

    @property
    def no_trade_baseline(self) -> Decimal:
        """Not trading keeps the capital unchanged."""
        return self.config.limits.capital


# --- engine ---


@dataclass
class _Open:
    profile: InstrumentProfile
    position: Position
    conversion: Conversion
    decided_at: datetime
    signal_id: str
    risk_usd: Decimal
    margin_usd: Decimal
    usd_side: int
    flat_at: datetime
    rank: int | None
    score: int | None
    last_mid: Decimal
    must_flatten: bool = False


class _Series:
    """One instrument's bars on the 1-hour grid, indexed by open time."""

    def __init__(self, bars: Sequence[PriceBar], timeframe: timedelta) -> None:
        aligned = sorted({b.timestamp: b for b in bars if int(b.timestamp.timestamp()) % int(timeframe.total_seconds()) == 0}.values(), key=lambda b: b.timestamp)
        self.bars = aligned
        self.times = [b.timestamp for b in aligned]
        self.by_time = {b.timestamp: b for b in aligned}

    def up_to_decision(self, t: datetime, timeframe: timedelta) -> list[PriceBar]:
        """Bars closed by t, plus the bar opening at t (prepare_bars then
        excludes it as still forming - kept so the count is auditable)."""
        index = bisect_right(self.times, t)
        return self.bars[max(0, index - _LOOKBACK - 1):index]


def run_replay(
    bars_by_symbol: Mapping[str, Sequence[PriceBar]],
    config: ReplayConfig,
    strategy: ScanStrategy | None = None,
    generated_at: datetime | None = None,
) -> ReplayReport:
    strategy = strategy or TrendMomentumVolatilityStrategy()
    tf = strategy.timeframe
    series = {p.symbol: _Series(bars_by_symbol.get(p.symbol, ()), tf) for p in config.universe}
    moments = _grid(config.start, config.end, tf)
    results = [_run_policy(policy, series, moments, config, strategy) for policy in config.policies]
    applied = sum(1 for r in results for x in r.rejections if x.reason == "objeción contemporánea")
    return ReplayReport(
        kind=REPLAY_KIND,
        version=REPLAY_VERSION,
        config=config,
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        decision_moments=len(moments),
        entry_decision_moments=sum(1 for t in moments if _may_decide(t, config.sessions, config)),
        coverage=[_coverage(p.symbol, series[p.symbol], config, tf) for p in config.universe],
        results=results,
        ai_decisions_used=applied,
        generated_at=generated_at or datetime.now(timezone.utc),
    )


def _grid(start: datetime, end: datetime, tf: timedelta) -> list[datetime]:
    step = int(tf.total_seconds())
    first = int(start.timestamp())
    first += (-first) % step
    return [datetime.fromtimestamp(s, tz=timezone.utc) for s in range(first, int(end.timestamp()), step)]


def _run_policy(policy: Policy, series: Mapping[str, _Series], moments: Sequence[datetime], config: ReplayConfig, strategy: ScanStrategy) -> PolicyResult:
    limits, rules, tf = config.limits, config.sessions, strategy.timeframe
    balance = limits.capital
    open_: list[_Open] = []
    trades: list[ReplayTrade] = []
    rejections: list[ReplayRejection] = []
    curve: list[EquityPoint] = []
    daily: dict[date, Decimal] = {}
    current_day: date | None = None
    day_start_equity = balance

    def close(item: _Open, event: ExitEvent, reason: ExitReason) -> None:
        nonlocal balance
        # Fills are prices on the instrument's tick, like entries (entry_fill):
        # the P&L is computed from the same rounded price the trade reports.
        fill = quantize_price(event.price, item.profile.spec)
        pnl_quote = (gross_pnl(item.position, fill) - item.position.commission).quantize(_CENT, rounding=ROUND_HALF_EVEN)
        rate = Decimal("1") if item.conversion is Conversion.DIRECT else fill
        net_usd = (pnl_quote / rate).quantize(_CENT, rounding=ROUND_HALF_EVEN) + 0  # + 0 drops a "-0.00"
        pnl_quote = pnl_quote + 0
        costs = item.profile.costs
        spec = item.profile.spec
        per_unit = 2 * half_spread(costs, spec) + slippage(costs, spec) * (1 if reason is ExitReason.TARGET else 2)
        costs_usd = (Decimal(item.position.units) * per_unit / rate).quantize(_CENT, rounding=ROUND_HALF_EVEN)
        balance += net_usd
        utc = timezone.utc
        day = session_day(event.time - timedelta(microseconds=1))
        daily[day] = daily.get(day, Decimal("0")) + net_usd
        trades.append(
            ReplayTrade(
                policy=policy, symbol=item.profile.symbol, direction=item.position.direction, signal_id=item.signal_id,
                decided_at=item.decided_at, entry_time=item.position.entry_time.astimezone(utc), entry_price=item.position.entry_price,
                stop_loss=item.position.stop_loss, take_profit=item.position.take_profit, units=item.position.units,
                risk_usd=item.risk_usd, margin_usd=item.margin_usd, exit_time=event.time.astimezone(utc), exit_price=fill,
                exit_reason=reason, same_bar_conflict=event.same_bar_conflict, pnl_quote=pnl_quote, net_pnl_usd=net_usd,
                costs_usd=costs_usd, rank=item.rank, score=item.score, strategy=f"{strategy.name} v{strategy.version}",
            )
        )
        open_.remove(item)

    for t in moments:
        # 1. the bar that closed at t updates each open position
        for item in list(open_):
            bar = series[item.profile.symbol].by_time.get(t - tf)
            if not item.must_flatten and bar is not None and bar.timestamp >= item.position.entry_time:
                item.last_mid = bar.close
                event = check_exit(item.position, bar, tf, item.profile.costs, item.profile.spec)
                if event is not None:
                    close(item, event, ExitReason.STOP if event.status is TradeStatus.STOP_LOSS else ExitReason.TARGET)
                elif t >= item.flat_at:
                    close(item, close_at_bar_close(item.position, bar, tf, item.profile.costs, item.profile.spec, TradeStatus.EXPIRED), ExitReason.SESSION_CLOSE)
            elif t >= item.flat_at:
                item.must_flatten = True  # flat time reached without a bar: exit at the next price

        # 1b. the OPEN of the bar starting at t is the price at this instant -
        # the same price new entries fill at - so exits it causes come first:
        # a gap through the stop, or a pending session close. Only the open is
        # read; the bar's high, low and close are not known yet.
        for item in list(open_):
            now_bar = series[item.profile.symbol].by_time.get(t)
            if now_bar is None:
                continue
            if item.must_flatten:
                close(item, close_at_bar_open(item.position, now_bar, item.profile.costs, item.profile.spec, TradeStatus.EXPIRED), ExitReason.SESSION_CLOSE)
                continue
            gap = _gap_exit(item.position, now_bar, item.profile)
            if gap is not None:
                item.last_mid = now_bar.open
                close(item, gap, ExitReason.STOP)

        # 2. mark to market
        equity = balance + sum((_unrealized_usd(i) for i in open_), Decimal("0"))
        curve.append(
            EquityPoint(
                t, balance, equity.quantize(_CENT), len(open_), sum((i.risk_usd for i in open_), Decimal("0")),
                sum((Decimal(i.position.units) * (Decimal("1") if i.conversion is Conversion.INVERSE else i.position.entry_price) for i in open_), Decimal("0")).quantize(_CENT),
            )
        )
        day = session_day(t)
        if day != current_day:
            current_day, day_start_equity = day, equity

        # 3. decisions - only if the position could also be closed inside the period
        if not _may_decide(t, rules, config):
            continue
        account = AccountState(
            equity=equity,
            day_start_equity=day_start_equity,
            realized_pnl_today=daily.get(day, Decimal("0")),
            open_positions=[OpenPosition(i.profile.symbol, i.risk_usd, i.margin_usd, "replay", i.usd_side) for i in open_],
        )
        scans = []
        for profile in config.universe:
            closed, data = prepare_bars(series[profile.symbol].up_to_decision(t, tf), tf, t, "replay")
            validation = _validation_at(config, strategy, profile.symbol, t)
            scans.append(
                assess_instrument(profile, closed, data, t, strategy=strategy, limits=limits, account=account, validation=validation, signal_id=f"{policy.value}-{t:%Y%m%d%H}-{profile.symbol}")
            )
        ranked = rank_and_allocate(scans, limits, account)
        for scan in ranked:
            if scan.technical_signal and not scan.is_candidate:
                rejections.append(ReplayRejection(t, scan.symbol, _rejection_reason(scan), scan.notes[-1] if scan.notes else ""))
        for scan in _select(policy, ranked, config, t, rejections):
            profile = next(p for p in config.universe if p.symbol == scan.symbol)
            entry_bar = series[profile.symbol].by_time.get(t)
            if entry_bar is None:
                rejections.append(ReplayRejection(t, scan.symbol, "sin precio ejecutable", "no hay vela que abra en el momento de la decisión"))
                continue
            opened = _open(scan, profile, entry_bar, t, rules, limits, account.equity)
            if opened is None:
                rejections.append(ReplayRejection(t, scan.symbol, "tamaño insuficiente al ejecutar", "tras ajustar al riesgo máximo no queda el tamaño mínimo"))
                continue
            # The fill can move the risk slightly: re-check the aggregate limits on actual figures.
            budget = daily_loss_budget(limits, account)
            side_risk = sum((i.risk_usd for i in open_ if i.usd_side in (opened.usd_side, 0)), Decimal("0"))
            if sum((i.risk_usd for i in open_), Decimal("0")) + opened.risk_usd > budget:
                rejections.append(ReplayRejection(t, scan.symbol, "riesgo agregado al ejecutar", "con el precio de ejecución se superaría la pérdida diaria permitida"))
                continue
            if opened.usd_side and side_risk + opened.risk_usd > limits.max_usd_direction_risk * account.equity:
                rejections.append(ReplayRejection(t, scan.symbol, "exposición compartida al USD al ejecutar", "con el precio de ejecución se superaría el límite"))
                continue
            open_.append(opened)

    # end of the period: anything still open closes at its last known close
    end_time = moments[-1] + tf if moments else config.end
    for item in list(open_):
        bars_seen = [b for b in series[item.profile.symbol].bars if item.position.entry_time <= b.timestamp and b.timestamp + tf <= end_time]
        if bars_seen:
            close(item, close_at_bar_close(item.position, bars_seen[-1], tf, item.profile.costs, item.profile.spec, TradeStatus.END_OF_DATA), ExitReason.END_OF_DATA)
        else:
            bar = series[item.profile.symbol].by_time.get(item.position.entry_time)
            assert bar is not None
            close(item, close_at_bar_open(item.position, bar, item.profile.costs, item.profile.spec, TradeStatus.END_OF_DATA), ExitReason.END_OF_DATA)

    drawdown, drawdown_fraction = _drawdown([p.equity for p in curve], limits.capital)
    return PolicyResult(
        policy=policy,
        initial_capital=limits.capital,
        final_balance=balance,
        net_return=((balance - limits.capital) / limits.capital).quantize(Decimal("0.0001")),
        metrics=_metrics(trades),
        max_drawdown=drawdown,
        max_drawdown_fraction=drawdown_fraction,
        max_open_positions=max((p.open_positions for p in curve), default=0),
        max_open_risk=max((p.open_risk for p in curve), default=Decimal("0")),
        max_notional=max((p.notional for p in curve), default=Decimal("0")),
        trades=trades,
        rejections=rejections,
        equity_curve=curve,
        daily=dict(sorted(daily.items())),
        by_instrument={p.symbol: _metrics([x for x in trades if x.symbol == p.symbol]) for p in config.universe},
        by_strategy={f"{strategy.name} v{strategy.version}": _metrics(trades)},
    )


def _may_decide(t: datetime, rules: ForexSessionRules, config: ReplayConfig) -> bool:
    """The session allows an entry at t, and its flat time is inside the period."""
    return rules.may_enter(t) and rules.flat_time(t) <= config.end


def _validation_at(config: ReplayConfig, strategy: ScanStrategy, symbol: str, t: datetime) -> HistoricalValidation:
    """The latest evidence record available at t (never one dated later)."""
    known = [
        v for v in config.validations
        if v.symbol == symbol and v.strategy_name == strategy.name and v.strategy_version == strategy.version and v.available_from <= t
    ]
    return max(known, key=lambda v: v.available_from).validation if known else NOT_VALIDATED


def _select(policy: Policy, ranked: Sequence[InstrumentScan], config: ReplayConfig, t: datetime, rejections: list[ReplayRejection]) -> list[InstrumentScan]:
    candidates = sorted((s for s in ranked if s.rank is not None), key=lambda s: s.rank or 0)
    if policy is Policy.ALL_ELIGIBLE:
        return candidates
    if policy is Policy.BEST_RANKED:
        for other in candidates[1:]:
            rejections.append(ReplayRejection(t, other.symbol, "no es la mejor del ranking", f"puesto {other.rank} (la política B solo toma la primera)"))
        return candidates[:1]
    chosen = []
    for scan in candidates:
        if scan.category is not Category.VALIDATED:
            rejections.append(ReplayRejection(t, scan.symbol, "sin evidencia validada previa", scan.validation.detail))
            continue
        objection = next(
            (o for o in config.objections if o.symbol == scan.symbol and t - config.objection_window < o.recorded_at <= t), None
        )
        if objection is not None:
            rejections.append(ReplayRejection(t, scan.symbol, "objeción contemporánea", objection.detail))
            continue
        chosen.append(scan)
    return chosen




def _open(
    scan: InstrumentScan, profile: InstrumentProfile, entry_bar: PriceBar, t: datetime, rules: ForexSessionRules, limits: RiskLimits, equity: Decimal
) -> _Open | None:
    """Fill at the open of the bar starting at the decision moment. Stop and
    target are re-derived from the fill; if the fill (or the JPY rate) moved
    the risk above the per-trade limit, the size shrinks to the lot step -
    it never exceeds the limit."""
    signal = scan.signal
    assert signal is not None and signal.plan is not None and signal.reading.atr is not None
    conversion = conversion_for(profile)
    assert conversion is not None
    spec, costs = profile.spec, profile.costs
    entry = entry_fill(signal.direction, entry_bar.open, costs, spec)
    stop, target = levels_for(signal.direction, entry, signal.reading.atr, signal.parameters, spec)
    risk_per_unit = abs(entry - stop) + slippage(costs, spec)
    loss_rate = Decimal("1") if conversion is Conversion.DIRECT else min(entry, stop)
    max_risk = limits.risk_per_trade * equity
    units = signal.plan.units
    if Decimal(units) * risk_per_unit / loss_rate > max_risk:
        step = Decimal(limits.lot_step_units)
        units = int((max_risk * loss_rate / risk_per_unit / step).to_integral_value(rounding=ROUND_DOWN) * step)
        if units < limits.min_units:
            return None
    risk_usd = Decimal(units) * risk_per_unit / loss_rate
    margin_quote = Decimal(units) * entry / limits.leverage
    margin_usd = margin_quote if conversion is Conversion.DIRECT else margin_quote / entry
    flat_at = rules.flat_time(t)
    position = Position(
        signal_id=signal.signal_id, direction=signal.direction, entry_time=entry_bar.timestamp, entry_price=entry,
        stop_loss=stop, take_profit=target, units=units, commission=signal.plan.commission, margin=margin_quote, expires_at=flat_at,
    )
    return _Open(
        profile=profile, position=position, conversion=conversion, decided_at=t, signal_id=signal.signal_id,
        risk_usd=risk_usd.quantize(_CENT), margin_usd=margin_usd.quantize(_CENT), usd_side=usd_direction(profile, signal.direction),
        flat_at=flat_at, rank=scan.rank, score=scan.score.total if scan.score else None, last_mid=entry_bar.open,
    )


def _gap_exit(position: Position, bar: PriceBar, profile: InstrumentProfile) -> ExitEvent | None:
    """A stop gapped through at this bar's OPEN, using the open only (an
    open-only bar makes check_exit unable to see the rest of the bar)."""
    open_only = PriceBar(bar.timestamp, bar.open, bar.open, bar.open, bar.open, 0)
    event = check_exit(position, open_only, timedelta(0), profile.costs, profile.spec)
    if event is None or event.status is not TradeStatus.STOP_LOSS or event.time != bar.timestamp:
        return None
    return event


def _unrealized_usd(item: _Open) -> Decimal:
    """Marked at the exit side of the last known close, in USD."""
    costs, spec = item.profile.costs, item.profile.spec
    sign = item.position.sign
    exit_price = item.last_mid - sign * half_spread(costs, spec)
    pnl_quote = gross_pnl(item.position, exit_price)
    return pnl_quote if item.conversion is Conversion.DIRECT else pnl_quote / exit_price


def _rejection_reason(scan: InstrumentScan) -> str:
    failed = [c for c in scan.checks if not c.passed]
    if failed:
        return failed[-1].name
    if scan.ai_review is not None and scan.ai_review.objected:
        return "objeción del comité de IA"
    return "descartada por la estrategia (costos o tamaño)"


def _metrics(trades: Sequence[ReplayTrade]) -> ReplayMetrics:
    wins = [x.net_pnl_usd for x in trades if x.net_pnl_usd > 0]
    losses = [x.net_pnl_usd for x in trades if x.net_pnl_usd < 0]
    net = sum((x.net_pnl_usd for x in trades), Decimal("0"))
    gross_profit, gross_loss = sum(wins, Decimal("0")), sum(losses, Decimal("0"))
    exits: dict[str, int] = {}
    for trade in trades:
        exits[trade.exit_reason.value] = exits.get(trade.exit_reason.value, 0) + 1
    return ReplayMetrics(
        trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        net_pnl=net,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        expectancy=(net / len(trades)).quantize(_CENT) if trades else None,
        profit_factor=(gross_profit / -gross_loss).quantize(_CENT) if gross_loss < 0 else None,
        total_costs=sum((x.costs_usd for x in trades), Decimal("0")),
        same_bar_conflicts=sum(1 for x in trades if x.same_bar_conflict),
        exits=exits,
    )


def _drawdown(equities: Sequence[Decimal], initial: Decimal) -> tuple[Decimal, Decimal]:
    peak, worst, worst_fraction = initial, Decimal("0"), Decimal("0")
    for value in equities:
        peak = max(peak, value)
        worst = min(worst, value - peak)
        worst_fraction = min(worst_fraction, (value - peak) / peak)
    return worst.quantize(_CENT), worst_fraction.quantize(Decimal("0.0001"))


def _coverage(symbol: str, series: _Series, config: ReplayConfig, tf: timedelta) -> DataCoverage:
    inside = [b for b in series.bars if config.start <= b.timestamp < config.end]
    present = {b.timestamp for b in inside}
    missing = sum(1 for t in _grid(config.start, config.end, tf) if is_week_open(t) and is_week_open(t + tf - timedelta(microseconds=1)) and t not in present)
    return DataCoverage(symbol, len(inside), inside[0].timestamp if inside else None, inside[-1].timestamp if inside else None, missing)
