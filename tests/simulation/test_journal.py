"""Tests for simulation.journal: append-only signals and outcomes, JSONL round trip."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from investment_committee.simulation import (
    InMemoryTradeJournal,
    JsonLinesTradeJournal,
    TradeJournalError,
    TradeOutcome,
    TradeStatus,
)

from ._support import closing_time, sideways, signal_for, uptrend


def _outcome(signal_id: str, status: TradeStatus = TradeStatus.TAKE_PROFIT) -> TradeOutcome:
    return TradeOutcome(signal_id=signal_id, status=status, evaluated_at=closing_time(uptrend()) + timedelta(days=1), bars_observed=3)


def test_jsonl_round_trip_preserves_signals_and_outcomes(tmp_path: Path) -> None:
    path = tmp_path / "trades.jsonl"
    trade = signal_for(uptrend())
    no_trade = replace(signal_for(sideways()), signal_id="sig-2")
    journal = JsonLinesTradeJournal(path)
    journal.append_signal(trade)
    journal.append_signal(no_trade)
    journal.append_outcome(_outcome(trade.signal_id))

    reloaded = JsonLinesTradeJournal(path)

    assert reloaded.signals() == (trade, no_trade)
    assert reloaded.final_outcomes() == {trade.signal_id: _outcome(trade.signal_id)}
    assert [t.outcome for t in reloaded.trades()] == [_outcome(trade.signal_id), None]


def test_signals_are_never_overwritten() -> None:
    journal = InMemoryTradeJournal()
    journal.append_signal(signal_for(uptrend()))

    with pytest.raises(TradeJournalError):
        journal.append_signal(signal_for(uptrend()))


@pytest.mark.parametrize(
    "bad",
    [
        lambda sid: _outcome(sid, TradeStatus.OPEN),
        lambda sid: _outcome("unknown"),
    ],
)
def test_invalid_outcomes_are_rejected(bad) -> None:  # type: ignore[no-untyped-def]
    journal = InMemoryTradeJournal()
    signal = signal_for(uptrend())
    journal.append_signal(signal)

    with pytest.raises(TradeJournalError):
        journal.append_outcome(bad(signal.signal_id))


def test_a_final_outcome_is_recorded_once() -> None:
    journal = InMemoryTradeJournal()
    signal = signal_for(uptrend())
    journal.append_signal(signal)
    journal.append_outcome(_outcome(signal.signal_id))

    with pytest.raises(TradeJournalError):
        journal.append_outcome(_outcome(signal.signal_id, TradeStatus.STOP_LOSS))


def test_corrupt_journal_raises(tmp_path: Path) -> None:
    path = tmp_path / "trades.jsonl"
    path.write_text("{not json\n", encoding="utf-8")

    with pytest.raises(TradeJournalError):
        JsonLinesTradeJournal(path)


def test_journal_written_before_engine_2_still_loads(tmp_path: Path) -> None:
    """Entries recorded by v1 lack the newer account and outcome fields;
    they load with the documented defaults."""
    import json

    from investment_committee.simulation.journal import _encode

    signal = signal_for(uptrend())
    encoded_signal = _encode(signal)
    for newer in ("min_units", "margin_reserve_fraction", "stop_out_level", "max_drawdown_halt"):
        del encoded_signal["account"][newer]
    del encoded_signal["parameters"]["max_entry_delay"]
    encoded_outcome = _encode(_outcome(signal.signal_id))
    for newer in ("entry_time", "entry_price", "stop_loss", "take_profit", "units"):
        del encoded_outcome[newer]
    path = tmp_path / "v1.jsonl"
    path.write_text(
        json.dumps({"type": "signal", "data": encoded_signal}) + "\n" + json.dumps({"type": "outcome", "data": encoded_outcome}) + "\n",
        encoding="utf-8",
    )

    journal = JsonLinesTradeJournal(path)

    assert journal.signals() == (signal,)
    assert journal.final_outcomes()[signal.signal_id].entry_price is None
