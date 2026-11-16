"""Portfolio Guardian: valuation and alert state machines (pure), plus the
application service over fake market data. No network, no orders."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from investment_committee.application import GuardianSettings, PortfolioGuardianService
from investment_committee.domain import PriceBar
from investment_committee.market_intelligence import MarketDataError, MarketIntelligenceService
from investment_committee.portfolio.guardian import (
    AlertKind,
    AlertRules,
    AssetKind,
    PortfolioPosition,
    Quote,
    Side,
    check_alerts,
    forex_position,
    usd_rates,
    value_position,
)
from investment_committee.portfolio.store import InMemoryAlertStore, InMemoryPositionStore, JsonAlertStore, JsonLinesPositionStore

from tests.application.test_opportunities import FakeMarket

UTC = timezone.utc
NOW = datetime(2026, 10, 13, 14, 30, tzinfo=UTC)  # Tuesday
FRESH = NOW.replace(minute=0)
AGE = timedelta(hours=3)


def quote(symbol: str, price: str, at: datetime = FRESH) -> Quote:
    return Quote(symbol, Decimal(price), at, "test")


def eur(side: Side, entry: str = "1.10000", qty: str = "10000", **kw) -> PortfolioPosition:  # type: ignore[no-untyped-def]
    return forex_position("p1", "EUR/USD", side, Decimal(qty), Decimal(entry), NOW - timedelta(days=1), **kw)


# --- valuation ---


def test_long_and_short_pnl_follow_the_position_not_the_asset() -> None:
    q = {"EURUSD=X": quote("EURUSD=X", "1.09000")}  # the asset fell 0,91 %

    long_ = value_position(eur(Side.LONG), q["EURUSD=X"], usd_rates(q), NOW, AGE)
    short = value_position(eur(Side.SHORT), q["EURUSD=X"], usd_rates(q), NOW, AGE)

    assert long_.pnl_usd == Decimal("-100.00") and short.pnl_usd == Decimal("100.00")
    assert long_.asset_change_pct == short.asset_change_pct == Decimal("-0.0091")
    assert long_.market_value_usd == short.market_value_usd == Decimal("10900.00")


def test_usd_jpy_pnl_is_converted_with_its_own_rate() -> None:
    position = forex_position("p2", "USD/JPY", Side.LONG, Decimal("10000"), Decimal("150.000"), NOW)
    q = {"USDJPY=X": quote("USDJPY=X", "151.500")}

    v = value_position(position, q["USDJPY=X"], usd_rates(q), NOW, AGE)

    # 10.000 x 1,5 JPY = 15.000 JPY, at 151,5 JPY per USD
    assert v.pnl_usd == Decimal("99.01") and v.market_value_usd == Decimal("10000.00")


def test_leverage_multiplier_and_costs() -> None:
    gold = PortfolioPosition("g1", "XAUUSD", AssetKind.METAL, Side.LONG, Decimal("1"), Decimal("4000"), "USD", NOW,
                             costs_usd=Decimal("12"), multiplier=Decimal("100"), leverage=Decimal("20"), stop=Decimal("3950"))

    v = value_position(gold, quote("XAUUSD", "4010"), {"USD": Decimal("1")}, NOW, AGE)

    assert v.market_value_usd == Decimal("401000.00") and v.margin_usd == Decimal("20050.00")
    assert v.pnl_usd == Decimal("988.00")  # 100 oz x 10 USD - 12 USD of costs
    assert v.stop_distance_pct == Decimal("0.0150")


def test_missing_rate_or_price_means_not_available() -> None:
    eur_stock = PortfolioPosition("s1", "SAP.DE", AssetKind.EQUITY, Side.LONG, Decimal("10"), Decimal("200"), "EUR", NOW)

    assert not value_position(eur_stock, quote("SAP.DE", "210"), {"USD": Decimal("1")}, NOW, AGE).available
    assert "tipo de cambio EUR/USD" in value_position(eur_stock, quote("SAP.DE", "210"), {"USD": Decimal("1")}, NOW, AGE).reason
    assert not value_position(eur(Side.LONG), None, {}, NOW, AGE).available


def test_stale_quotes_are_flagged() -> None:
    old = quote("EURUSD=X", "1.1", at=NOW - timedelta(hours=5))

    assert value_position(eur(Side.LONG), old, {"USD": Decimal("1")}, NOW, AGE).stale


def test_positions_are_validated() -> None:
    with pytest.raises(ValueError):
        forex_position("x", "EUR/USD", Side.LONG, Decimal("0"), Decimal("1.1"), NOW)
    with pytest.raises(ValueError):
        forex_position("x", "EURO", Side.LONG, Decimal("1"), Decimal("1.1"), NOW)


# --- alerts ---


def _check(price: str, states, now: datetime = NOW, side: Side = Side.LONG, rules: AlertRules | None = None, **kw):  # type: ignore[no-untyped-def]
    q = quote("EURUSD=X", price, at=now.replace(minute=0))
    v = value_position(eur(side, **kw), q, {"USD": Decimal("1")}, now, AGE)
    return check_alerts([v], rules or AlertRules(), states, now)


def test_loss_alert_fires_once_then_respects_cooldown_and_hysteresis() -> None:
    events, states = _check("1.08800", {})  # -120 USD
    assert [(e.kind, e.transition) for e in events] == [(AlertKind.UNREALIZED_LOSS, "ACTIVADA")]

    again, states = _check("1.08700", states, NOW + timedelta(hours=1))  # still losing, inside cooldown
    assert again == []
    hovering, states = _check("1.09100", states, NOW + timedelta(hours=2))  # -90: below 100 but inside the 20 % band
    assert hovering == []
    repeat, states = _check("1.08800", states, NOW + timedelta(hours=5))  # cooldown over, still beyond
    assert [e.transition for e in repeat] == ["REPETIDA"]
    resolved, states = _check("1.09500", states, NOW + timedelta(hours=6))  # -50: recovered beyond the band
    assert [e.transition for e in resolved] == ["RESUELTA"]
    quiet, _ = _check("1.09500", states, NOW + timedelta(hours=7))
    assert quiet == []


def test_a_falling_asset_is_a_drop_alert_but_not_a_loss_for_a_short() -> None:
    events, _ = _check("1.06000", {}, side=Side.SHORT)

    kinds = {e.kind for e in events}
    assert AlertKind.ASSET_DROP in kinds and AlertKind.UNREALIZED_LOSS not in kinds
    drop = next(e for e in events if e.kind is AlertKind.ASSET_DROP)
    assert "favorable para esta posición SHORT" in drop.message


def test_near_stop_alert() -> None:
    events, _ = _check("1.09520", {}, stop=Decimal("1.09500"))

    assert any(e.kind is AlertKind.NEAR_STOP for e in events)


def test_exposure_alert() -> None:
    events, _ = _check("1.10000", {}, rules=AlertRules(max_exposure_usd=Decimal("5000")))

    assert [e.kind for e in events] == [AlertKind.EXPOSURE]


def test_stale_data_alert_is_not_repeated() -> None:
    old = quote("EURUSD=X", "1.1", at=NOW - timedelta(hours=6))
    v = value_position(eur(Side.LONG), old, {"USD": Decimal("1")}, NOW, AGE)

    events, states = check_alerts([v], AlertRules(), {}, NOW)
    again, _ = check_alerts([v], AlertRules(), states, NOW + timedelta(minutes=30))

    assert [e.kind for e in events] == [AlertKind.STALE_DATA] and again == []


def test_rules_are_validated() -> None:
    with pytest.raises(ValueError):
        AlertRules(hysteresis=Decimal("1.5"))


# --- stores and service ---


def _bars(price: str, closes_at: datetime) -> list[PriceBar]:
    p = Decimal(price)
    return [PriceBar(closes_at - timedelta(hours=k + 1), p, p, p, p, 0) for k in reversed(range(5))]


def test_service_values_manual_positions_and_persists_alert_state(tmp_path) -> None:  # type: ignore[no-untyped-def]
    positions = JsonLinesPositionStore(tmp_path / "positions.jsonl")
    positions.add(eur(Side.LONG), NOW)
    positions.add(PortfolioPosition("g1", "XAUUSD", AssetKind.METAL, Side.LONG, Decimal("1"), Decimal("4000"), "USD", NOW), NOW)
    alerts = JsonAlertStore(tmp_path)
    market = FakeMarket({"EURUSD=X": _bars("1.08800", FRESH)})
    service = PortfolioGuardianService(MarketIntelligenceService(market), positions, alerts, clock=lambda: NOW)

    first = service.check()
    second = service.check()

    by_id = {v.position.position_id: v for v in first.view.valuations}
    assert by_id["p1"].pnl_usd == Decimal("-120.00")
    assert not by_id["g1"].available and "XAU/USD spot" in by_id["g1"].reason
    assert [e.transition for e in first.events] == ["ACTIVADA"] and second.events == []
    assert (tmp_path / "portfolio_alerts.jsonl").read_text(encoding="utf-8").count("\n") == 1
    assert all(r[0] != "GC=F" for r in market.requests)  # the futures proxy is off by default


def test_gold_futures_proxy_is_explicit() -> None:
    positions = InMemoryPositionStore()
    positions.add(PortfolioPosition("g1", "XAUUSD", AssetKind.METAL, Side.LONG, Decimal("1"), Decimal("4000"), "USD", NOW), NOW)
    market = FakeMarket({"GC=F": _bars("4100", FRESH)})
    service = PortfolioGuardianService(MarketIntelligenceService(market), positions, InMemoryAlertStore(),
                                       settings=GuardianSettings(allow_gold_futures_proxy=True), clock=lambda: NOW)

    (v,) = service.view().valuations

    assert v.available and v.pnl_usd == Decimal("100.00") and v.proxy_note == "precio de futuros COMEX GC=F, no spot"


def test_closing_a_registered_position_only_stops_monitoring() -> None:
    positions = InMemoryPositionStore()
    positions.add(eur(Side.LONG), NOW)
    positions.close("p1", NOW, "vendida en el bróker")

    assert positions.open_positions() == []
    with pytest.raises(Exception):
        positions.close("p1", NOW)


def test_missing_market_data_leaves_the_valuation_unavailable() -> None:
    positions = InMemoryPositionStore()
    positions.add(eur(Side.LONG), NOW)
    service = PortfolioGuardianService(MarketIntelligenceService(FakeMarket({"EURUSD=X": MarketDataError("down")})), positions, InMemoryAlertStore(), clock=lambda: NOW)

    view = service.view()

    assert view.unavailable == 1 and view.total_exposure_usd == 0
