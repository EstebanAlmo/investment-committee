"""Tests for application.OpportunityScannerService - fake market data, fake
AI committee, in-memory or tmp-path ledgers. No network, no paid calls."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping, Sequence

import pytest

from investment_committee.application import (
    OpportunityScannerService,
    OpportunityScanRecordingError,
    ScannerConfig,
    account_state_from_journal,
)
from investment_committee.domain import Asset, AssetType, MarketSnapshot, PriceBar
from investment_committee.market_intelligence import MarketDataError, MarketIntelligenceService
from investment_committee.market_intelligence.providers.base import MarketDataProvider
from investment_committee.simulation import (
    EURUSD,
    EURUSD_PROFILE,
    AccountAssumptions,
    AIReviewSettings,
    Category,
    HistoricalValidation,
    InMemoryOpportunityScanLedger,
    InMemoryTradeJournal,
    JsonLinesOpportunityScanLedger,
    OpportunityLedgerError,
    OpportunityScan,
    ScanSettings,
    StrategyParameters,
    TradeOutcome,
    TradeStatus,
    TradingCosts,
    ValidationStatus,
    evaluate_signal,
)

from tests.simulation._support import HOUR, bars_from_moves, closing_time, downtrend, sideways, uptrend

NOW = closing_time(uptrend())


def jpy_bars(direction: int = 1) -> list[PriceBar]:
    big, small = Decimal("0.10") * direction, Decimal("-0.08") * direction
    return bars_from_moves([str(big) if i % 2 == 0 else str(small) for i in range(70)], first_close="150.000")


class FakeMarket(MarketDataProvider):
    def __init__(self, by_symbol: Mapping[str, Sequence[PriceBar] | Exception]) -> None:
        self.by_symbol = by_symbol
        self.requests: list[tuple[str, str, str]] = []

    def get_daily_history(self, ticker: str) -> MarketSnapshot:
        raise MarketDataError("not used")

    def get_intraday_history(self, ticker: str, interval: str, period: str) -> MarketSnapshot:
        self.requests.append((ticker, interval, period))
        value = self.by_symbol[ticker]
        if isinstance(value, Exception):
            raise value
        asset = Asset(ticker=ticker, name=None, asset_type=AssetType.STOCK, exchange=None, currency=None)
        return MarketSnapshot(asset=asset, generated_at=NOW, price_bars=list(value))


DEFAULT_DATA = {"EURUSD=X": uptrend(), "GBPUSD=X": downtrend(), "USDJPY=X": sideways()}


def scanner(data: Mapping[str, object] | None = None, *, ledger=None, config: ScannerConfig | None = None, **kwargs):  # type: ignore[no-untyped-def]
    market = FakeMarket(data or DEFAULT_DATA)  # type: ignore[arg-type]
    service = OpportunityScannerService(
        MarketIntelligenceService(market), ledger if ledger is not None else InMemoryOpportunityScanLedger(), config or ScannerConfig(),
        clock=lambda: NOW, id_factory=lambda: "scan-1", **kwargs,
    )
    return service, market


def test_scans_every_instrument_and_ranks_the_candidates() -> None:
    service, market = scanner()

    scan = service.scan()

    assert [r[0] for r in market.requests] == ["EURUSD=X", "GBPUSD=X", "USDJPY=X"]
    assert {r[1:] for r in market.requests} == {("1h", "30d")}
    categories = {s.symbol: s.category for s in scan.instruments}
    assert categories == {"EURUSD=X": Category.EXPLORATORY, "GBPUSD=X": Category.EXPLORATORY, "USDJPY=X": Category.NO_SIGNAL}
    assert [s.symbol for s in scan.ranked] == ["EURUSD=X", "GBPUSD=X"]
    assert scan.technical_signals == 2 and scan.simulated_trade_opened is False and scan.errors == ()


def test_missing_data_and_unexpected_errors_are_partial_errors() -> None:
    service, _ = scanner({"EURUSD=X": MarketDataError("no price history"), "GBPUSD=X": RuntimeError("boom"), "USDJPY=X": jpy_bars()})

    scan = service.scan()

    by_symbol = {s.symbol: s for s in scan.instruments}
    assert by_symbol["EURUSD=X"].category is Category.DATA_ERROR and "sin datos de mercado" in by_symbol["EURUSD=X"].error  # type: ignore[operator]
    assert by_symbol["GBPUSD=X"].category is Category.DATA_ERROR and "RuntimeError" in by_symbol["GBPUSD=X"].error  # type: ignore[operator]
    assert by_symbol["USDJPY=X"].rank == 1  # the rest of the scan went on
    assert len(scan.errors) == 2


def test_incomplete_history_is_reported_not_traded() -> None:
    service, _ = scanner({**DEFAULT_DATA, "EURUSD=X": uptrend(20)})

    scan = service.scan()

    eur = next(s for s in scan.instruments if s.symbol == "EURUSD=X")
    assert eur.category is Category.NO_SIGNAL and eur.signal.reasons[0].value == "INSUFFICIENT_DATA"  # type: ignore[union-attr]


def test_future_and_forming_bars_never_reach_the_signal() -> None:
    bars = uptrend()
    last = bars[-1]
    forming = PriceBar(last.timestamp + HOUR, last.close, last.close + Decimal("0.02"), last.close, last.close + Decimal("0.02"), 0)
    future = replace(forming, timestamp=last.timestamp + 4 * HOUR)
    clock = NOW + timedelta(minutes=30)
    service = OpportunityScannerService(
        MarketIntelligenceService(FakeMarket({**DEFAULT_DATA, "EURUSD=X": [*bars, forming, future]})),
        InMemoryOpportunityScanLedger(), clock=lambda: clock, id_factory=lambda: "scan-f",
    )

    scan = service.scan()

    eur = next(s for s in scan.instruments if s.symbol == "EURUSD=X")
    assert eur.data.future_bars_dropped == 1 and eur.data.forming_bars_excluded == 1  # type: ignore[union-attr]
    assert eur.signal.data_as_of == NOW <= scan.started_at  # type: ignore[union-attr]
    assert all(s.data.last_closed_bar_close <= scan.started_at for s in scan.instruments if s.data and s.data.last_closed_bar_close)


def test_time_budget_skips_the_remaining_instruments() -> None:
    ticks = iter([0.0, 0.0, 0.0, 100.0, 100.0])
    service, market = scanner(config=ScannerConfig(settings=ScanSettings(time_budget=timedelta(seconds=30))), monotonic=lambda: next(ticks))

    scan = service.scan()

    assert len(market.requests) == 2
    jpy = next(s for s in scan.instruments if s.symbol == "USDJPY=X")
    assert jpy.category is Category.DATA_ERROR and "tiempo máximo" in jpy.error  # type: ignore[operator]


def test_universe_size_is_limited_and_validated() -> None:
    with pytest.raises(ValueError, match="limit"):
        ScannerConfig(settings=ScanSettings(max_instruments=2))
    with pytest.raises(ValueError, match="repeated"):
        ScannerConfig(universe=(EURUSD_PROFILE, EURUSD_PROFILE))
    with pytest.raises(ValueError):
        ScanSettings(max_instruments=50)


def test_bars_per_instrument_are_capped() -> None:
    service, _ = scanner({**DEFAULT_DATA, "EURUSD=X": uptrend(200)}, config=ScannerConfig(settings=ScanSettings(max_bars_per_instrument=80)))

    eur = next(s for s in service.scan().instruments if s.symbol == "EURUSD=X")

    assert eur.data.bars_received == 80  # type: ignore[union-attr]


def test_validated_evidence_comes_only_from_configuration() -> None:
    evidence = {("tendencia-momentum-volatilidad", "1.0.0", "GBPUSD=X"): HistoricalValidation(ValidationStatus.VALIDADA, "fixture")}
    service, _ = scanner(config=ScannerConfig(validations=evidence))

    scan = service.scan()

    assert [(s.symbol, s.category) for s in scan.ranked] == [("GBPUSD=X", Category.VALIDATED), ("EURUSD=X", Category.EXPLORATORY)]


def test_zero_opportunities() -> None:
    service, _ = scanner({"EURUSD=X": sideways(), "GBPUSD=X": sideways(), "USDJPY=X": sideways()})

    scan = service.scan()

    assert scan.ranked == [] and scan.candidates == [] and scan.technical_signals == 0


# --- ledger ---


def test_every_scan_is_recorded_with_audit_fields(tmp_path: Path) -> None:
    ledger = JsonLinesOpportunityScanLedger(tmp_path / "opportunity_scans.jsonl")
    service, _ = scanner(ledger=ledger)

    scan = service.scan()

    (record,) = ledger.records()
    assert record["schema_version"] == 1 and record["scan_id"] == "scan-1"
    assert record["started_at"] == NOW.isoformat() and record["universe"] == ["EURUSD=X", "GBPUSD=X", "USDJPY=X"]
    assert record["strategy_name"] == "tendencia-momentum-volatilidad" and record["strategy_version"] == "1.0.0"
    assert record["simulated_trade_opened"] is False
    assert record["data_integrity"] == {"latest_bar_close_used": NOW.isoformat(), "no_bar_after_scan_start": True}
    eur = record["instruments"][0]
    assert eur["category"] == "CANDIDATA_EXPLORATORIA" and eur["validation"]["status"] == "NO_VALIDADA"
    assert eur["data"]["source"].startswith("Yahoo Finance") and eur["data"]["last_closed_bar_close"] == NOW.isoformat()
    assert eur["money"]["risk_amount"] and eur["signal"]["plan"]["entry_price"]
    assert [c["name"] for c in eur["checks"]][:1] == ["riesgo por operación"]
    assert eur["evaluation"] == {
        "status": "PENDIENTE", "hypothetical": True, "evaluate_only_with_bars_closing_after": NOW.isoformat(), "outcome": None,
    }
    assert record["instruments"][2]["evaluation"] is None  # no candidate, nothing to evaluate
    text = (tmp_path / "opportunity_scans.jsonl").read_text(encoding="utf-8")
    assert "sk-" not in text and "api_key" not in text.lower()
    assert len(scan.instruments) == 3


def test_a_ledger_failure_is_reported(tmp_path: Path) -> None:
    class Broken(InMemoryOpportunityScanLedger):
        def append(self, scan: OpportunityScan) -> None:
            raise OpportunityLedgerError("disk full")

    service, _ = scanner(ledger=Broken())

    with pytest.raises(OpportunityScanRecordingError):
        service.scan()


def test_the_scan_never_writes_the_trade_journal() -> None:
    journal = InMemoryTradeJournal()
    service, _ = scanner(account_provider=lambda now: account_state_from_journal(journal, Decimal("10000"), now))

    service.scan()

    assert journal.signals() == ()


# --- account from the journal ---


def _journal_trade(journal: InMemoryTradeJournal, bars: Sequence[PriceBar], signal_id: str):  # type: ignore[no-untyped-def]
    signal = evaluate_signal(
        bars, closing_time(bars), signal_id=signal_id, instrument=EURUSD, parameters=StrategyParameters(), costs=TradingCosts(),
        account=AccountAssumptions(),
    )
    journal.append_signal(signal)
    return signal


def test_open_journal_trades_count_as_open_risk() -> None:
    journal = InMemoryTradeJournal()
    signal = _journal_trade(journal, uptrend(), "j-1")

    account = account_state_from_journal(journal, Decimal("10000"), NOW)

    assert account.open_risk == signal.plan.risk_amount and account.open_positions[0].symbol == "EURUSD=X"  # type: ignore[union-attr]
    service, _ = scanner(account_provider=lambda now: account)
    eur = next(s for s in service.scan().instruments if s.symbol == "EURUSD=X")
    assert eur.category is Category.SIGNAL_REJECTED  # same instrument already open


def test_todays_realized_loss_reduces_the_daily_budget() -> None:
    journal = InMemoryTradeJournal()
    signal = _journal_trade(journal, uptrend(), "j-2")
    journal.append_outcome(TradeOutcome(signal_id="j-2", status=TradeStatus.STOP_LOSS, evaluated_at=NOW, bars_observed=2, exit_time=NOW - HOUR, pnl_amount=Decimal("-120")))

    account = account_state_from_journal(journal, Decimal("10000"), NOW)

    assert account.realized_pnl_today == Decimal("-120") and account.equity == Decimal("9880") and account.day_start_equity == Decimal("10000")
    assert account.open_positions == ()
    scan = scanner(account_provider=lambda now: account)[0].scan()
    assert len(scan.ranked) == 0  # 150 - 120 = 30 USD left: no ~49 USD candidate fits
    assert all("riesgo agregado" in s.notes[-1] for s in scan.discarded)


# --- optional AI review ---


class FakeAI:
    def __init__(self, conclusions: Mapping[str, str], available: bool = True) -> None:
        self.conclusions = conclusions
        self.available = available
        self.unavailable_reason = None if available else "falta configurar DEEPSEEK_API_KEY"
        self.calls: list[str] = []

    def analyze(self, symbol: str):  # type: ignore[no-untyped-def]
        self.calls.append(symbol)
        return SimpleNamespace(analysis_id=f"ai-{symbol}", decision=SimpleNamespace(conclusion=self.conclusions[symbol]))


def test_ai_is_off_by_default() -> None:
    ai = FakeAI({})
    service, _ = scanner(ai_committee=ai)

    scan = service.scan()

    assert ai.calls == [] and scan.ai_review_enabled is False and scan.ai_reviews_used == 0


def test_ai_reviews_only_ranked_candidates_within_the_limit_and_can_only_object() -> None:
    ai = FakeAI({"EURUSD=X": "NO_CONCLUYENTE", "GBPUSD=X": "DESFAVORABLE"})
    service, _ = scanner(ai_committee=ai)

    scan = service.scan(ai_reviews=1)

    assert ai.calls == ["EURUSD=X"]  # best-ranked only; USD/JPY had no candidate
    eur = next(s for s in scan.instruments if s.symbol == "EURUSD=X")
    assert eur.category is Category.SIGNAL_REJECTED and eur.ai_review.objected  # type: ignore[union-attr]
    assert [s.symbol for s in scan.ranked] == ["GBPUSD=X"] and scan.ranked[0].rank == 1
    assert scan.ai_reviews_used == 1


def test_ai_agreement_never_upgrades_a_candidate() -> None:
    ai = FakeAI({"EURUSD=X": "FAVORABLE", "GBPUSD=X": "DESFAVORABLE"})
    service, _ = scanner(ai_committee=ai)

    scan = service.scan(ai_reviews=3)

    assert ai.calls == ["EURUSD=X", "GBPUSD=X"]
    assert all(s.category is Category.EXPLORATORY and s.ai_review and not s.ai_review.objected for s in scan.ranked)


def test_unavailable_ai_is_reported_without_calls() -> None:
    ai = FakeAI({}, available=False)
    service, _ = scanner(ai_committee=ai)

    scan = service.scan(ai_reviews=2)

    assert ai.calls == [] and any("no disponible" in e for e in scan.errors)
    assert len(scan.ranked) == 2


def test_ai_review_count_is_capped() -> None:
    with pytest.raises(ValueError):
        AIReviewSettings(max_reviews=10)
    with pytest.raises(ValueError):
        scanner()[0].scan(ai_reviews=9)


def test_ledger_record_is_json_serialisable_in_memory() -> None:
    ledger = InMemoryOpportunityScanLedger()
    scanner(ledger=ledger)[0].scan()

    json.dumps(ledger.records()[0])


