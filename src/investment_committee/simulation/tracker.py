"""Opportunity Tracker: prospective follow-up of the scanner's candidates.

A candidate is OBSERVED - never executed. Its hypothetical result says
what would have happened, from data that arrived AFTER it was detected:
  - entry: the OPEN of the first bar that opens at or after the detection
    moment (the scan time), with spread and slippage - a scan at 22:19
    enters at the 23:00 open, never at an earlier price; it must open within
    `max_entry_delay` and while ForexSessionRules allow an entry, else
    EXPIRADA;
  - exits as in the replay (simulation/execution.py unchanged): stop, target
    (both in one bar -> stop), flat at the session close (CIERRE_DE_SESIÓN);
  - only bars fully closed at evaluation time are used.
Statuses: PENDIENTE (no entry bar yet), ACTIVADA (entered, still running),
STOP, OBJETIVO, CIERRE_DE_SESIÓN, EXPIRADA, NO_EVALUABLE.

Audit: data/ledger/opportunity_tracker.jsonl, append-only events. An
OBSERVED event freezes the candidate (levels, costs, strategy version); a
STATUS event is appended only when the evaluated status changes. Nothing
is ever rewritten.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

from investment_committee.domain import PriceBar

from .execution import Position, check_exit, close_at_bar_close, close_at_bar_open, entry_fill, gross_pnl, levels_for, quantize_price
from .models import InstrumentSpec, StrategyParameters, TradeDirection, TradeStatus, TradingCosts
from .opportunities import Conversion, InstrumentScan, OpportunityScan, is_aligned
from .opportunities import DEFAULT_UNIVERSE as _UNIVERSE
from .sessions import ForexSessionRules

DEFAULT_TRACKER_PATH = Path("data") / "ledger" / "opportunity_tracker.jsonl"
_CENT = Decimal("0.01")


class TrackStatus(str, Enum):
    PENDING = "PENDIENTE"
    ACTIVE = "ACTIVADA"
    STOP = "STOP"
    TARGET = "OBJETIVO"
    SESSION_CLOSE = "CIERRE_DE_SESIÓN"
    EXPIRED = "EXPIRADA"
    NOT_EVALUABLE = "NO_EVALUABLE"

    @property
    def final(self) -> bool:
        return self not in (TrackStatus.PENDING, TrackStatus.ACTIVE)


@dataclass(frozen=True)
class TrackedCandidate:
    """Frozen at detection. Reference levels are the scanner's (at the
    signal bar's close); the hypothetical fill re-derives stop and target
    from the actual entry with the same ATR multiples."""

    candidate_id: str
    scan_id: str
    symbol: str
    display_name: str
    direction: TradeDirection
    detected_at: datetime  # the scan moment: nothing before it is executable
    signal_data_as_of: datetime
    reference_entry: Decimal
    reference_stop: Decimal
    reference_target: Decimal
    atr: Decimal
    units: int
    pip_size: Decimal
    price_places: int
    quote_currency: str
    base_currency: str
    spread_pips: Decimal
    slippage_pips: Decimal
    stop_atr_multiple: Decimal
    target_atr_multiple: Decimal
    strategy_name: str
    strategy_version: str
    rank: int | None
    category: str


@dataclass(frozen=True)
class Evaluation:
    status: TrackStatus
    evaluated_at: datetime
    bars_used_until: datetime | None  # close time of the last bar used
    entry_time: datetime | None = None
    entry_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    exit_time: datetime | None = None
    exit_price: Decimal | None = None
    net_pnl_usd: Decimal | None = None  # HYPOTHETICAL: never a realized result
    same_bar_conflict: bool = False
    detail: str = ""


def candidates_from_scan(scan: OpportunityScan) -> list[TrackedCandidate]:
    """Ranked candidates of a scan (exploratory or validated), frozen."""
    return [_freeze(scan, item) for item in scan.ranked]


def _freeze(scan: OpportunityScan, item: InstrumentScan) -> TrackedCandidate:
    signal = item.signal
    assert signal is not None and signal.plan is not None and signal.reading.atr is not None and signal.data_as_of is not None
    profile = next((p for p in _UNIVERSE if p.symbol == item.symbol), None)
    spec, costs = signal.instrument, signal.costs
    return TrackedCandidate(
        candidate_id=f"{scan.scan_id}:{item.symbol}",
        scan_id=scan.scan_id,
        symbol=item.symbol,
        display_name=item.display_name,
        direction=signal.direction,
        detected_at=scan.started_at,
        signal_data_as_of=signal.data_as_of,
        reference_entry=signal.plan.entry_price,
        reference_stop=signal.plan.stop_loss,
        reference_target=signal.plan.take_profit,
        atr=signal.reading.atr,
        units=signal.plan.units,
        pip_size=spec.pip_size,
        price_places=spec.price_places,
        quote_currency=spec.quote_currency,
        base_currency=profile.base_currency if profile else "?",
        spread_pips=costs.spread_pips,
        slippage_pips=costs.slippage_pips,
        stop_atr_multiple=signal.parameters.stop_atr_multiple,
        target_atr_multiple=signal.parameters.target_atr_multiple,
        strategy_name=signal.strategy_name,
        strategy_version=signal.strategy_version,
        rank=item.rank,
        category=item.category.value,
    )


def evaluate(
    candidate: TrackedCandidate,
    bars: Sequence[PriceBar],
    now: datetime,
    rules: ForexSessionRules | None = None,
    timeframe: timedelta = timedelta(hours=1),
    max_entry_delay: timedelta = timedelta(hours=2),
) -> Evaluation:
    rules = rules or ForexSessionRules()
    spec = InstrumentSpec(candidate.symbol, candidate.display_name, candidate.pip_size, candidate.quote_currency, candidate.price_places)
    costs = TradingCosts(spread_pips=candidate.spread_pips, slippage_pips=candidate.slippage_pips)
    parameters = StrategyParameters(stop_atr_multiple=candidate.stop_atr_multiple, target_atr_multiple=candidate.target_atr_multiple)
    if candidate.quote_currency == "USD":
        conversion: Conversion | None = Conversion.DIRECT
    elif candidate.base_currency == "USD":
        conversion = Conversion.INVERSE
    else:
        conversion = None
    if conversion is None:
        return Evaluation(TrackStatus.NOT_EVALUABLE, now, None, detail="sin conversión a USD para este par")

    usable = sorted(
        (b for b in bars if is_aligned(b.timestamp, timeframe) and b.timestamp >= candidate.detected_at and b.timestamp + timeframe <= now),
        key=lambda b: b.timestamp,
    )
    deadline = candidate.detected_at + max_entry_delay
    entry_bar = usable[0] if usable else None
    if entry_bar is None:
        if now >= deadline + timeframe:
            return Evaluation(TrackStatus.EXPIRED, now, None, detail="no hubo una vela ejecutable a tiempo después de la detección")
        return Evaluation(TrackStatus.PENDING, now, None, detail="todavía no cerró la primera vela posterior a la detección")
    if entry_bar.timestamp > deadline or not rules.may_enter(entry_bar.timestamp):
        return Evaluation(TrackStatus.EXPIRED, now, entry_bar.timestamp + timeframe, detail="la primera vela posterior llegó tarde o fuera del horario de entradas")

    entry = entry_fill(candidate.direction, entry_bar.open, costs, spec)
    stop, target = levels_for(candidate.direction, entry, candidate.atr, parameters, spec)
    flat_at = rules.flat_time(entry_bar.timestamp)
    position = Position(candidate.candidate_id, candidate.direction, entry_bar.timestamp, entry, stop, target, candidate.units, Decimal("0"), Decimal("0"), flat_at)
    base = dict(entry_time=entry_bar.timestamp.astimezone(timezone.utc), entry_price=entry, stop_loss=stop, take_profit=target)

    def finish(status: TrackStatus, price: Decimal, at: datetime, conflict: bool, until: datetime) -> Evaluation:
        price = quantize_price(price, spec)  # the same rounded fill the evaluation reports
        pnl_quote = gross_pnl(position, price)
        rate = Decimal("1") if conversion is Conversion.DIRECT else price
        return Evaluation(status, now, until, exit_time=at.astimezone(timezone.utc), exit_price=price,
                          net_pnl_usd=(pnl_quote / rate).quantize(_CENT, rounding=ROUND_HALF_EVEN),
                          same_bar_conflict=conflict, **base)  # type: ignore[arg-type]

    last_close = None
    for bar in usable:
        close_time = bar.timestamp + timeframe
        if bar.timestamp >= flat_at:  # flat time passed inside a data gap: first price after it
            event = close_at_bar_open(position, bar, costs, spec, TradeStatus.EXPIRED)
            return finish(TrackStatus.SESSION_CLOSE, event.price, event.time, False, close_time)
        last_close = close_time
        event = check_exit(position, bar, timeframe, costs, spec)
        if event is not None:
            status = TrackStatus.STOP if event.status is TradeStatus.STOP_LOSS else TrackStatus.TARGET
            return finish(status, event.price, event.time, event.same_bar_conflict, close_time)
        if close_time >= flat_at:
            event = close_at_bar_close(position, bar, timeframe, costs, spec, TradeStatus.EXPIRED)
            return finish(TrackStatus.SESSION_CLOSE, event.price, event.time, False, close_time)
    if now >= flat_at + 2 * timeframe:
        return Evaluation(TrackStatus.NOT_EVALUABLE, now, last_close, detail="faltan velas después del cierre de sesión", **base)  # type: ignore[arg-type]
    return Evaluation(TrackStatus.ACTIVE, now, last_close, detail="entró y sigue abierta (hipotética)", **base)  # type: ignore[arg-type]


# --- append-only event ledger ---


class TrackerLedgerError(Exception):
    pass


class TrackerLedger(ABC):
    @abstractmethod
    def append(self, event: dict[str, Any]) -> None: ...

    @abstractmethod
    def events(self) -> Sequence[dict[str, Any]]: ...

    def observe(self, candidates: Sequence[TrackedCandidate], at: datetime) -> int:
        known = {e["candidate_id"] for e in self.events() if e["event"] == "OBSERVED"}
        added = 0
        for candidate in candidates:
            if candidate.candidate_id in known:
                continue
            self.append({"event": "OBSERVED", "candidate_id": candidate.candidate_id, "recorded_at": at.isoformat(), "candidate": _encode(candidate)})
            added += 1
        return added

    def record(self, candidate_id: str, evaluation: Evaluation) -> None:
        self.append({"event": "STATUS", "candidate_id": candidate_id, "recorded_at": evaluation.evaluated_at.isoformat(), "evaluation": _encode(evaluation)})

    def state(self) -> list[tuple[TrackedCandidate, Evaluation | None]]:
        """Every observed candidate with its LATEST recorded evaluation."""
        candidates: dict[str, TrackedCandidate] = {}
        latest: dict[str, Evaluation] = {}
        for event in self.events():
            if event["event"] == "OBSERVED":
                candidates[event["candidate_id"]] = _decode_candidate(event["candidate"])
            elif event["event"] == "STATUS":
                latest[event["candidate_id"]] = _decode_evaluation(event["evaluation"])
        return [(c, latest.get(cid)) for cid, c in candidates.items()]


class InMemoryTrackerLedger(TrackerLedger):
    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    def append(self, event: dict[str, Any]) -> None:
        self._events.append(json.loads(json.dumps(event)))

    def events(self) -> Sequence[dict[str, Any]]:
        return tuple(self._events)


class JsonLinesTrackerLedger(TrackerLedger):
    def __init__(self, path: Path | str = DEFAULT_TRACKER_PATH) -> None:
        self._path = Path(path)

    def append(self, event: dict[str, Any]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as exc:
            raise TrackerLedgerError(f"cannot write tracker ledger {self._path}: {exc}") from exc

    def events(self) -> Sequence[dict[str, Any]]:
        if not self._path.exists():
            return ()
        try:
            return tuple(json.loads(line) for line in self._path.read_text(encoding="utf-8").splitlines() if line.strip())
        except (OSError, ValueError) as exc:
            raise TrackerLedgerError(f"cannot read tracker ledger {self._path}: {exc}") from exc


def _encode(value: Any) -> Any:
    if isinstance(value, (TrackedCandidate, Evaluation)):
        return {k: _encode(v) for k, v in value.__dict__.items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _decode_candidate(raw: dict[str, Any]) -> TrackedCandidate:
    decimals = {"reference_entry", "reference_stop", "reference_target", "atr", "pip_size", "spread_pips", "slippage_pips", "stop_atr_multiple", "target_atr_multiple"}
    times = {"detected_at", "signal_data_as_of"}
    values = {k: Decimal(v) if k in decimals else datetime.fromisoformat(v) if k in times else v for k, v in raw.items()}
    values["direction"] = TradeDirection(values["direction"])
    return TrackedCandidate(**values)


def _decode_evaluation(raw: dict[str, Any]) -> Evaluation:
    decimals = {"entry_price", "stop_loss", "take_profit", "exit_price", "net_pnl_usd"}
    times = {"evaluated_at", "bars_used_until", "entry_time", "exit_time"}
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if value is None:
            values[key] = None
        elif key in decimals:
            values[key] = Decimal(value)
        elif key in times:
            values[key] = datetime.fromisoformat(value)
        else:
            values[key] = value
    values["status"] = TrackStatus(values["status"])
    return Evaluation(**values)

