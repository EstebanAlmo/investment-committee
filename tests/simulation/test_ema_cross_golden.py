"""Golden tests: ema-rsi-atr-forex@0.1.0 (strategy A) must not change.

Written BEFORE the code of A was touched to make room for a second strategy
(the shared data gates, the common signal contract of build_proposal). Every
expectation below was produced by commit 8282908 and is a literal: an
identifier, a SHA-256 or a number. If one of them moves, A's observable
behaviour moved - that is a new version of A, never a silent change.

The digests are SHA-256 of the `repr` of the frozen dataclasses (Decimals,
UTC datetimes, enums): the same text on Windows and macOS.

Bars are synthetic, except in the last test, which reads the Yahoo snapshot
already on disk (prices ESTIMADA, git-ignored; the test is skipped where the
snapshot is absent). Nothing is downloaded.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Sequence

import pytest

from investment_committee.domain import PriceBar
from investment_committee.market_intelligence.quotes import QuoteKind, estimated_from_mid, to_utc
from investment_committee.market_intelligence.snapshots import read_snapshot
from investment_committee.simulation.ema_cross import STRATEGY_ID, EmaCrossParameters, Outcome, SignalEvaluation, evaluate, fingerprint
from investment_committee.simulation.opportunities import AccountState, RiskLimits
from investment_committee.simulation.sessions import is_week_open
from investment_committee.simulation.strategies import all_strategies, parameters_fingerprint, strategy_parameters
from investment_committee.simulation.trade_proposal import (
    EURUSD_RULES,
    GBPUSD_RULES,
    ExecutionParameters,
    ExitReason,
    FinancingAssumption,
    build_proposal,
    indeterminate,
    settle,
)

from ._ema_cross_support import HOUR, SOURCE, START, always_open, walk

D = Decimal
SNAPSHOT = Path(__file__).resolve().parents[2] / "data" / "raw" / "snapshots" / "fx_1h_20260331_20260930"
ILLUSTRATIVE = FinancingAssumption(D("0.03"), "escenario ilustrativo, no es una tasa real")


def digest(items: Iterable[object]) -> str:
    return hashlib.sha256("\n".join(repr(item) for item in items).encode("utf-8")).hexdigest()


def run(bars: Sequence[PriceBar], as_of: datetime, **kwargs) -> SignalEvaluation:  # type: ignore[no-untyped-def]
    kwargs.setdefault("is_open", always_open)
    return evaluate(bars, as_of, symbol="EURUSD", data_kind=QuoteKind.SYNTHETIC, data_source=SOURCE, **kwargs)


def synthetic_evaluations() -> list[SignalEvaluation]:
    """Every hourly evaluation of a 700-bar walk, from the first full window on."""
    bars = walk(700)
    return [run(bars, bars[index].timestamp + HOUR) for index in range(299, 700)]


def rejection_cases() -> list[SignalEvaluation]:
    bars = walk(400)
    last = bars[-1].timestamp + HOUR
    contradictory = PriceBar(bars[350].timestamp, bars[350].open, bars[350].high + D("0.00010"), bars[350].low, bars[350].close, 0)
    saturday = datetime(2026, 1, 10, 12, 0, tzinfo=START.tzinfo)
    return [
        run(bars[:200], bars[199].timestamp + HOUR),  # too little history
        run(bars[:-1], last),  # the candle that just closed has no bar
        run(bars[:380] + bars[381:], last),  # a hole among the last 50
        run(bars[:100] + bars[104:], last),  # four old holes: one too many
        run(bars[:100] + bars[103:], last),  # three old holes: tolerated
        run(bars + [contradictory], last),  # two different bars for one instant
        run(bars, saturday, is_open=is_week_open),  # closed market
        run(bars, last + timedelta(minutes=37)),  # the same candle, seen later in the hour
    ]


def proposals_and_results() -> tuple[list[object], Counter[str]]:
    """Every signal of the walk taken to a proposal on an ESTIMATED quote
    (with no financing rate and with the illustrative one), and every
    accepted proposal settled in each way."""
    items: list[object] = []
    counts: Counter[str] = Counter()
    for evaluation in synthetic_evaluations():
        if not evaluation.is_signal:
            continue
        assert evaluation.last_close is not None
        quote = estimated_from_mid("EURUSD", evaluation.candle_close + timedelta(seconds=20), evaluation.last_close, EURUSD_RULES.assumed_spread, SOURCE)
        for financing in (None, ILLUSTRATIVE):
            proposal = build_proposal(evaluation, quote, quote.timestamp + timedelta(seconds=3), account=AccountState.fresh(D("10000")), financing=financing)
            items.append(proposal)
            counts[proposal.status.value] += 1
            counts.update(r.value for r in proposal.rejections)
            if not proposal.accepted:
                continue
            assert proposal.stop is not None and proposal.entry is not None
            later = quote.timestamp + 30 * HOUR
            beyond = proposal.stop - proposal.sign * D("0.00150")
            items += [
                settle(proposal, ExitReason.TARGET, later, financing=financing),
                settle(proposal, ExitReason.STOP, later, financing=financing),
                settle(proposal, ExitReason.STOP, later, market_price=beyond, financing=financing),
                settle(proposal, ExitReason.TIME, later, market_price=proposal.entry, financing=financing),
                settle(proposal, ExitReason.WEEKLY_CLOSE, later, market_price=proposal.entry + D("0.00040"), financing=financing),
                indeterminate(proposal, later, "orden de stop y objetivo desconocido", financing=financing),
            ]
    return items, counts


# --- identity ---


def test_identifier_and_parameter_fingerprint_are_literal() -> None:
    assert STRATEGY_ID == "ema-rsi-atr-forex@0.1.0"
    assert fingerprint() == "3c66ab0254d0e0b9d96bd5438ddec6335f8f0b40f354b4b2cc3d60f2102f1ed7"
    assert repr(EmaCrossParameters()) == (
        "EmaCrossParameters(timeframe=datetime.timedelta(seconds=3600), ema_fast=20, ema_slow=50, rsi_period=14, atr_period=14, "
        "rsi_threshold=Decimal('50'), window_bars=300, max_missing_in_window=3, contiguous_tail=50)"
    )


def test_execution_and_instrument_assumptions_are_literal() -> None:
    assert parameters_fingerprint(ExecutionParameters()) == "0f71f8bddac0184d14b5c76718e75faa33265bf18c9fa67d9a6357f6fe4f19d2"
    assert parameters_fingerprint(RiskLimits()) == "ae2b899f4355ebb9d6a13365c5723db6a4505f39c46a06762f449cfac01fa523"
    assert digest([EURUSD_RULES, GBPUSD_RULES]) == "39dcfce636c2ba70e14fadaa96855bcd966dde851324d590a8734b07b7c32268"


# --- signals and indicator values ---


def test_every_evaluation_of_the_synthetic_walk_is_unchanged() -> None:
    evaluations = synthetic_evaluations()

    assert Counter(e.outcome.value for e in evaluations) == {"SIN_CRUCE": 395, "SHORT": 3, "LONG": 3}
    assert digest(evaluations) == "391fa9645d162689557aee3a098ad65dfa0dae0abefb146c040731358fad062d"


def test_the_first_long_and_the_first_short_keep_their_numbers() -> None:
    evaluations = synthetic_evaluations()
    long_ = next(e for e in evaluations if e.outcome is Outcome.LONG)
    short = next(e for e in evaluations if e.outcome is Outcome.SHORT)

    assert [str(v) for v in (long_.candle_close, long_.ema_fast, long_.ema_slow, long_.previous_ema_fast, long_.previous_ema_slow, long_.rsi, long_.atr)] == ["2026-01-19 18:00:00+00:00", "1.100762211153686771210323534", "1.100755225917839976720641925", "1.100749812327759062916673380", "1.100750133098159975770464044", "58.03344297106587028310211029", "0.0001404528070696177425964291711"]
    assert [str(v) for v in (short.candle_close, short.ema_fast, short.ema_slow, short.previous_ema_fast, short.previous_ema_slow, short.rsi, short.atr)] == ["2026-01-17 19:00:00+00:00", "1.100838551956034294499466272", "1.100843480377383582971889278", "1.100864715319827378130989038", "1.100853826515235974113599044", "34.20982247491456969897222992", "0.0001371959645966843823182608769"]
    assert long_.signal_id == "ema-rsi-atr-forex@0.1.0|EURUSD|2026-01-19T18:00:00+00:00" and short.signal_id == "ema-rsi-atr-forex@0.1.0|EURUSD|2026-01-17T19:00:00+00:00"


def test_rejections_are_unchanged() -> None:
    cases = rejection_cases()

    assert [(e.outcome.value, [r.value for r in e.reasons]) for e in cases] == [("DATOS_RECHAZADOS", ["DATOS_INSUFICIENTES"]), ("DATOS_RECHAZADOS", ["DATOS_DESACTUALIZADOS"]), ("DATOS_RECHAZADOS", ["DATOS_INCOMPLETOS"]), ("DATOS_RECHAZADOS", ["DATOS_INCOMPLETOS"]), ("SIN_CRUCE", ["SIN_CRUCE"]), ("DATOS_RECHAZADOS", ["DATOS_INCONSISTENTES"]), ("DATOS_RECHAZADOS", ["MERCADO_CERRADO"]), ("SIN_CRUCE", ["SIN_CRUCE"])]
    assert digest(cases) == "79d98181efdee2b716f67e9291b95e3ae0fd8c438b53c4f68033bd7b2dd4cc23"


# --- proposals and settlement ---


def test_proposals_and_settlements_of_the_synthetic_signals_are_unchanged() -> None:
    items, counts = proposals_and_results()

    assert counts == {"RECHAZADA": 8, "MERCADO_CERRADO": 2, "MARGEN_INSUFICIENTE": 4, "ACEPTADA": 4, "ENTRADA_BLOQUEADA_ROLLOVER": 2}
    assert digest(items) == "8f5fb8a9faf4f35f823b6a415ebd0012c5b68a9344ad758909f757553def7dc8"


def test_the_first_accepted_proposal_keeps_its_numbers() -> None:
    items, _ = proposals_and_results()
    proposal = next(i for i in items if getattr(i, "accepted", False))
    index = items.index(proposal)
    target, stop, gap = items[index + 1], items[index + 2], items[index + 3]

    assert [str(v) for v in (proposal.proposal_id, proposal.direction, proposal.entry, proposal.stop, proposal.target, proposal.units,  # type: ignore[attr-defined]
                             proposal.estimated_loss_at_stop, proposal.expected_net_profit, proposal.margin_required)] == ["ema-rsi-atr-forex@0.1.0|EURUSD|2026-01-19T18:00:00+00:00", "LONG", "1.10095", "1.10074", "1.10348", "72000", "49.59", "149.13", "2642.28"]  # type: ignore[attr-defined]
    assert [str(r.net_pnl) for r in (target, stop, gap)] == ["168.95", "-29.77", "-137.77"]  # type: ignore[attr-defined]


# --- the earlier protocol ---


def test_the_frozen_strategies_are_unchanged_and_do_not_include_this_one() -> None:
    frozen = [(s.name, s.version, parameters_fingerprint(strategy_parameters(s))) for s in all_strategies()]

    assert [f"{name}@{version}" for name, version, _ in frozen] == ["tendencia-momentum-volatilidad@1.0.0", "tendencia-confirmada-4h@0.1.0", "ruptura-rango-londres@0.1.0", "reversion-media-lateral@0.1.0"]
    assert digest(frozen) == "903485e24f5cc72cdd09d8410deb1155100463d6dcf9175da8bc6dcb4432bebf"


# --- the historical diagnosis (Yahoo snapshot, ESTIMADA) ---


def _yahoo_week(moment: datetime) -> bool:
    return is_week_open(moment) and is_week_open(moment - 2 * HOUR)


@pytest.mark.skipif(not SNAPSHOT.exists(), reason="the Yahoo snapshot is git-ignored and not on this machine")
@pytest.mark.parametrize(
    "symbol, expected, signals_digest",
    [
        ("EURUSD=X", {"LONG": 22, "SHORT": 21, "SIN_CRUCE": 2509, "DATOS_INCOMPLETOS": 394, "DATOS_DESACTUALIZADOS": 10, "DATOS_INSUFICIENTES": 299,
                      "MERCADO_CERRADO": 1350}, "66d1fb12397c76e45b58e98c91b637a15f038509a37e09c9e0bc475509e8648c"),
        ("GBPUSD=X", {"LONG": 25, "SHORT": 23, "CRUCE_SIN_CONFIRMACION": 1, "SIN_CRUCE": 2503, "DATOS_INCOMPLETOS": 394, "DATOS_DESACTUALIZADOS": 10,
                      "DATOS_INSUFICIENTES": 299, "MERCADO_CERRADO": 1350}, "aa1b92bc84a78a5197a9105aa645f1004b8f0993d70c9ac28644d91f35e38514"),
    ],
)
def test_the_known_counts_of_the_historical_diagnosis_are_unchanged(symbol: str, expected: dict[str, int], signals_digest: str) -> None:
    """The counts are those of scripts/diagnose_ema_cross_signals.py with the
    calendar observed in Yahoo (docs/ema-rsi-atr-signals.md). The script
    hands every evaluation the whole history and takes minutes; here each
    evaluation gets the 340 bars before its moment, which is all it reads."""
    _, data = read_snapshot(SNAPSHOT)
    bars = sorted(data[symbol], key=lambda b: to_utc(b.timestamp))
    moments = [to_utc(b.timestamp) for b in bars]
    moment, end, upto = moments[0] + HOUR, moments[-1] + HOUR, 0
    counts: Counter[str] = Counter()
    crosses: list[tuple[object, ...]] = []
    while moment <= end:
        while upto < len(bars) and moments[upto] + HOUR <= moment:
            upto += 1
        e = evaluate(bars[max(0, upto - 340):upto], moment, symbol=symbol, data_kind=QuoteKind.ESTIMATED, data_source="Yahoo Finance H1", is_open=_yahoo_week)
        counts[e.reasons[0].value if e.outcome is Outcome.DATA_REJECTED else e.outcome.value] += 1
        if e.outcome in (Outcome.LONG, Outcome.SHORT, Outcome.NOT_CONFIRMED):
            crosses.append((e.evaluation_key, e.outcome, e.ema_fast, e.ema_slow, e.previous_ema_fast, e.previous_ema_slow, e.rsi, e.atr))
        moment += HOUR

    assert counts == expected
    assert digest(crosses) == signals_digest
