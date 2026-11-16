"""Strategy C through the shared proposal engine and the backtest engine:
structural stops (level, floor, cap), the 2 R target, sizing, sessions, and
proof that signals without a structural stop (A, B) are built exactly as
before. Made-up prices with levels computed by hand; no historical data.

Base quote for every case: bid 1.08490, ask 1.08500, slippage 0.2 pip,
ATR 10 pips, 10 000 USD, no commission, no financing.
  LONG   E = 1.08502;   SHORT  E = 1.08488
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_committee.market_intelligence.quotes import QuoteKind, estimated_from_mid
from investment_committee.simulation import ema_pullback
from investment_committee.simulation.forex_backtest import BASE, COSTS_X2, STRATEGY_A, STRATEGY_B, STRATEGY_C, STRATEGY_EXECUTION, hypothetical_commission, run
from investment_committee.simulation.forex_backtest_metrics import record
from investment_committee.simulation.opportunities import AccountState
from investment_committee.simulation.signals import Outcome, StructuralStop
from investment_committee.simulation.trade_proposal import ExecutionParameters, ExitReason, ProposalStatus, Rejection, build_proposal

from ._ema_cross_support import SOURCE, always_open
from ._forex_backtest_support import HOUR, MID, T0, Scripted, play, sig, tape
from .test_ema_pullback import VALID, series

D = Decimal
C_EXECUTION = STRATEGY_EXECUTION[ema_pullback.STRATEGY_ID]


@dataclass(frozen=True)
class Structured(Scripted):
    structural_stop: StructuralStop | None = None


def signal(reference: str | None, outcome: Outcome = Outcome.LONG, offset: int = 0, floor: str = "1.0", cap: str = "3.0") -> Structured:
    stop = None if reference is None else StructuralStop(D(reference), D(floor), D(cap))
    return Structured("EURUSD", outcome, T0 + offset * HOUR, MID, D("0.00100"), structural_stop=stop, strategy_id=ema_pullback.STRATEGY_ID)


def propose(s: Structured, execution: ExecutionParameters = C_EXECUTION):  # type: ignore[no-untyped-def]
    quote = estimated_from_mid("EURUSD", s.candle_close, MID, D("0.00010"), "prueba")
    return build_proposal(s, quote, s.candle_close, account=AccountState.fresh(D("10000")), execution=execution)


# --- the stop ---


def test_the_stop_is_the_signals_level_and_the_target_is_two_r() -> None:
    """SL 1.08322: D = 0.00180 (1.8 ATR). l = 0.00180 + 0.00002 = 0.00182.
    T = 2 x 0.00182 = 0.00364 -> TP 1.08866. units = floor(50 / 0.00182 / 1000) = 27 -> 27 000;
    loss at the stop 27 000 x 0.00182 = 49.14; gain at the target 27 000 x 0.00364 = 98.28."""
    p = propose(signal("1.08322"))

    assert p.status is ProposalStatus.ACCEPTED and (p.entry, p.stop, p.target) == (D("1.08502"), D("1.08322"), D("1.08866"))
    assert (p.stop_distance, p.target_distance, p.units) == (D("0.00180"), D("0.00364"), 27000)
    assert (p.estimated_loss_at_stop, p.expected_net_profit, p.risk_budget) == (D("49.14"), D("98.28"), D("50.00"))
    assert p.net_reward_risk_before_financing == D("2") and p.gross_reward_risk == D("0.00364") / D("0.00180")


def test_a_short_mirrors_it() -> None:
    """E 1.08488; SL 1.08668 (D 0.00180); T 0.00364 -> TP 1.08124."""
    p = propose(signal("1.08668", Outcome.SHORT))

    assert (p.entry, p.stop, p.target, p.units, p.direction) == (D("1.08488"), D("1.08668"), D("1.08124"), 27000, "SHORT")


def test_a_reference_between_ticks_is_rounded_to_the_nearest_tick() -> None:
    assert propose(signal("1.083224")).stop == D("1.08322") and propose(signal("1.083226")).stop == D("1.08323")
    assert propose(signal("1.083225")).stop == D("1.08322")  # half to even, like every other stop


@pytest.mark.parametrize("reference, outcome, stop", [
    ("1.08450", Outcome.LONG, "1.08402"),  # 5.2 pips from the entry: moved out to 1.0 ATR
    ("1.08402", Outcome.LONG, "1.08402"),  # exactly 1.0 ATR: kept
    ("1.08401", Outcome.LONG, "1.08401"),
    ("1.08600", Outcome.LONG, "1.08402"),  # a level on the wrong side of the entry: the floor still gives a stop
    ("1.08540", Outcome.SHORT, "1.08588"),
])
def test_a_stop_nearer_than_one_atr_is_moved_out_to_one_atr(reference: str, outcome: Outcome, stop: str) -> None:
    p = propose(signal(reference, outcome))

    assert p.accepted and p.stop == D(stop) and p.stop_distance >= D("0.00100")


def test_a_stop_further_than_three_atr_is_no_trade() -> None:
    at_the_cap, beyond = propose(signal("1.08202")), propose(signal("1.08201"))  # 3.00 and 3.01 ATR from 1.08502
    short = propose(signal("1.08789", Outcome.SHORT))

    assert at_the_cap.accepted and at_the_cap.stop_distance == D("0.00300")  # the limit is inclusive
    for p in (beyond, short):
        assert p.status is ProposalStatus.REJECTED and p.rejections == (Rejection.STOP_TOO_FAR,) and p.units is None
        assert "3.0 ATR" in p.detail and p.stop is not None  # the levels are shown, no size is computed
    assert Rejection.STOP_TOO_FAR.value == "RETROCESO_DEMASIADO_PROFUNDO"


def test_a_wider_stop_means_a_smaller_position_and_the_same_risk() -> None:
    """3.0 ATR: l = 0.00302 -> floor(50 / 0.00302 / 1000) = 16 -> 16 000 units, 48.32 at the stop.
    1.0 ATR: l = 0.00102 -> 49 000 units, 49.98 at the stop."""
    wide, tight = propose(signal("1.08202")), propose(signal("1.08402"))

    assert (wide.units, wide.estimated_loss_at_stop) == (16000, D("48.32")) and (tight.units, tight.estimated_loss_at_stop) == (49000, D("49.98"))
    assert wide.estimated_loss_at_stop <= wide.risk_budget and tight.estimated_loss_at_stop <= tight.risk_budget


def test_floor_and_cap_travel_with_the_signal() -> None:
    assert propose(signal("1.08450", floor="0.5")).stop == D("1.08450") and propose(signal("1.08352", cap="1.2")).rejections == (Rejection.STOP_TOO_FAR,)


# --- A and B are built exactly as before ---


def test_a_signal_without_a_structural_stop_keeps_the_fixed_atr_stop() -> None:
    """A and B carry no structural stop: SL = E - 1.5 ATR = 1.08352, 3 R -> TP 1.08958, 32 000 units -
    the numbers of their own tests, with the default execution parameters."""
    plain, none = sig(0), signal(None)
    for s in (plain, none):
        p = propose(s, ExecutionParameters())
        assert (p.entry, p.stop, p.target, p.units) == (D("1.08502"), D("1.08352"), D("1.08958"), 32000)
    assert not hasattr(plain, "structural_stop")
    assert propose(signal(None)) == propose(Structured(**{**signal(None).__dict__}))  # deterministic


def test_only_c_has_its_own_execution_parameters() -> None:
    assert set(STRATEGY_EXECUTION) == {ema_pullback.STRATEGY_ID} and C_EXECUTION == ExecutionParameters(min_net_reward_risk=D("2"))
    assert BASE.execution() == BASE.execution(STRATEGY_A.strategy_id) == BASE.execution(STRATEGY_B.strategy_id) == BASE.execution("otra@1") == ExecutionParameters()
    assert BASE.execution(STRATEGY_C.strategy_id) == C_EXECUTION and C_EXECUTION.stop_atr_multiple == D("1.5")  # unused by C: its stop is structural
    assert (COSTS_X2.execution(STRATEGY_C.strategy_id).min_net_reward_risk, hypothetical_commission(D("7")).execution(STRATEGY_C.strategy_id).commission_per_100k) == (D("2"), D("7"))


# --- through the backtest engine ---


def c_run(signals, bars, **options):  # type: ignore[no-untyped-def]
    from investment_committee.simulation.forex_backtest import run_account

    by_time = {s.candle_close: s for s in signals}
    return run_account(ema_pullback.STRATEGY_ID, ema_pullback.fingerprint(), {"EURUSD": by_time}, {"EURUSD": bars}, start=T0 - 4 * HOUR,
                       end=T0 + options.pop("last", 100) * HOUR, **options)


def test_the_engine_exits_c_at_its_structural_stop_and_at_its_two_r_target() -> None:
    """Stop 1.08322 is touched when low - 0.00005 <= 1.08322: fill 1.08320, 27 000 x -0.00182 = -49.14 (-1 R).
    Target 1.08866 when high - 0.00005 >= 1.08866: 27 000 x 0.00364 = +98.28 (+2 R)."""
    stopped = c_run([signal("1.08322")], tape({3: ("1.08495", "1.08505", "1.08327", "1.08400")}))
    won = c_run([signal("1.08322")], tape({3: ("1.08495", "1.08871", "1.08485", "1.08800")}))
    untouched = c_run([signal("1.08322")], tape({3: ("1.08495", "1.08870", "1.08328", "1.08495")}), last=10)

    assert (stopped.trades[0].result.reason, stopped.trades[0].result.exit_price, stopped.trades[0].result.net_pnl, stopped.trades[0].result.r_multiple) == (
        ExitReason.STOP, D("1.08320"), D("-49.14"), D("-1.0000"))
    assert (won.trades[0].result.reason, won.trades[0].result.net_pnl, won.trades[0].result.r_multiple) == (ExitReason.TARGET, D("98.28"), D("2.0000"))
    assert untouched.trades[0].status.value == "ABIERTA_AL_FINAL"  # one tick short of both
    assert record(won, commit="x", dataset="y", dataset_description="z", executed_at=datetime(2026, 11, 12, tzinfo=timezone.utc))["reproducible"][
        "execution_parameters"]["min_net_reward_risk"] == "2"


def test_the_same_signals_under_another_strategy_id_keep_the_three_r_target() -> None:
    """The engine takes the 2 R from the strategy's identity, not from the signal."""
    bars = tape()
    as_c = c_run([signal("1.08322")], bars, last=5)
    as_other = play([signal("1.08322")], bars, last=5)  # strategy "prueba@0.0.0"

    assert as_c.trades[0].proposal.target == D("1.08866") and as_other.trades[0].proposal.target == D("1.09048")  # 3 x 0.00182 = 0.00546
    assert as_other.trades[0].proposal.stop == D("1.08322")  # the structural stop is the signal's in both


