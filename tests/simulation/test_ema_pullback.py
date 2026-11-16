"""Tests of simulation.ema_pullback (strategy C, ema-pullback-forex@0.1.0)
on made-up candles: the rule in both directions, every invalidation, the
equalities, the structural stop, the data gates and the absence of
lookahead. An independent reference in exact fractions (below) re-reads
the rule on a pseudo-random series. No market data, no result of any trade.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from investment_committee.domain import PriceBar
from investment_committee.market_intelligence.quotes import QuoteKind
from investment_committee.simulation import ema_cross, ema_pullback, ema_trend_cross
from investment_committee.simulation.ema_pullback import STRATEGY_ID, EmaPullbackParameters, Reason, evaluate, fingerprint
from investment_committee.simulation.signals import Outcome, StructuralStop
from investment_committee.simulation.wilder import ema_series

from ._ema_cross_support import HOUR, SOURCE, START, always_open, ref_atr, ref_ema, walk

D = Decimal
PIP = D("0.0001")
VALID = [(-10, 1, 1), (-6, 8, 1), (22, 1, 1)]  # two closes under EMA9, the second with a wick down to EMA20, then a strong close back


def series(tail, base: int = 745, sign: int = 1) -> list[PriceBar]:  # type: ignore[no-untyped-def]
    """`base` candles of a steady 2-pip trend, then `tail`: (close move, extra
    wick against the trend, extra wick with it), in pips, for an uptrend.
    sign = -1 mirrors everything. Open = previous close."""
    bars, close = [], D("1.05000") if sign > 0 else D("1.35000")
    moves = [(2, 1, 1)] * base + list(tail)
    for index, (move, against, with_) in enumerate(moves):
        open_, close = close, close + sign * move * PIP
        low_wick, high_wick = (against, with_) if sign > 0 else (with_, against)
        bars.append(PriceBar(START + index * HOUR, open_, max(open_, close) + high_wick * PIP, min(open_, close) - low_wick * PIP, close, 0))
    return bars


def ev(bars, **options):  # type: ignore[no-untyped-def]
    return evaluate(bars, bars[-1].timestamp + HOUR, symbol="EURUSD", data_kind=QuoteKind.SYNTHETIC, data_source=SOURCE,
                    **{"is_open": always_open, **options})


def reasons(evaluation) -> list[str]:  # type: ignore[no-untyped-def]
    return [r.value for r in evaluation.reasons]


# --- identity ---


def test_identity_and_frozen_parameters() -> None:
    p = EmaPullbackParameters()

    assert STRATEGY_ID == "ema-pullback-forex@0.1.0" and STRATEGY_ID not in (ema_cross.STRATEGY_ID, ema_trend_cross.STRATEGY_ID)
    assert (p.ema_fast, p.ema_trend_short, p.ema_trend_mid, p.ema_trend_long, p.atr_period) == (9, 20, 50, 120, 14)
    assert (p.window_bars, p.max_missing_in_window, p.contiguous_tail, p.max_leg_bars) == (720, 7, 50, 12)
    assert (p.stop_buffer_atr, p.stop_floor_atr, p.stop_cap_atr, p.net_reward_risk) == (D("0.10"), D("1.0"), D("3.0"), D("2"))
    assert fingerprint() == "c4891b951204320a8fffb22539f0a4ec399c5094f4cf4bb56039f3fa97debe71"  # a literal: changing any parameter is a new version
    assert fingerprint(replace(p, max_leg_bars=11)) != fingerprint() and fingerprint(replace(p, net_reward_risk=D("3"))) != fingerprint()


@pytest.mark.parametrize("change", [dict(ema_fast=20), dict(window_bars=100), dict(max_leg_bars=0), dict(contiguous_tail=12), dict(stop_floor_atr=D("4")),
                                    dict(net_reward_risk=D("0")), dict(stop_buffer_atr=D("-0.1"))])
def test_inconsistent_parameters_are_refused(change: dict) -> None:  # type: ignore[type-arg]
    with pytest.raises(ValueError):
        replace(EmaPullbackParameters(), **change)


# --- the rule ---


@pytest.mark.parametrize("sign, outcome, trend", [(1, Outcome.LONG, "TENDENCIA_ALCISTA"), (-1, Outcome.SHORT, "TENDENCIA_BAJISTA")])
def test_a_pullback_that_ends_inside_a_trend_is_a_signal_in_both_directions(sign: int, outcome: Outcome, trend: str) -> None:
    bars = series(VALID, sign=sign)
    e = ev(bars)

    assert e.outcome is outcome and e.is_signal and reasons(e) == [trend, "RETROCESO_VALIDO", "CONFIRMACION"]
    assert e.signal_id == e.evaluation_key == f"{STRATEGY_ID}|EURUSD|{(bars[-1].timestamp + HOUR).isoformat()}"
    assert (e.leg_bars, e.leg_start, e.candle_close, e.last_close, e.last_open) == (2, bars[-3].timestamp, bars[-1].timestamp + HOUR, bars[-1].close, bars[-1].open)
    assert (e.strategy_id, e.parameters_sha256, e.data_kind, e.bars_used) == (STRATEGY_ID, fingerprint(), QuoteKind.SYNTHETIC, 720)
    extreme = min(b.low for b in bars[-3:]) if sign > 0 else max(b.high for b in bars[-3:])
    assert e.pullback_extreme == extreme == (D("1.19660") if sign > 0 else D("1.20340"))


def test_the_indicators_and_the_stop_match_an_independent_calculation() -> None:
    bars = series(VALID)
    e, window = ev(bars), bars[-720:]
    closes = [b.close for b in window]

    for value, period in ((e.ema_fast, 9), (e.ema_trend_short, 20), (e.ema_trend_mid, 50), (e.ema_trend_long, 120)):
        assert abs(Fraction(value) - ref_ema(closes, period)) < Fraction(1, 10**18)
    assert abs(Fraction(e.atr) - ref_atr(window, 14)) < Fraction(1, 10**18)
    assert e.structural_stop == StructuralStop(reference=e.pullback_extreme - D("0.10") * e.atr, floor_atr=D("1.0"), cap_atr=D("3.0"))
    short = ev(series(VALID, sign=-1))
    assert short.structural_stop.reference == short.pullback_extreme + D("0.10") * short.atr  # above the highest high


@pytest.mark.parametrize("tail, expected", [
    ([(2, 1, 1)] * 3, ["TENDENCIA_ALCISTA", "SIN_RETROCESO"]),  # the trend simply goes on
    ([(-10, 1, 1), (-6, 8, 1)], ["TENDENCIA_ALCISTA", "RETROCESO_EN_CURSO"]),  # no close back above EMA9 yet
])
def test_without_a_leg_that_just_ended_there_is_nothing_to_confirm(tail, expected) -> None:  # type: ignore[no-untyped-def]
    e = ev(series(tail))

    assert e.outcome is Outcome.NO_CROSS and reasons(e) == expected and e.structural_stop is None and not e.is_signal


def test_without_a_trend_there_is_no_signal() -> None:
    flat = [PriceBar(START + i * HOUR, D("1.1"), D("1.1002"), D("1.0998"), D("1.1"), 0) for i in range(760)]  # every average equal: not a trend
    e = ev(flat)

    assert e.outcome is Outcome.NO_CROSS and reasons(e) == ["SIN_TENDENCIA"] and e.leg_bars is None


@pytest.mark.parametrize("tail, failed", [
    ([(-10, 1, 1), (-4, 1, 1), (22, 1, 1)], ["RETROCESO_SIN_TOCAR_EMA20"]),
    ([(-30, 1, 1), (-30, 1, 1), (-30, 1, 1), (95, 1, 1)], ["CIERRE_MAS_ALLA_DE_EMA50"]),
    ([(-8, 1, 1)] * 6 + [(22, 1, 1)], ["CIERRE_SIN_SUPERAR_EMA20", "CIERRE_MAS_ALLA_DE_EMA50"]),  # back above EMA9, which had fallen under EMA20
    ([(-12, 12, 1)] + [(0, 12, 1)] * 12 + [(14, 1, 1)], ["RETROCESO_DEMASIADO_LARGO"]),  # 13 closes under EMA9
])
def test_each_condition_that_fails_is_reported_and_gives_no_signal(tail, failed) -> None:  # type: ignore[no-untyped-def]
    for sign, trend in ((1, "TENDENCIA_ALCISTA"), (-1, "TENDENCIA_BAJISTA")):
        e = ev(series(tail, sign=sign))

        assert e.outcome is Outcome.NOT_CONFIRMED and reasons(e) == [trend, *failed] and e.structural_stop is None and e.signal_id is None


def test_a_leg_of_exactly_twelve_candles_is_still_allowed() -> None:
    twelve = ev(series([(-12, 12, 1)] + [(0, 12, 1)] * 11 + [(14, 1, 1)]))
    thirteen = ev(series([(-12, 12, 1)] + [(0, 12, 1)] * 12 + [(14, 1, 1)]))

    assert (twelve.outcome, twelve.leg_bars) == (Outcome.LONG, 12) and (thirteen.outcome, thirteen.leg_bars) == (Outcome.NOT_CONFIRMED, 13)


def test_the_confirming_candle_must_close_in_the_direction_of_the_trade() -> None:
    """The same closes, but the last candle opens above its close (a gap up
    that fades): back above EMA9 and EMA20, yet not a bullish candle."""
    bars = series(VALID)
    last = bars[-1]
    faded = bars[:-1] + [PriceBar(last.timestamp, last.close + 3 * PIP, last.close + 4 * PIP, last.low, last.close, 0)]
    doji = bars[:-1] + [PriceBar(last.timestamp, last.close, last.high, last.low, last.close, 0)]  # close == open is not bullish either

    assert ev(bars).outcome is Outcome.LONG
    for candles in (faded, doji):
        e = ev(candles)
        assert e.outcome is Outcome.NOT_CONFIRMED and reasons(e) == ["TENDENCIA_ALCISTA", "VELA_DE_CONFIRMACION_CONTRARIA"]


def test_every_failed_condition_is_listed_together() -> None:
    bars = series([(-10, 1, 1), (-4, 1, 1), (22, 1, 1)])  # no touch ...
    last = bars[-1]
    e = ev(bars[:-1] + [PriceBar(last.timestamp, last.close + PIP, last.high + PIP, last.low, last.close, 0)])  # ... and a bearish confirming candle

    assert e.outcome is Outcome.NOT_CONFIRMED and reasons(e) == ["TENDENCIA_ALCISTA", "VELA_DE_CONFIRMACION_CONTRARIA", "RETROCESO_SIN_TOCAR_EMA20"]


# --- equalities ---


def test_a_low_exactly_on_ema20_is_a_touch_and_a_tick_above_is_not() -> None:
    bars = series([(-10, 1, 1), (-4, 1, 1), (22, 1, 1)])  # no touch as it stands
    ema20 = ema_series([b.close for b in bars[-720:]], 20)[-2]  # EMA20 of the leg's second candle, in the window that ends at c
    leg_candle = bars[-2]

    def with_low(low: Decimal) -> list[PriceBar]:
        return bars[:-2] + [PriceBar(leg_candle.timestamp, leg_candle.open, leg_candle.high, low, leg_candle.close, 0), bars[-1]]

    assert ev(with_low(ema20)).outcome is Outcome.LONG
    assert reasons(ev(with_low(ema20 + D("0.000001")))) == ["TENDENCIA_ALCISTA", "RETROCESO_SIN_TOCAR_EMA20"]


def test_a_close_exactly_on_ema9_still_belongs_to_the_leg() -> None:
    """EMA9_c equals close_c exactly when close_c equals EMA9_(c-1)."""
    bars = series(VALID)
    window_previous = ema_series([b.close for b in bars[-720:]], 9)[-2]  # EMA9 of c-1, in the window that ends at c
    last = bars[-1]
    on_the_average = bars[:-1] + [PriceBar(last.timestamp, last.open, last.high, last.low, window_previous, 0)]
    e = ev(on_the_average)

    assert e.ema_fast == e.last_close == window_previous
    assert e.outcome is Outcome.NO_CROSS and reasons(e) == ["TENDENCIA_ALCISTA", "RETROCESO_EN_CURSO"]


# --- observed whole ---


def test_a_leg_that_spans_a_market_closure_gives_no_signal() -> None:
    """The calendar is closed for two hours inside the leg and there are no
    bars then: the gates accept the window (a normal closure), the rule
    does not accept the leg."""
    bars = series([(-10, 1, 1), (2, 1, 1), (2, 1, 1), (-10, 8, 1), (22, 1, 1)])
    closed = {bars[-4].timestamp, bars[-3].timestamp}
    kept = [b for b in bars if b.timestamp not in closed]
    e = ev(kept, is_open=lambda moment: not any(start <= moment < start + HOUR for start in closed))

    assert e.outcome is Outcome.NOT_CONFIRMED and "RETROCESO_CON_HUECO_O_CIERRE_DE_MERCADO" in reasons(e) and e.market_closures_in_window == 1
    assert e.leg_bars == 2 and e.leg_start == bars[-5].timestamp


def test_a_missing_hour_near_the_candle_is_stopped_by_the_data_gate_first() -> None:
    bars = series(VALID)
    e = ev(bars[:-2] + bars[-1:])

    assert e.outcome is Outcome.DATA_REJECTED and reasons(e) == ["DATOS_INCOMPLETOS"] and e.atr is None


@pytest.mark.parametrize("count, reason", [(719, "DATOS_INSUFICIENTES"), (720, None)])
def test_the_window_is_720_expected_hours(count: int, reason: str | None) -> None:
    e = ev(series([], base=count))

    assert (reasons(e) == [reason]) if reason else (e.outcome is Outcome.NO_CROSS and e.bars_used == 720)


def test_a_candle_outside_the_calendar_is_not_evaluated() -> None:
    bars = series(VALID)
    e = ev(bars, is_open=lambda moment: moment < bars[-1].timestamp)

    assert e.outcome is Outcome.DATA_REJECTED and reasons(e) == ["MERCADO_CERRADO"]


# --- no lookahead, no state ---


def test_nothing_after_the_candle_changes_its_evaluation() -> None:
    bars = series(VALID + [(-40, 30, 1), (55, 1, 30), (-70, 1, 1)])
    as_of = bars[-4].timestamp + HOUR  # the confirming candle of VALID
    alone = evaluate(bars[:-3], as_of, symbol="EURUSD", data_kind=QuoteKind.SYNTHETIC, data_source=SOURCE, is_open=always_open)
    with_future = evaluate(bars, as_of, symbol="EURUSD", data_kind=QuoteKind.SYNTHETIC, data_source=SOURCE, is_open=always_open)
    shuffled = evaluate(bars[::-1], as_of, symbol="EURUSD", data_kind=QuoteKind.SYNTHETIC, data_source=SOURCE, is_open=always_open)

    assert alone.outcome is Outcome.LONG
    assert replace(with_future, bars_received=0, forming_bars_excluded=0) == replace(alone, bars_received=0, forming_bars_excluded=0)
    assert with_future.forming_bars_excluded == 3 and shuffled == with_future  # later bars are counted and set aside; order does not matter


def test_a_forming_candle_is_never_read() -> None:
    bars = series(VALID)
    inside = bars[-1].timestamp + timedelta(minutes=59)  # the confirming candle has not closed yet
    e = evaluate(bars, inside, symbol="EURUSD", data_kind=QuoteKind.SYNTHETIC, data_source=SOURCE, is_open=always_open)

    assert e.candle_close == bars[-1].timestamp and e.outcome is Outcome.NO_CROSS and reasons(e) == ["TENDENCIA_ALCISTA", "RETROCESO_EN_CURSO"]


def test_the_same_candle_always_evaluates_the_same() -> None:
    bars = series(VALID)

    assert ev(bars) == ev(list(bars)) == ev(bars + [])


# --- an independent reading of the rule ---


def reference(window: list[PriceBar]) -> tuple[str, int | None]:
    """The rule of spec-007 written apart, in exact fractions, with every
    average recomputed by the closed-form sum for each candle it reads."""
    closes = [b.close for b in window]
    c = len(window) - 1

    def avg(i: int, period: int) -> Fraction:
        return ref_ema(closes[: i + 1], period)

    def up(i: int) -> bool:
        return avg(i, 20) > avg(i, 50) > avg(i, 120)

    def down(i: int) -> bool:
        return avg(i, 20) < avg(i, 50) < avg(i, 120)

    if up(c):
        s, holds = 1, up
    elif down(c):
        s, holds = -1, down
    else:
        return "SIN_CRUCE", None
    below = lambda i: s * (Fraction(closes[i]) - avg(i, 9)) <= 0  # noqa: E731
    if not below(c - 1) or below(c):
        return "SIN_CRUCE", None
    n = 1
    while n < 13 and below(c - n - 1):
        n += 1
    leg = list(range(c - n, c))
    ok = (s * (Fraction(closes[c]) - avg(c, 20)) > 0 and s * (closes[c] - window[c].open) > 0 and n <= 12
          and any(s * (Fraction(window[i].low if s > 0 else window[i].high) - avg(i, 20)) <= 0 for i in leg)
          and all(holds(i) for i in leg) and not any(s * (Fraction(closes[i]) - avg(i, 50)) < 0 for i in leg)
          and all(window[i + 1].timestamp - window[i].timestamp == HOUR for i in leg))
    return ("LONG" if s > 0 else "SHORT") if ok else "CRUCE_SIN_CONFIRMACION", n


def test_the_module_agrees_with_the_independent_reading_on_a_pseudo_random_series() -> None:
    bars = walk(1000, seed=11)
    seen: dict[str, int] = {}
    for index in range(880, 1000):
        e = ev(bars[: index + 1])
        expected, n = reference(bars[index - 719: index + 1])
        assert (e.outcome.value, e.leg_bars) == (expected, n), index
        seen[expected] = seen.get(expected, 0) + 1

    assert set(seen) >= {"SIN_CRUCE", "CRUCE_SIN_CONFIRMACION"} and (seen.get("LONG", 0) + seen.get("SHORT", 0)) >= 1
