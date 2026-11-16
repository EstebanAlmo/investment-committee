"""Historical Replay (simulation.replay) on synthetic bars with known
outcomes. No network."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Sequence

import pytest

from investment_committee.domain import PriceBar
from investment_committee.simulation import (
    DEFAULT_UNIVERSE,
    EURUSD_PROFILE,
    HistoricalValidation,
    RiskLimits,
    TradeDirection,
    ValidationStatus,
    entry_fill,
)
from investment_committee.simulation.replay import (
    REPLAY_KIND,
    ContemporaneousObjection,
    DatedValidation,
    ExitReason,
    Policy,
    ReplayConfig,
    run_replay,
)
from investment_committee.simulation.sessions import ForexSessionRules

from ._support import bars_from_moves

HOUR = timedelta(hours=1)
MONDAY = datetime(2026, 10, 5, 0, tzinfo=timezone.utc)
UP = ["0.0005" if i % 2 == 0 else "-0.0004" for i in range(24 * 12)]
FLAT = ["0.0001" if i % 2 == 0 else "-0.0001" for i in range(24 * 12)]


def series(moves: Sequence[str], first: str = "1.10000", scale: str = "1", start: datetime = MONDAY) -> list[PriceBar]:
    return bars_from_moves([str(Decimal(m) * Decimal(scale)) for m in moves], start=start, first_close=first)


def market(eur: Sequence[str] = UP, gbp: Sequence[str] | None = None, jpy: Sequence[str] = FLAT, start: datetime = MONDAY) -> dict[str, list[PriceBar]]:
    gbp = gbp if gbp is not None else [str(-Decimal(m)) for m in UP]
    return {
        "EURUSD=X": series(eur, start=start),
        "GBPUSD=X": series(gbp, "1.30000", start=start),
        "USDJPY=X": series(jpy, "150.000", "200", start=start),
    }


def config(start: datetime = MONDAY + timedelta(days=3), days: int = 4, **kwargs) -> ReplayConfig:  # type: ignore[no-untyped-def]
    return ReplayConfig(start=start, end=start + timedelta(days=days), universe=DEFAULT_UNIVERSE, **kwargs)


def result(report, policy: Policy):  # type: ignore[no-untyped-def]
    return next(r for r in report.results if r.policy is policy)


def test_report_is_labelled_retrospective_and_ai_free() -> None:
    report = run_replay(market(), config())

    assert report.kind == REPLAY_KIND == "BACKTEST_RETROSPECTIVO"
    assert report.ai_decisions_used == 0
    assert [r.policy for r in report.results] == [Policy.ALL_ELIGIBLE, Policy.BEST_RANKED, Policy.VALIDATED_ONLY]
    assert report.no_trade_baseline == Decimal("10000")


def test_replay_is_deterministic() -> None:
    first, second = run_replay(market(), config()), run_replay(market(), config())

    assert [r.trades for r in first.results] == [r.trades for r in second.results]


# --- no look-ahead, entry after the signal ---


def test_changing_the_future_does_not_change_earlier_decisions() -> None:
    data = market()
    cut = MONDAY + timedelta(days=4, hours=12)
    altered = {s: [replace(b, open=b.open * 2, high=b.high * 2, low=b.low * 2, close=b.close * 2) if b.timestamp > cut else b for b in bars] for s, bars in data.items()}

    before = run_replay(data, config())
    after = run_replay(altered, config())

    for a, b in zip(before.results, after.results):
        # sorted by decision: the trade list is in closing order, which the future may change
        early_a = sorted((x.decided_at, x.symbol, x.entry_price, x.units) for x in a.trades if x.entry_time <= cut)
        early_b = sorted((x.decided_at, x.symbol, x.entry_price, x.units) for x in b.trades if x.entry_time <= cut)
        assert early_a == early_b


def test_entry_is_the_open_of_the_bar_starting_at_the_decision() -> None:
    data = market()
    report = run_replay(data, config())

    trades = result(report, Policy.ALL_ELIGIBLE).trades
    assert trades
    profiles = {p.symbol: p for p in DEFAULT_UNIVERSE}
    for trade in trades:
        bar = next(b for b in data[trade.symbol] if b.timestamp == trade.decided_at)
        profile = profiles[trade.symbol]
        assert trade.entry_time == trade.decided_at
        assert trade.entry_price == entry_fill(trade.direction, bar.open, profile.costs, profile.spec)
        assert trade.exit_time > trade.entry_time


def test_a_missing_bar_at_the_decision_means_no_entry() -> None:
    data = market()
    first = result(run_replay(data, config()), Policy.ALL_ELIGIBLE).trades[0]
    data[first.symbol] = [b for b in data[first.symbol] if b.timestamp != first.decided_at]

    redo = result(run_replay(data, config()), Policy.ALL_ELIGIBLE)

    assert not any(t.symbol == first.symbol and t.decided_at == first.decided_at for t in redo.trades)
    assert any(r.reason == "sin precio ejecutable" and r.decided_at == first.decided_at for r in redo.rejections)


# --- exits ---


def _first_trade(data, policy: Policy = Policy.ALL_ELIGIBLE, symbol: str = "EURUSD=X"):  # type: ignore[no-untyped-def]
    return next(t for t in result(run_replay(data, config()), policy).trades if t.symbol == symbol)


def _replace_bar(data, symbol: str, at: datetime, **changes) -> dict:  # type: ignore[no-untyped-def]
    return {**data, symbol: [replace(b, **changes) if b.timestamp == at else b for b in data[symbol]]}


def test_stop_and_target_in_the_same_bar_resolve_as_stop() -> None:
    data = market()
    trade = _first_trade(data)
    bar = next(b for b in data["EURUSD=X"] if b.timestamp == trade.entry_time)
    wide = _replace_bar(data, "EURUSD=X", trade.entry_time, high=bar.open + Decimal("0.0100"), low=bar.open - Decimal("0.0100"))

    redo = _first_trade(wide)

    assert redo.exit_reason is ExitReason.STOP and redo.same_bar_conflict
    assert redo.net_pnl_usd < 0


def test_a_gap_through_the_stop_loses_more_than_the_planned_risk() -> None:
    data = market()
    trade = _first_trade(data)
    after = trade.entry_time + HOUR
    bar = next(b for b in data["EURUSD=X"] if b.timestamp == after)
    gapped = _replace_bar(data, "EURUSD=X", after, open=bar.open - Decimal("0.0080"), low=bar.open - Decimal("0.0085"), close=bar.open - Decimal("0.0080"))

    redo = _first_trade(gapped)

    assert redo.exit_reason is ExitReason.STOP and redo.exit_time == after
    assert -redo.net_pnl_usd > redo.risk_usd  # a stop does not cap the loss


def test_positions_are_flat_by_16_00_new_york_in_summer_and_winter() -> None:
    summer = result(run_replay(market(), config()), Policy.ALL_ELIGIBLE).trades
    winter_monday = datetime(2026, 11, 9, 0, tzinfo=timezone.utc)
    winter = result(run_replay(market(start=winter_monday), config(start=winter_monday + timedelta(days=3))), Policy.ALL_ELIGIBLE).trades

    rules = ForexSessionRules()
    for trade in [*summer, *winter]:
        if trade.exit_reason is ExitReason.SESSION_CLOSE:
            assert trade.exit_time == rules.flat_time(trade.decided_at)
        assert rules.may_enter(trade.decided_at)
    assert {t.exit_time.hour for t in summer if t.exit_reason is ExitReason.SESSION_CLOSE} == {20}  # 16:00 EDT
    assert {t.exit_time.hour for t in winter if t.exit_reason is ExitReason.SESSION_CLOSE} == {21}  # 16:00 EST


def test_no_position_survives_the_session() -> None:
    report = run_replay(market(), config())

    for r in report.results:
        for trade in r.trades:
            assert trade.exit_time <= ForexSessionRules().flat_time(trade.decided_at) or trade.exit_reason is ExitReason.END_OF_DATA


# --- money ---


def test_usd_jpy_pnl_is_converted_at_the_exit_rate() -> None:
    data = market(jpy=UP)

    trade = _first_trade(data, symbol="USDJPY=X")

    assert trade.net_pnl_usd == (trade.pnl_quote / trade.exit_price).quantize(Decimal("0.01"))
    assert trade.risk_usd <= Decimal("50.00")


def test_long_and_short_pnl_signs_follow_the_position_not_the_price() -> None:
    report = result(run_replay(market(), config()), Policy.ALL_ELIGIBLE)

    gbp = [t for t in report.trades if t.symbol == "GBPUSD=X"]
    assert gbp and all(t.direction is TradeDirection.SHORT for t in gbp)  # GBP/USD falls in this market
    for trade in report.trades:
        move = (trade.exit_price - trade.entry_price) * (1 if trade.direction is TradeDirection.LONG else -1)
        assert (trade.pnl_quote > 0) == (move > 0)


def test_costs_are_reported_and_already_inside_the_pnl() -> None:
    trades = result(run_replay(market(), config()), Policy.ALL_ELIGIBLE).trades

    assert all(t.costs_usd > 0 for t in trades)
    eur = next(t for t in trades if t.symbol == "EURUSD=X")
    spec, costs = EURUSD_PROFILE.spec, EURUSD_PROFILE.costs
    assert eur.costs_usd == (Decimal(eur.units) * (costs.spread_pips + costs.slippage_pips * 2) * spec.pip_size).quantize(Decimal("0.01"))


# --- limits ---


def test_daily_loss_blocks_new_entries_for_the_rest_of_the_session() -> None:
    limits = RiskLimits(risk_per_trade=Decimal("0.005"), max_daily_loss=Decimal("0.005"), max_usd_direction_risk=Decimal("0.005"))
    data = market()
    first = result(run_replay(data, config(limits=limits)), Policy.ALL_ELIGIBLE).trades[0]
    after = first.entry_time + HOUR
    bar = next(b for b in data[first.symbol] if b.timestamp == after)
    sign = Decimal("-1") if first.direction is TradeDirection.LONG else Decimal("1")
    crash = _replace_bar(data, first.symbol, after, open=bar.open + sign * Decimal("0.0080"), high=max(bar.high, bar.open + sign * Decimal("0.0080")),
                         low=min(bar.low, bar.open + sign * Decimal("0.0080")), close=bar.open + sign * Decimal("0.0080"))

    policy = result(run_replay(crash, config(limits=limits)), Policy.ALL_ELIGIBLE)

    loss = next(t for t in policy.trades if t.decided_at == first.decided_at and t.symbol == first.symbol)
    assert loss.net_pnl_usd < -limits.max_daily_loss * limits.capital
    session_end = ForexSessionRules().flat_time(loss.decided_at)
    later_same_session = [t for t in policy.trades if loss.exit_time <= t.decided_at < session_end]
    assert later_same_session == []
    assert any(r.reason == "límite de pérdida diaria" and loss.exit_time <= r.decided_at < session_end for r in policy.rejections)


def test_aggregate_risk_and_shared_usd_exposure_are_enforced() -> None:
    limits = RiskLimits(max_usd_direction_risk=Decimal("0.005"))
    report = result(run_replay(market(jpy=[str(-Decimal(m)) for m in UP]), config(limits=limits)), Policy.ALL_ELIGIBLE)

    assert any(r.reason == "exposición compartida al USD" for r in report.rejections)
    for point in report.equity_curve:
        assert point.open_risk <= limits.max_daily_loss * Decimal("11000")


def test_open_risk_never_exceeds_the_daily_budget() -> None:
    report = result(run_replay(market(), config()), Policy.ALL_ELIGIBLE)

    assert report.max_open_positions <= 3
    for point in report.equity_curve:
        assert point.open_risk <= Decimal("0.015") * point.equity * Decimal("1.02")


# --- policies ---


def test_best_ranked_policy_takes_one_candidate_per_decision() -> None:
    report = result(run_replay(market(), config()), Policy.BEST_RANKED)

    decided = [t.decided_at for t in report.trades]
    assert len(decided) == len(set(decided))
    assert all(t.rank == 1 for t in report.trades)


def test_policy_c_does_not_trade_without_prior_evidence() -> None:
    report = result(run_replay(market(), config()), Policy.VALIDATED_ONLY)

    assert report.trades == () or list(report.trades) == []
    assert report.final_balance == Decimal("10000")
    assert set(report.rejection_counts) <= {"sin evidencia validada previa", "posición abierta en el mismo instrumento", "exposición compartida al USD", "riesgo agregado"}


def test_policy_c_uses_evidence_only_from_its_date() -> None:
    known_from = MONDAY + timedelta(days=4)  # Friday 00:00 UTC: Friday's session still trades
    evidence = DatedValidation("EURUSD=X", "tendencia-momentum-volatilidad", "1.0.0", known_from, HistoricalValidation(ValidationStatus.VALIDADA, "fixture"))

    report = result(run_replay(market(), config(validations=(evidence,))), Policy.VALIDATED_ONLY)

    assert report.trades and all(t.symbol == "EURUSD=X" and t.decided_at >= known_from for t in report.trades)
    assert any(r.reason == "sin evidencia validada previa" and r.symbol == "EURUSD=X" and r.decided_at < known_from for r in report.rejections)


def test_a_contemporaneous_objection_blocks_policy_c() -> None:
    known_from = MONDAY + timedelta(days=3)
    evidence = DatedValidation("EURUSD=X", "tendencia-momentum-volatilidad", "1.0.0", known_from, HistoricalValidation(ValidationStatus.VALIDADA, "fixture"))
    first = result(run_replay(market(), config(validations=(evidence,))), Policy.VALIDATED_ONLY).trades[0]
    objection = ContemporaneousObjection("EURUSD=X", first.decided_at - timedelta(minutes=10), "registro IA contemporáneo: NO CONCLUYENTE")

    report = run_replay(market(), config(validations=(evidence,), objections=(objection,)))

    c = result(report, Policy.VALIDATED_ONLY)
    assert not any(t.decided_at == first.decided_at for t in c.trades)
    assert report.ai_decisions_used == 1


def test_zero_trades_keep_the_capital() -> None:
    flat = market(eur=FLAT, gbp=FLAT, jpy=FLAT)

    report = run_replay(flat, config())

    for r in report.results:
        assert r.metrics.trades == 0 and r.final_balance == Decimal("10000") and r.net_return == 0
        assert r.metrics.expectancy is None and r.metrics.profit_factor is None and r.max_drawdown == 0


def test_metrics_add_up() -> None:
    report = result(run_replay(market(), config()), Policy.ALL_ELIGIBLE)

    m = report.metrics
    assert m.net_pnl == sum(t.net_pnl_usd for t in report.trades) == report.final_balance - Decimal("10000")
    assert m.wins + m.losses <= m.trades and sum(m.exits.values()) == m.trades
    assert sum(report.daily.values()) == m.net_pnl
    assert sum(x.trades for x in report.by_instrument.values()) == m.trades
    assert report.max_drawdown <= 0


def test_config_is_validated() -> None:
    with pytest.raises(ValueError):
        ReplayConfig(start=MONDAY, end=MONDAY, universe=DEFAULT_UNIVERSE)
    with pytest.raises(ValueError):
        ReplayConfig(start=MONDAY.replace(tzinfo=None), end=MONDAY + HOUR, universe=DEFAULT_UNIVERSE)




def test_london_labelled_bars_give_utc_times_and_rounded_prices() -> None:
    from zoneinfo import ZoneInfo

    london = ZoneInfo("Europe/London")
    data = {s: [replace(b, timestamp=b.timestamp.astimezone(london)) for b in bars] for s, bars in market().items()}

    trades = result(run_replay(data, config()), Policy.ALL_ELIGIBLE).trades

    assert trades == result(run_replay(market(), config()), Policy.ALL_ELIGIBLE).trades  # same moments, whatever the label offset
    for trade in trades:
        assert trade.exit_time.utcoffset() == timedelta(0) and trade.entry_time.utcoffset() == timedelta(0)
        places = 3 if trade.symbol == "USDJPY=X" else 5
        assert trade.exit_price == trade.exit_price.quantize(Decimal(1).scaleb(-places))


# --- audit regressions (September 2026 integrity review) ---


def test_a_gap_at_the_open_is_processed_before_the_decision_at_that_instant() -> None:
    # Defect found: a stop gapped through at the open of the bar starting at t
    # was only processed at t + 1h, while entries at t already used that open.
    data = market()
    trade = _first_trade(data)
    after = trade.entry_time + HOUR
    bar = next(b for b in data["EURUSD=X"] if b.timestamp == after)
    gapped = _replace_bar(data, "EURUSD=X", after, open=bar.open - Decimal("0.0080"), low=bar.open - Decimal("0.0085"), close=bar.open - Decimal("0.0080"))

    report = result(run_replay(gapped, config()), Policy.ALL_ELIGIBLE)

    redo = next(t for t in report.trades if t.symbol == "EURUSD=X" and t.decided_at == trade.decided_at)
    point = next(p for p in report.equity_curve if p.at == after)
    assert redo.exit_time == after
    assert point.balance == Decimal("10000") + sum(t.net_pnl_usd for t in report.trades if t.exit_time <= after)
    open_then = [t for t in report.trades if t.entry_time < after < t.exit_time or (t.entry_time < after and t.exit_time > after)]
    assert point.open_positions == len(open_then)


def test_exit_fills_are_on_the_tick_and_the_pnl_uses_them() -> None:
    # Defect found: P&L used the unrounded provider price while the trade
    # reported the rounded fill (differences of up to 0,15 USD).
    data = market()
    for bars in data.values():
        for i, b in enumerate(bars):  # provider-like prices with many decimals
            bars[i] = replace(b, open=b.open + Decimal("0.0000012345"), high=b.high + Decimal("0.0000012345"),
                              low=b.low + Decimal("0.0000012345"), close=b.close + Decimal("0.0000012345"))
    trades = result(run_replay(data, config()), Policy.ALL_ELIGIBLE).trades

    assert trades
    for t in trades:
        places = 3 if t.symbol == "USDJPY=X" else 5
        assert t.exit_price == t.exit_price.quantize(Decimal(1).scaleb(-places))
        sign = 1 if t.direction is TradeDirection.LONG else -1
        assert t.pnl_quote == (Decimal(t.units) * (t.exit_price - t.entry_price) * sign).quantize(Decimal("0.01"))


def test_invariants_risk_per_trade_daily_budget_and_reconciliation() -> None:
    from investment_committee.simulation.sessions import session_day

    for r in run_replay(market(jpy=UP), config(days=4)).results:
        day_start: dict = {}
        for p in r.equity_curve:
            day_start.setdefault(session_day(p.at), p.equity)
        equity = {p.at: p.equity for p in r.equity_curve}
        for t in r.trades:
            assert t.risk_usd <= Decimal("0.005") * equity[t.decided_at] + Decimal("0.01")  # 0,5 % of equity AT the decision
            d = session_day(t.decided_at)
            realized = sum((x.net_pnl_usd for x in r.trades if x.exit_time <= t.decided_at and session_day(x.exit_time - timedelta(microseconds=1)) == d), Decimal("0"))
            open_risk = sum((x.risk_usd for x in r.trades if x is not t and x.entry_time <= t.decided_at < x.exit_time), Decimal("0"))
            assert max(Decimal("0"), -realized) + open_risk + t.risk_usd <= Decimal("0.015") * day_start[d] + Decimal("0.01")
        assert r.equity_curve[-1].balance == r.final_balance == Decimal("10000") + sum(t.net_pnl_usd for t in r.trades)
        assert sum(r.daily.values()) == sum(x.net_pnl for x in r.by_instrument.values()) == r.metrics.net_pnl


def test_policy_b_opens_at_most_one_position_per_decision_moment() -> None:
    from collections import Counter

    b = result(run_replay(market(jpy=UP), config()), Policy.BEST_RANKED)

    assert b.trades and max(Counter(t.decided_at for t in b.trades).values()) == 1


def test_session_close_follows_new_york_across_the_2026_dst_gap() -> None:
    # London leaves summer time on 2026-10-25, New York on 2026-11-01: in the
    # week between, 16:00 New York is still 20:00 UTC; from 2026-11-02, 21:00 UTC.
    from zoneinfo import ZoneInfo

    london = ZoneInfo("Europe/London")
    start = datetime(2026, 10, 26, 0, tzinfo=timezone.utc)
    moves = UP * 2
    data = {s: [replace(b, timestamp=b.timestamp.astimezone(london)) for b in bars]
            for s, bars in market(eur=moves, gbp=[str(-Decimal(m)) for m in moves], jpy=moves, start=start).items()}

    trades = result(run_replay(data, ReplayConfig(start=start + timedelta(days=3), end=start + timedelta(days=12), universe=DEFAULT_UNIVERSE)), Policy.ALL_ELIGIBLE).trades

    closes = {t.exit_time.date(): t.exit_time.hour for t in trades if t.exit_reason is ExitReason.SESSION_CLOSE}
    assert closes
    for day, hour in closes.items():
        assert hour == (20 if day < datetime(2026, 11, 2).date() else 21), (day, hour)


def test_a_break_even_trade_is_zero_not_negative_zero() -> None:
    from investment_committee.conversation.replay_render import render_replay

    report = run_replay(market(), config())
    a = result(report, Policy.ALL_ELIGIBLE)
    assert all(str(t.net_pnl_usd) != "-0.00" for t in a.trades)
    assert a.metrics.wins + a.metrics.losses <= a.metrics.trades
    assert "-0,00" not in render_replay(report)
