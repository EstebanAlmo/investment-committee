"""OpportunityTrackerService: /seguimiento - prospective follow-up of the
candidates the scanner emitted.

update() fetches recent 1-hour bars only for instruments with candidates
still PENDIENTE or ACTIVADA, evaluates each one (simulation.tracker) with
bars closed after its detection, and appends a STATUS event only when the
status changed. Observed candidates are hypothetical: none is a simulated
trade, and their results are never counted as P&L. Nothing runs on a
schedule: update() runs when a person asks (/seguimiento or
scripts/update_tracker.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Sequence

from investment_committee.market_intelligence import MarketDataError, MarketIntelligenceService
from investment_committee.simulation.tracker import Evaluation, TrackedCandidate, TrackerLedger, TrackerLedgerError, evaluate

from .errors import ApplicationServiceError

_logger = logging.getLogger(__name__)


class TrackerUnavailableError(ApplicationServiceError):
    """The tracker ledger could not be read or written."""


@dataclass(frozen=True)
class TrackerView:
    entries: Sequence[tuple[TrackedCandidate, Evaluation | None]]  # most recent detection first
    updated: int  # status events appended by this refresh
    data_errors: Sequence[str]
    as_of: datetime


class OpportunityTrackerService:
    def __init__(
        self,
        market_intelligence_service: MarketIntelligenceService,
        ledger: TrackerLedger,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._market = market_intelligence_service
        self._ledger = ledger
        self._clock = clock

    def update(self) -> TrackerView:
        now = self._clock()
        try:
            state = self._ledger.state()
            open_items = [(c, e) for c, e in state if e is None or not e.status.final]
            bars_by_symbol, errors = {}, []
            for symbol in sorted({c.symbol for c, _ in open_items}):
                try:
                    bars_by_symbol[symbol] = self._market.get_intraday_history(symbol, interval="1h", period="7d").price_bars
                except MarketDataError as exc:
                    errors.append(f"{symbol}: sin datos ({type(exc).__name__})")
            updated = 0
            for candidate, latest in open_items:
                if candidate.symbol not in bars_by_symbol:
                    continue
                evaluation = evaluate(candidate, bars_by_symbol[candidate.symbol], now)
                if latest is None or evaluation.status is not latest.status:
                    self._ledger.record(candidate.candidate_id, evaluation)
                    updated += 1
            entries = sorted(self._ledger.state(), key=lambda ce: ce[0].detected_at, reverse=True)
        except TrackerLedgerError as exc:
            raise TrackerUnavailableError(str(exc)) from exc
        return TrackerView(entries=entries, updated=updated, data_errors=errors, as_of=now)


