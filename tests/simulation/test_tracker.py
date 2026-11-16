"""Opportunity Tracker (simulation.tracker + application service) on
synthetic bars. No network."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from investment_committee.application import OpportunityTrackerService
from investment_committee.domain import PriceBar
from investment_committee.market_intelligence import MarketIntelligenceService
from investment_committee.simulation import TradeDirection
from investment_committee.simulation.tracker import (
    InMemoryTrackerLedger,
    JsonLinesTrackerLedger,
    TrackedCandidate,
    TrackStatus,
    candidates_from_scan,
    evaluate,
)

from tests.application.test_opportunities import DEFAULT_DATA, FakeMarket, scanner

HOUR = timedelta(hours=1)
UTC = timezone.utc
DETECTED = datetime(2026, 10, 13, 12, 19, tzinfo=UTC)  # Tuesday, 08:19 New York: entries allowed


def candidate(direction: TradeDirection = TradeDirection.LONG, detected: datetime = DETECTED, **changes) -> TrackedCandidate:  # type: ignore[no-untyped-def]
    base = TrackedCandidate(
        candidate_id="scan-1:EURUSD=X", scan_id="scan-1", symbol="EURUSD=X", display_name="EUR/USD", direction=direction,
        detected_at=detected, signal_data_as_of=detected.replace(minute=0), reference_entry=Decimal("1.10010"),
        reference_stop=Decimal("1.09860"), reference_target=Decimal("1.10310"), atr=Decimal("0.0010"), units=30000,
        pip_size=Decimal("0.0001"), price_places=5, quote_currency="USD", base_currency="EUR", spread_pips=Decimal("1.0"),
        slippage_pips=Decimal("0.2"), stop_atr_multiple=Decimal("1.5"), target_atr_multiple=Decimal("3.0"),
        strategy_name="tendencia-momentum-volatilidad", strategy_version="1.0.0", rank=1, category="CANDIDATA_EXPLORATORIA",
    )
    return replace(base, **changes)


def bar(at: datetime, open_: str, high: str, low: str, close: str) -> PriceBar:
    return PriceBar(at, Decimal(open_), Decimal(high), Decimal(low), Decimal(close), 0)


def quiet(start: datetime, count: int, price: str = "1.10000") -> list[PriceBar]:
    return [bar(start + i * HOUR, price, str(Decimal(price) + Decimal("0.0002")), str(Decimal(price) - Decimal("0.0002")), price) for i in range(count)]


def test_entry_is_the_first_open_after_the_detection_never_earlier() -> None:
    earlier = bar(datetime(2026, 10, 13, 12, tzinfo=UTC), "1.09000", "1.09100", "1.08900", "1.09050")  # opened before 12:19
    bars = [earlier, *quiet(datetime(2026, 10, 13, 13, tzinfo=UTC), 3)]

    result = evaluate(candidate(), bars, now=datetime(2026, 10, 13, 16, tzinfo=UTC))

    assert result.entry_time == datetime(2026, 10, 13, 13, tzinfo=UTC)
    assert result.entry_price == Decimal("1.10007")  # 1.10000 + 0.5 pip spread + 0.2 pip slippage
    assert result.status is TrackStatus.ACTIVE


def test_pending_until_the_first_later_bar_has_closed() -> None:
    bars = quiet(datetime(2026, 10, 13, 13, tzinfo=UTC), 1)

    assert evaluate(candidate(), bars, now=datetime(2026, 10, 13, 13, 59, tzinfo=UTC)).status is TrackStatus.PENDING
    assert evaluate(candidate(), bars, now=datetime(2026, 10, 13, 14, tzinfo=UTC)).status is TrackStatus.ACTIVE


def test_target_and_stop_and_same_bar_conflict() -> None:
    start = datetime(2026, 10, 13, 13, tzinfo=UTC)
    rally = [*quiet(start, 1), bar(start + HOUR, "1.10000", "1.10500", "1.09990", "1.10400")]
    drop = [*quiet(start, 1), bar(start + HOUR, "1.10000", "1.10010", "1.09500", "1.09600")]
    both = [*quiet(start, 1), bar(start + HOUR, "1.10000", "1.10600", "1.09400", "1.10000")]
    now = start + 3 * HOUR

    assert evaluate(candidate(), rally, now).status is TrackStatus.TARGET
    stopped = evaluate(candidate(), drop, now)
    assert stopped.status is TrackStatus.STOP and stopped.net_pnl_usd is not None and stopped.net_pnl_usd < 0
    conflict = evaluate(candidate(), both, now)
    assert conflict.status is TrackStatus.STOP and conflict.same_bar_conflict


def test_short_profits_when_the_price_falls() -> None:
    start = datetime(2026, 10, 13, 13, tzinfo=UTC)
    drop = [*quiet(start, 1), bar(start + HOUR, "1.10000", "1.10010", "1.09500", "1.09600")]

    result = evaluate(candidate(TradeDirection.SHORT), drop, start + 3 * HOUR)

    assert result.status is TrackStatus.TARGET and result.net_pnl_usd is not None and result.net_pnl_usd > 0


def test_session_close_at_16_00_new_york() -> None:
    bars = quiet(datetime(2026, 10, 13, 13, tzinfo=UTC), 12)

    result = evaluate(candidate(), bars, now=datetime(2026, 10, 14, 1, tzinfo=UTC))

    assert result.status is TrackStatus.SESSION_CLOSE and result.exit_time == datetime(2026, 10, 13, 20, tzinfo=UTC)  # 16:00 EDT


def test_expired_when_no_executable_bar_arrives_in_time() -> None:
    late = quiet(datetime(2026, 10, 13, 16, tzinfo=UTC), 2)  # first bar 3h41 after detection

    assert evaluate(candidate(), late, now=datetime(2026, 10, 13, 19, tzinfo=UTC)).status is TrackStatus.EXPIRED
    assert evaluate(candidate(), [], now=datetime(2026, 10, 13, 16, tzinfo=UTC)).status is TrackStatus.EXPIRED


def test_detection_too_late_in_the_session_expires() -> None:
    late = candidate(detected=datetime(2026, 10, 13, 19, 10, tzinfo=UTC))  # 15:10 New York

    assert evaluate(late, quiet(datetime(2026, 10, 13, 20, tzinfo=UTC), 2), now=datetime(2026, 10, 13, 23, tzinfo=UTC)).status is TrackStatus.EXPIRED


def test_cross_pair_is_not_evaluable() -> None:
    cross = candidate(symbol="EURGBP=X", quote_currency="GBP", base_currency="EUR")

    assert evaluate(cross, quiet(datetime(2026, 10, 13, 13, tzinfo=UTC), 3), datetime(2026, 10, 13, 17, tzinfo=UTC)).status is TrackStatus.NOT_EVALUABLE


def test_usd_jpy_result_is_converted_to_usd() -> None:
    jpy = candidate(symbol="USDJPY=X", display_name="USD/JPY", quote_currency="JPY", base_currency="USD", pip_size=Decimal("0.01"),
                    price_places=3, atr=Decimal("0.10"), units=50000)
    start = datetime(2026, 10, 13, 13, tzinfo=UTC)
    rally = [bar(start, "150.000", "150.020", "149.980", "150.000"), bar(start + HOUR, "150.000", "150.500", "149.990", "150.400")]

    result = evaluate(jpy, rally, start + 3 * HOUR)

    assert result.status is TrackStatus.TARGET and result.exit_price is not None and result.net_pnl_usd is not None
    assert result.net_pnl_usd == ((result.exit_price - result.entry_price) * 50000 / result.exit_price).quantize(Decimal("0.01"))  # type: ignore[operator]


# --- ledger and service ---


def test_scan_candidates_are_observed_once_and_statuses_are_append_only(tmp_path: Path) -> None:
    ledger = JsonLinesTrackerLedger(tmp_path / "tracker.jsonl")
    service, _ = scanner(tracker=ledger)
    scan = service.scan()

    assert len(ledger.events()) == len(scan.ranked) > 0
    assert ledger.observe(candidates_from_scan(scan), scan.started_at) == 0  # never duplicated
    observed = [e for e in ledger.events() if e["event"] == "OBSERVED"]
    first = observed[0]["candidate"]
    assert first["detected_at"] == scan.started_at.isoformat() and first["strategy_version"] == "1.0.0"

    market = FakeMarket(DEFAULT_DATA)  # history up to the scan moment, nothing after it yet
    times = iter([scan.started_at + timedelta(minutes=30), scan.started_at + timedelta(minutes=40)])
    tracker = OpportunityTrackerService(MarketIntelligenceService(market), ledger, clock=lambda: next(times))
    view = tracker.update()
    assert view.updated == len(scan.ranked) and all(e.status is TrackStatus.PENDING for _, e in view.entries if e)
    again = tracker.update()  # same status: no new event
    assert again.updated == 0
    lines = (tmp_path / "tracker.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 * len(scan.ranked)
    assert [e for e in ledger.events() if e["event"] == "OBSERVED"] == observed  # originals untouched


def test_final_candidates_are_not_refetched() -> None:
    ledger = InMemoryTrackerLedger()
    c = candidate()
    ledger.observe([c], DETECTED)
    start = datetime(2026, 10, 13, 13, tzinfo=UTC)
    market = FakeMarket({"EURUSD=X": [*quiet(start, 1), bar(start + HOUR, "1.10000", "1.10500", "1.09990", "1.10400")]})
    tracker = OpportunityTrackerService(MarketIntelligenceService(market), ledger, clock=lambda: start + 3 * HOUR)

    first = tracker.update()
    second = tracker.update()

    assert first.entries[0][1].status is TrackStatus.TARGET  # type: ignore[union-attr]
    assert second.updated == 0 and len(market.requests) == 1


def test_bars_before_detection_cannot_change_the_result() -> None:
    start = datetime(2026, 10, 13, 13, tzinfo=UTC)
    after = [*quiet(start, 1), bar(start + HOUR, "1.10000", "1.10500", "1.09990", "1.10400")]
    noisy_past = [bar(start - k * HOUR, "1.2", "1.3", "1.0", "1.1") for k in range(1, 5)]

    assert evaluate(candidate(), after, start + 3 * HOUR) == evaluate(candidate(), [*noisy_past, *after], start + 3 * HOUR)