def test_a_rejected_pullback_is_recorded_with_its_reason_and_opens_nothing() -> None:
    result = c_run([signal("1.08100"), signal("1.08322", offset=2)], tape(), last=5)

    assert [d.proposal.rejections for d in result.decisions] == [(Rejection.STOP_TOO_FAR,), ()]
    assert [t.entry_at for t in result.trades] == [T0 + 2 * HOUR]


def test_session_rules_and_one_position_per_pair_apply_to_c_unchanged() -> None:
    friday_noon = datetime(2026, 12, 4, 17, 0, tzinfo=timezone.utc)  # 12:00 in New York
    blocked = c_run([Structured("EURUSD", Outcome.LONG, friday_noon, MID, D("0.00100"), structural_stop=StructuralStop(D("1.08322"), D("1"), D("3")),
                                strategy_id=ema_pullback.STRATEGY_ID)], tape(), last=80)
    twice = c_run([signal("1.08322"), signal("1.08322", offset=1)], tape(), last=5)

    assert blocked.decisions[0].proposal.rejections == (Rejection.BLOCKED_FRIDAY,)
    assert [d.proposal.rejections for d in twice.decisions] == [(), (Rejection.POSITION_OPEN,)]


def test_strategy_c_runs_end_to_end_on_its_own_signals_without_reading_ahead() -> None:
    """Real evaluations of a synthetic series: the signal at the candle that
    ends the pullback, the entry at the next bar's open, the stop from the
    pullback's low. With this series' tiny ATR the stop is beyond the cap."""
    bars = series(VALID + [(2, 1, 1)] * 6)
    at = bars[-7].timestamp + HOUR  # the close of the confirming candle
    options = dict(start=at - 3 * HOUR, data_kind=QuoteKind.SYNTHETIC, data_source=SOURCE, calendar=always_open)
    result = run(STRATEGY_C, {"EURUSD": bars}, end=bars[-1].timestamp, **options)  # type: ignore[arg-type]
    shorter = run(STRATEGY_C, {"EURUSD": bars[:-3]}, end=bars[-4].timestamp, **options)  # type: ignore[arg-type]

    assert (result.strategy_id, result.parameters_sha256) == (ema_pullback.STRATEGY_ID, ema_pullback.fingerprint())
    assert [(d.at, d.direction) for d in result.decisions] == [(at, "LONG")] and result.coverage["EURUSD"]["LONG"] == 1
    decision = result.decisions[0]
    assert (decision.proposal.bid + decision.proposal.ask) / 2 == bars[-6].open  # the bar after the confirming candle
    own = ema_pullback.evaluate(bars[:-6], at, symbol="EURUSD", data_kind=QuoteKind.SYNTHETIC, data_source=SOURCE, is_open=always_open)
    assert decision.proposal.stop == own.structural_stop.reference.quantize(D("0.00001")) and decision.proposal.rejections == (Rejection.STOP_TOO_FAR,)
    assert shorter.decisions == tuple(d for d in result.decisions if d.at <= bars[-4].timestamp)  # cutting the future changes nothing
