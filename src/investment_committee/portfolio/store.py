"""Storage for the Portfolio Guardian (git-ignored, under data/ledger/):
  - portfolio_positions.jsonl: append-only OPENED / CLOSED events for
    manually registered positions (closing here only stops monitoring - it
    sends nothing anywhere);
  - portfolio_alert_state.json: the alerts' current states (dedupe,
    hysteresis, cooldown); rewritten as a whole on each check;
  - portfolio_alerts.jsonl: append-only log of every alert event.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from .guardian import AlertEvent, AlertState, AssetKind, PortfolioPosition, Side

DEFAULT_DIR = Path("data") / "ledger"


class PortfolioStoreError(Exception):
    pass


def _encode_position(p: PortfolioPosition) -> dict[str, Any]:
    return {
        "position_id": p.position_id, "instrument": p.instrument, "kind": p.kind.value, "side": p.side.value,
        "quantity": str(p.quantity), "entry_price": str(p.entry_price), "currency": p.currency, "opened_at": p.opened_at.isoformat(),
        "costs_usd": str(p.costs_usd), "stop": None if p.stop is None else str(p.stop), "multiplier": str(p.multiplier),
        "leverage": str(p.leverage), "source": p.source, "base_currency": p.base_currency,
    }


def _decode_position(raw: Mapping[str, Any]) -> PortfolioPosition:
    return PortfolioPosition(
        position_id=raw["position_id"], instrument=raw["instrument"], kind=AssetKind(raw["kind"]), side=Side(raw["side"]),
        quantity=Decimal(raw["quantity"]), entry_price=Decimal(raw["entry_price"]), currency=raw["currency"],
        opened_at=datetime.fromisoformat(raw["opened_at"]), costs_usd=Decimal(raw["costs_usd"]),
        stop=None if raw["stop"] is None else Decimal(raw["stop"]), multiplier=Decimal(raw["multiplier"]),
        leverage=Decimal(raw["leverage"]), source=raw["source"], base_currency=raw.get("base_currency"),
    )


class PositionStore(ABC):
    @abstractmethod
    def _append(self, event: dict[str, Any]) -> None: ...

    @abstractmethod
    def _events(self) -> Sequence[dict[str, Any]]: ...

    def add(self, position: PortfolioPosition, at: datetime) -> None:
        if any(p.position_id == position.position_id for p in self.open_positions()):
            raise PortfolioStoreError(f"position {position.position_id} is already registered")
        self._append({"event": "OPENED", "at": at.isoformat(), "position": _encode_position(position)})

    def close(self, position_id: str, at: datetime, note: str = "") -> None:
        if not any(p.position_id == position_id for p in self.open_positions()):
            raise PortfolioStoreError(f"no open position {position_id}")
        self._append({"event": "CLOSED", "at": at.isoformat(), "position_id": position_id, "note": note})

    def open_positions(self) -> list[PortfolioPosition]:
        positions: dict[str, PortfolioPosition] = {}
        for event in self._events():
            if event["event"] == "OPENED":
                position = _decode_position(event["position"])
                positions[position.position_id] = position
            elif event["event"] == "CLOSED":
                positions.pop(event["position_id"], None)
        return list(positions.values())


class InMemoryPositionStore(PositionStore):
    def __init__(self) -> None:
        self._log: list[dict[str, Any]] = []

    def _append(self, event: dict[str, Any]) -> None:
        self._log.append(event)

    def _events(self) -> Sequence[dict[str, Any]]:
        return tuple(self._log)


class JsonLinesPositionStore(PositionStore):
    def __init__(self, path: Path | str = DEFAULT_DIR / "portfolio_positions.jsonl") -> None:
        self._path = Path(path)

    def _append(self, event: dict[str, Any]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as exc:
            raise PortfolioStoreError(str(exc)) from exc

    def _events(self) -> Sequence[dict[str, Any]]:
        if not self._path.exists():
            return ()
        try:
            return tuple(json.loads(line) for line in self._path.read_text(encoding="utf-8").splitlines() if line.strip())
        except (OSError, ValueError) as exc:
            raise PortfolioStoreError(str(exc)) from exc


class AlertStore(ABC):
    @abstractmethod
    def load(self) -> dict[str, AlertState]: ...

    @abstractmethod
    def save(self, states: Mapping[str, AlertState], events: Sequence[AlertEvent]) -> None: ...


class InMemoryAlertStore(AlertStore):
    def __init__(self) -> None:
        self.states: dict[str, AlertState] = {}
        self.log: list[AlertEvent] = []

    def load(self) -> dict[str, AlertState]:
        return dict(self.states)

    def save(self, states: Mapping[str, AlertState], events: Sequence[AlertEvent]) -> None:
        self.states = dict(states)
        self.log.extend(events)


class JsonAlertStore(AlertStore):
    def __init__(self, directory: Path | str = DEFAULT_DIR) -> None:
        self._state_path = Path(directory) / "portfolio_alert_state.json"
        self._log_path = Path(directory) / "portfolio_alerts.jsonl"

    def load(self) -> dict[str, AlertState]:
        if not self._state_path.exists():
            return {}
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PortfolioStoreError(str(exc)) from exc
        return {
            key: AlertState(
                key, value["active"], _time(value.get("since")), _time(value.get("last_notified")), value.get("last_value")
            )
            for key, value in raw.items()
        }

    def save(self, states: Mapping[str, AlertState], events: Sequence[AlertEvent]) -> None:
        payload = {
            key: {"active": s.active, "since": _iso(s.since), "last_notified": _iso(s.last_notified), "last_value": s.last_value}
            for key, s in states.items()
        }
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8")
            with self._log_path.open("a", encoding="utf-8") as handle:
                for e in events:
                    handle.write(json.dumps({"key": e.key, "kind": e.kind.value, "position_id": e.position_id, "transition": e.transition,
                                             "at": e.at.isoformat(), "message": e.message}, ensure_ascii=False) + "\n")
        except OSError as exc:
            raise PortfolioStoreError(str(exc)) from exc


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _time(text: str | None) -> datetime | None:
    return None if text is None else datetime.fromisoformat(text)
