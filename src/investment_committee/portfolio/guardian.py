"""Portfolio Guardian: valuation and alerts for positions held elsewhere.

Monitors positions only: it never sends an order and never closes a
position. Positions are simulated (the trade journal) or registered by
hand. Pure logic - prices arrive as Quote objects from application/.

Valuation (all money in USD):
  - P&L follows the POSITION: LONG gains when the price rises, SHORT gains
    when it falls. "Asset change" (the price move) is reported apart, so a
    falling asset is never shown as a loss of a SHORT;
  - notional = quantity x multiplier x price; margin = notional / leverage;
  - quote currency -> USD with a rate (USD per unit of that currency): 1 for
    USD; for a pair whose base is USD (USD/JPY) its own price gives the rate;
    other currencies need a supplied rate, else the valuation is NOT
    AVAILABLE (never a guessed figure);
  - gold: Yahoo has no XAU/USD spot quote (XAUUSD=X does not exist; checked
    2026-10-13). GC=F is the COMEX future - another instrument, with basis
    to spot - and is used only when explicitly allowed, flagged as a proxy.

Alerts (configurable AlertRules): unrealized loss above a USD or % limit,
price near the stop, a significant asset drop, total exposure above a
multiple of equity, stale market data. Each alert has a state: it fires on
ACTIVATION, repeats at most once per cooldown while still true (REPETIDA),
and is RESUELTA only after the metric recovers beyond a hysteresis band -
so a value oscillating around a threshold does not spam.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from enum import Enum
from typing import Mapping, Sequence

_CENT = Decimal("0.01")


def _fmt(value: Decimal | None, places: int = 2) -> str:
    """es-CO number: thousands with ".", decimals with ","."""
    if value is None:
        return "-"
    return f"{value:,.{places}f}".replace(",", "_").replace(".", ",").replace("_", ".")


class AssetKind(str, Enum):
    FOREX = "FOREX"
    METAL = "METAL"
    EQUITY = "EQUITY"  # stocks and ETFs


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True)
class PortfolioPosition:
    position_id: str
    instrument: str  # canonical: "EURUSD=X", "XAUUSD", "AAPL"
    kind: AssetKind
    side: Side
    quantity: Decimal  # FX: base-currency units; gold: troy ounces; equity: shares
    entry_price: Decimal
    currency: str  # the price's currency (FX: the quote currency)
    opened_at: datetime
    costs_usd: Decimal = Decimal("0")  # paid already (commissions, spread estimate)
    stop: Decimal | None = None
    multiplier: Decimal = Decimal("1")  # e.g. 100 oz per gold contract
    leverage: Decimal = Decimal("1")
    source: str = "MANUAL"  # MANUAL | SIMULADA
    base_currency: str | None = None  # FX only

    def __post_init__(self) -> None:
        if self.quantity <= 0 or self.entry_price <= 0 or self.multiplier <= 0 or self.leverage < 1:
            raise ValueError("quantity, entry price and multiplier must be positive, leverage at least 1")
        if self.opened_at.tzinfo is None:
            raise ValueError("opened_at must be timezone-aware")
        if self.kind is AssetKind.FOREX and not self.base_currency:
            raise ValueError("a Forex position needs its base currency")

    @property
    def sign(self) -> Decimal:
        return Decimal("1") if self.side is Side.LONG else Decimal("-1")


def forex_position(position_id: str, pair: str, side: Side, quantity: Decimal, entry_price: Decimal, opened_at: datetime, **kwargs) -> PortfolioPosition:  # type: ignore[no-untyped-def]
    """"EUR/USD" (or "EURUSD") -> a Forex position quoted in USD."""
    letters = pair.replace("/", "").replace("=X", "").upper()
    if len(letters) != 6 or not letters.isalpha():
        raise ValueError(f"not a currency pair: {pair}")
    return PortfolioPosition(position_id, f"{letters}=X", AssetKind.FOREX, side, quantity, entry_price, letters[3:], opened_at, base_currency=letters[:3], **kwargs)


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: Decimal
    as_of: datetime  # close time of the bar the price comes from
    source: str
    proxy_note: str | None = None  # set when the price is a stand-in (e.g. gold futures for spot)


@dataclass(frozen=True)
class Valuation:
    position: PortfolioPosition
    available: bool
    reason: str = ""  # why not available
    price: Decimal | None = None
    price_as_of: datetime | None = None
    market_value_usd: Decimal | None = None  # notional at the current price, always positive
    pnl_usd: Decimal | None = None  # unrealized, net of costs, signed by the POSITION
    pnl_pct: Decimal | None = None  # of the entry notional
    asset_change_pct: Decimal | None = None  # the price move, whatever the side
    exposure_usd: Decimal | None = None
    margin_usd: Decimal | None = None
    stop_distance_pct: Decimal | None = None  # how far the price is from the stop, as a fraction of price
    stale: bool = False
    proxy_note: str | None = None


def usd_rates(quotes: Mapping[str, Quote]) -> dict[str, Decimal]:
    """USD per unit of each currency, from Forex quotes that involve USD."""
    rates = {"USD": Decimal("1")}
    for symbol, quote in quotes.items():
        letters = symbol.replace("=X", "")
        if len(letters) != 6:
            continue
        base, counter = letters[:3], letters[3:]
        if base == "USD" and counter != "USD":
            rates.setdefault(counter, Decimal("1") / quote.price)
        elif counter == "USD" and base != "USD":
            rates.setdefault(base, quote.price)
    return rates


def value_position(position: PortfolioPosition, quote: Quote | None, rates: Mapping[str, Decimal], now: datetime, max_age: timedelta) -> Valuation:
    if quote is None:
        return Valuation(position, False, "no hay precio de mercado para este instrumento")
    rate = rates.get(position.currency)
    if rate is None:
        return Valuation(position, False, f"falta el tipo de cambio {position.currency}/USD", price=quote.price, price_as_of=quote.as_of)
    size = position.quantity * position.multiplier
    entry_notional = size * position.entry_price * rate
    notional = (size * quote.price * rate).quantize(_CENT, rounding=ROUND_HALF_EVEN)
    pnl = (size * (quote.price - position.entry_price) * position.sign * rate - position.costs_usd).quantize(_CENT, rounding=ROUND_HALF_EVEN)
    stop_distance = None
    if position.stop is not None:
        stop_distance = ((quote.price - position.stop) * position.sign / quote.price).quantize(Decimal("0.0001"))
    return Valuation(
        position=position,
        available=True,
        price=quote.price,
        price_as_of=quote.as_of,
        market_value_usd=notional,
        pnl_usd=pnl,
        pnl_pct=(pnl / entry_notional).quantize(Decimal("0.0001")),
        asset_change_pct=((quote.price - position.entry_price) / position.entry_price).quantize(Decimal("0.0001")),
        exposure_usd=notional,
        margin_usd=(notional / position.leverage).quantize(_CENT),
        stop_distance_pct=stop_distance,
        stale=now - quote.as_of > max_age,
        proxy_note=quote.proxy_note,
    )


# --- alerts ---


class AlertKind(str, Enum):
    UNREALIZED_LOSS = "PERDIDA_NO_REALIZADA"
    NEAR_STOP = "CERCA_DEL_STOP"
    ASSET_DROP = "CAIDA_DEL_ACTIVO"
    EXPOSURE = "EXCESO_DE_EXPOSICION"
    STALE_DATA = "DATOS_DESACTUALIZADOS"


@dataclass(frozen=True)
class AlertRules:
    max_loss_usd: Decimal = Decimal("100")
    max_loss_pct: Decimal = Decimal("0.02")  # of the position's entry notional
    stop_proximity_pct: Decimal = Decimal("0.0025")  # price within 0,25 % of the stop
    asset_drop_pct: Decimal = Decimal("0.03")  # asset 3 % below the entry price
    max_exposure_usd: Decimal = Decimal("100000")  # total notional
    hysteresis: Decimal = Decimal("0.20")  # recover 20 % beyond the threshold before RESUELTA
    cooldown: timedelta = timedelta(hours=4)

    def __post_init__(self) -> None:
        if not (Decimal("0") <= self.hysteresis < Decimal("1")):
            raise ValueError("hysteresis must be in [0, 1)")
        if self.cooldown < timedelta(0):
            raise ValueError("cooldown must not be negative")


@dataclass(frozen=True)
class AlertState:
    key: str
    active: bool = False
    since: datetime | None = None
    last_notified: datetime | None = None
    last_value: str | None = None


@dataclass(frozen=True)
class AlertEvent:
    key: str
    kind: AlertKind
    position_id: str | None
    transition: str  # ACTIVADA | REPETIDA | RESUELTA
    at: datetime
    message: str


@dataclass(frozen=True)
class _Reading:
    key: str
    kind: AlertKind
    position_id: str | None
    triggered: bool  # beyond the threshold
    cleared: bool  # back inside the hysteresis band
    message: str
    value: str


def _readings(valuations: Sequence[Valuation], rules: AlertRules) -> list[_Reading]:
    h = rules.hysteresis
    out: list[_Reading] = []
    for v in valuations:
        pid = v.position.position_id
        label = f"{v.position.instrument} {v.position.side.value}"
        if v.available and v.pnl_usd is not None and v.pnl_pct is not None:
            loss, loss_pct = -v.pnl_usd, -v.pnl_pct
            out.append(_Reading(
                f"{AlertKind.UNREALIZED_LOSS.value}:{pid}", AlertKind.UNREALIZED_LOSS, pid,
                loss >= rules.max_loss_usd or loss_pct >= rules.max_loss_pct,
                loss < rules.max_loss_usd * (1 - h) and loss_pct < rules.max_loss_pct * (1 - h),
                f"{label}: pérdida no realizada de {_fmt(loss)} USD ({_fmt(loss_pct * 100)} % del valor de entrada)", str(loss),
            ))
        if v.available and v.stop_distance_pct is not None:
            d = v.stop_distance_pct
            out.append(_Reading(
                f"{AlertKind.NEAR_STOP.value}:{pid}", AlertKind.NEAR_STOP, pid,
                d <= rules.stop_proximity_pct, d > rules.stop_proximity_pct * (1 + h),
                f"{label}: el precio está a {_fmt(d * 100)} % del stop {_fmt(v.position.stop, 5)}"
                + (" (ya lo cruzó: revisa la posición en tu bróker)" if d <= 0 else ""), str(d),
            ))
        if v.available and v.asset_change_pct is not None:
            change = v.asset_change_pct
            effect = "favorable para esta posición SHORT" if v.position.side is Side.SHORT else "en contra de esta posición LONG"
            out.append(_Reading(
                f"{AlertKind.ASSET_DROP.value}:{pid}", AlertKind.ASSET_DROP, pid,
                change <= -rules.asset_drop_pct, change > -rules.asset_drop_pct * (1 - h),
                f"{label}: el activo cayó {_fmt(-change * 100)} % desde la entrada ({effect})", str(change),
            ))
        if v.price_as_of is not None:
            out.append(_Reading(
                f"{AlertKind.STALE_DATA.value}:{pid}", AlertKind.STALE_DATA, pid, v.stale, not v.stale,
                f"{label}: el último precio es de {v.price_as_of:%Y-%m-%d %H:%M} UTC; los datos están desactualizados", str(v.price_as_of),
            ))
    exposures = [v.exposure_usd for v in valuations if v.available and v.exposure_usd is not None]
    total = sum(exposures, Decimal("0"))
    out.append(_Reading(
        f"{AlertKind.EXPOSURE.value}:total", AlertKind.EXPOSURE, None,
        total >= rules.max_exposure_usd, total < rules.max_exposure_usd * (1 - h),
        f"exposición total de {_fmt(total)} USD frente a un máximo de {_fmt(rules.max_exposure_usd)} USD", str(total),
    ))
    return out


def check_alerts(
    valuations: Sequence[Valuation], rules: AlertRules, states: Mapping[str, AlertState], now: datetime
) -> tuple[list[AlertEvent], dict[str, AlertState]]:
    """Advance every alert's state machine one step. Returns the events to
    notify and the new states (persist them; they carry the dedupe)."""
    events: list[AlertEvent] = []
    new_states = dict(states)
    seen = set()
    for reading in _readings(valuations, rules):
        seen.add(reading.key)
        state = states.get(reading.key, AlertState(reading.key))
        if not state.active and reading.triggered:
            events.append(AlertEvent(reading.key, reading.kind, reading.position_id, "ACTIVADA", now, reading.message))
            new_states[reading.key] = AlertState(reading.key, True, now, now, reading.value)
        elif state.active and reading.cleared:
            events.append(AlertEvent(reading.key, reading.kind, reading.position_id, "RESUELTA", now, reading.message))
            new_states[reading.key] = AlertState(reading.key, False, None, state.last_notified, reading.value)
        elif state.active and reading.triggered and state.last_notified is not None and now - state.last_notified >= rules.cooldown:
            events.append(AlertEvent(reading.key, reading.kind, reading.position_id, "REPETIDA", now, reading.message))
            new_states[reading.key] = replace(state, last_notified=now, last_value=reading.value)
        else:
            new_states[reading.key] = replace(state, last_value=reading.value)  # inside the band, or in cooldown: silent
    for key, state in states.items():
        if key not in seen and state.active:  # the position is gone: resolve silently
            new_states[key] = AlertState(key, False, None, state.last_notified, state.last_value)
    return events, new_states


