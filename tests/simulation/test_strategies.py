"""Experimental strategies V2 (simulation.strategies): deterministic rules,
identity and frozen parameters, closed bars only. Synthetic bars."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Sequence

import pytest

from investment_committee.domain import PriceBar
from investment_committee.simulation import (
    EURUSD_PROFILE,
    USDJPY_PROFILE,
    RiskLimits,
    SignalReason,
    TradeDirection,
    technical_direction,
)
from investment_committee.simulation.strategies import (
    BreakoutStrategy,
    MeanReversionStrategy,
    TrendHTFStrategy,
    all_strategies,
    higher_timeframe_closes,
    parameters_fingerprint,
    standard_deviation,
    strategy_by_id,
    strategy_parameters,
)

from ._support import HOUR, bars_from_moves, closing_time

UTC = timezone.utc
ACCOUNT = RiskLimits().account_in(Decimal("10000"))
TUESDAY = datetime(2026, 10, 6, 0, tzinfo=UTC)


def run(strategy, bars: Sequence[PriceBar], as_of: datetime | None = None, profile=EURUSD_PROFILE):  # type: ignore[no-untyped-def]
    return strategy.evaluate(bars, as_of or closing_time(bars), signal_id="t", profile=profile, account=ACCOUNT)


def trend(sign: int, count: int = 200, start: datetime = datetime(2026, 9, 28, tzinfo=UTC)) -> list[PriceBar]:
    return bars_from_moves([str(Decimal("0.0005") * sign) if i % 2 == 0 else str(Decimal("-0.0004") * sign) for i in range(count)], start=start)


# --- identity and frozen parameters ---


def test_each_strategy_has_its_own_identity_and_v1_is_still_there() -> None:
    ids = [f"{s.name}@{s.version}" for s in all_strategies()]

    assert ids == ["tendencia-momentum-volatilidad@1.0.0", "tendencia-confirmada-4h@0.1.0", "ruptura-rango-londres@0.1.0", "reversion-media-lateral@0.1.0"]
    assert strategy_by_id("ruptura-rango-londres@0.1.0").name == "ruptura-rango-londres"
    with pytest.raises(KeyError):
        strategy_by_id("nada@9")


def test_parameters_are_frozen_by_fingerprint() -> None:
    expected = {  # changing a value must come with a new version
        "tendencia-confirmada-4h": "TrendHTFParameters",
        "ruptura-rango-londres": "BreakoutParameters",
        "reversion-media-lateral": "MeanReversionParameters",
    }
    for strategy in all_strategies()[1:]:
        params = strategy_parameters(strategy)
        assert type(params).__name__ == expected[strategy.name]
        assert parameters_fingerprint(params) == parameters_fingerprint(type(params)())
        with pytest.raises(Exception):
            params.stop_atr_multiple = Decimal("9")  # type: ignore[misc]
    assert parameters_fingerprint(strategy_parameters(TrendHTFStrategy())) != parameters_fingerprint(replace(strategy_parameters(TrendHTFStrategy()), htf_sma=21))


def test_v2_strategies_share_v1_execution_and_risk_gates() -> None:
    for strategy in all_strategies()[1:]:
        p = strategy.parameters
        assert (p.min_net_reward_risk, p.max_cost_fraction_of_risk, p.atr_min_pips, p.atr_max_pips) == (Decimal("1.5"), Decimal("0.25"), 3, 40)
        assert strategy.calibrated_symbols == frozenset()  # never designed on any pair's data


# --- 1. trend with 4h confirmation ---


def test_trend_htf_long_and_short() -> None:
    long_ = run(TrendHTFStrategy(), trend(1))
    short = run(TrendHTFStrategy(), trend(-1))

    assert long_.direction is TradeDirection.LONG and SignalReason.HTF_CONFIRMS in long_.reasons
    assert short.direction is TradeDirection.SHORT and SignalReason.SETUP_SHORT in short.reasons
    assert long_.strategy_name == "tendencia-confirmada-4h" and long_.strategy_version == "0.1.0"


def test_trend_htf_needs_the_4h_confirmation() -> None:
    # a long fall, then a short rally: 1h turns up but 4h is still down
    bars = bars_from_moves(["-0.0005", "0.0003"] * 90 + ["0.0006", "-0.0002"] * 12, start=datetime(2026, 9, 28, tzinfo=UTC))

    signal = run(TrendHTFStrategy(), bars)

    assert signal.direction is TradeDirection.NO_TRADE
    assert SignalReason.HTF_NOT_CONFIRMING in signal.reasons or SignalReason.TREND_SIDEWAYS in signal.reasons


def test_4h_buckets_never_include_an_unfinished_bucket() -> None:
    bars = bars_from_moves(["0.0001"] * 10, start=datetime(2026, 10, 6, 0, tzinfo=UTC))  # 00:00-09:00 opens
    closes = higher_timeframe_closes(bars, 4, datetime(2026, 10, 6, 10, tzinfo=UTC))

    assert closes == [bars[3].close, bars[7].close]  # buckets 00-04 and 04-08; 08-12 has not elapsed


# --- 2. breakout ---


def _breakout_bars(last_move: str, close_hour_utc: int) -> list[PriceBar]:
    # 40 quiet bars ending so the LAST bar closes at `close_hour_utc`
    end_open = datetime(2026, 10, 6, close_hour_utc - 1, tzinfo=UTC)
    start = end_open - 40 * HOUR
    moves = ["0.0003" if i % 2 == 0 else "-0.0003" for i in range(40)] + [last_move]
    return bars_from_moves(moves, start=start)


def test_breakout_up_down_and_inside() -> None:
    up = run(BreakoutStrategy(), _breakout_bars("0.0025", 10))  # closes 10:00 UTC = 11:00 London (BST)
    down = run(BreakoutStrategy(), _breakout_bars("-0.0025", 10))
    inside = run(BreakoutStrategy(), _breakout_bars("0.0001", 10))

    assert SignalReason.BREAKOUT_UP in up.reasons and technical_direction(up) is TradeDirection.LONG
    assert SignalReason.BREAKOUT_DOWN in down.reasons and technical_direction(down) is TradeDirection.SHORT
    assert inside.direction is TradeDirection.NO_TRADE and SignalReason.NO_BREAKOUT in inside.reasons


def test_breakout_only_in_london_morning_with_dst() -> None:
    late = run(BreakoutStrategy(), _breakout_bars("0.0025", 13))  # 14:00 London: outside
    assert late.reasons[0] is SignalReason.OUTSIDE_TRADING_HOURS
    # in winter London = UTC: a bar closing 12:00 UTC is 12:00 London (inside), 13:00 UTC is outside
    winter_end = datetime(2026, 11, 10, 11, tzinfo=UTC)
    bars = bars_from_moves(["0.0003", "-0.0003"] * 20 + ["0.0025"], start=winter_end - 40 * HOUR)
    assert SignalReason.OUTSIDE_TRADING_HOURS not in run(BreakoutStrategy(), bars).reasons


def test_breakout_needs_a_compressed_range() -> None:
    wide = bars_from_moves(["0.0030", "-0.0001"] * 20 + ["0.0040"], start=datetime(2026, 10, 6, 9, tzinfo=UTC) - 40 * HOUR)

    assert SignalReason.RANGE_TOO_WIDE in run(BreakoutStrategy(), wide).reasons


# --- 3. mean reversion ---


def _ranging_then(last_moves: list[str]) -> list[PriceBar]:
    base = ["0.0006", "-0.0006", "0.0004", "-0.0004"] * 20
    return bars_from_moves(base + last_moves, start=datetime(2026, 10, 5, 0, tzinfo=UTC))


def test_mean_reversion_buys_a_low_extreme_only_when_ranging() -> None:
    long_ = run(MeanReversionStrategy(), _ranging_then(["-0.0010", "-0.0012", "-0.0014"]))
    trending = run(MeanReversionStrategy(), trend(-1))

    assert SignalReason.MARKET_RANGING in long_.reasons and SignalReason.BAND_EXTREME_LOW in long_.reasons
    assert technical_direction(long_) is TradeDirection.LONG
    assert SignalReason.MARKET_TRENDING in trending.reasons and trending.direction is TradeDirection.NO_TRADE


def test_mean_reversion_sells_a_high_extreme() -> None:
    short = run(MeanReversionStrategy(), _ranging_then(["0.0012", "0.0014", "0.0016", "0.0018"]))

    assert SignalReason.BAND_EXTREME_HIGH in short.reasons and technical_direction(short) is TradeDirection.SHORT


def test_standard_deviation() -> None:
    assert standard_deviation([Decimal(2), Decimal(4), Decimal(4), Decimal(4), Decimal(5), Decimal(5), Decimal(7), Decimal(9)]) == 2


# --- common gates and time integrity ---


@pytest.mark.parametrize("strategy", [TrendHTFStrategy(), BreakoutStrategy(), MeanReversionStrategy()], ids=lambda s: s.name)
def test_gates_and_closed_bars_only(strategy) -> None:  # type: ignore[no-untyped-def]
    bars = trend(1)
    now = closing_time(bars)

    assert run(strategy, bars[:10]).reasons == (SignalReason.INSUFFICIENT_DATA,)
    assert run(strategy, bars, as_of=now + timedelta(hours=5)).reasons == (SignalReason.STALE_DATA,)
    saturday = bars_from_moves(["0.0001"] * 200, start=datetime(2026, 10, 2, 0, tzinfo=UTC))
    assert run(strategy, saturday, as_of=datetime(2026, 10, 10, 12, tzinfo=UTC)).reasons[0] in (SignalReason.MARKET_CLOSED, SignalReason.STALE_DATA)
    future = replace(bars[-1], timestamp=bars[-1].timestamp + HOUR, close=bars[-1].close * 2, high=bars[-1].high * 2)
    assert run(strategy, [*bars, future], as_of=now) == run(strategy, bars, as_of=now)  # a still-forming bar is ignored


def test_usd_jpy_is_supported_with_its_own_pip() -> None:
    bars = bars_from_moves([str(Decimal("0.10") if i % 2 == 0 else Decimal("-0.08")) for i in range(200)], start=datetime(2026, 9, 28, tzinfo=UTC), first_close="150.000")

    # the scanner hands a JPY-quoted strategy its capital in JPY (simulation.opportunities)
    signal = TrendHTFStrategy().evaluate(bars, closing_time(bars), signal_id="t", profile=USDJPY_PROFILE,
                                         account=RiskLimits().account_in(Decimal("10000") * bars[-1].close))

    assert signal.direction is TradeDirection.LONG and signal.reading.atr_pips is not None and signal.reading.atr_pips > 5
