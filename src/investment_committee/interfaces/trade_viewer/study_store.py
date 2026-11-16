"""Read-only access to a backtest study written by
simulation/forex_backtest_study.py, for the trade viewer.

Nothing here writes, recomputes a signal or runs a backtest. Standard
library only: it works without Plotly or Streamlit.

A study is trusted only after it checks out against its own manifest:
  load_study  manifest.json's study hash; the snapshot's bars against the
              manifest's dataset hash (the manifest stores no path, so the
              snapshot folder is given by whoever opens the study);
  load_run    run.json against the results hash the manifest recorded for
              that run - every trade, decision and hourly point;
  load_signals  signals.csv has no hash of its own in the manifest (a gap
              of B4.3, stated here rather than hidden): it is accepted only
              if, for every decision of the run being viewed, it holds the
              same instrument, instant, outcome and ATR as the verified
              run.json (check_signals).
Anything that does not match raises StudyError: the viewer shows the error
and draws nothing.

Everything is kept as the files spell it: decimals as Decimal parsed from
text, instants as UTC datetimes. Instruments are named without Yahoo's
"=X" suffix ("EURUSD").
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

from investment_committee.domain import PriceBar
from investment_committee.market_intelligence.snapshots import SnapshotError, read_snapshot
from investment_committee.simulation.forex_backtest_metrics import _sha256, dataset_sha256
from investment_committee.simulation.trade_proposal import instrument_for

UTC = timezone.utc


class StudyError(Exception):
    """The study, its snapshot or one of its files is not what the manifest says."""


def instrument(symbol: str) -> str:
    rules = instrument_for(symbol)
    if rules is None:
        raise StudyError(f"instrumento no soportado: {symbol}")
    return rules.symbol


def instant(text: str | None) -> datetime | None:
    return None if not text else datetime.fromisoformat(text).astimezone(UTC)


def number(text: Any) -> Decimal | None:
    if text is None or text == "":
        return None
    try:
        return Decimal(str(text))
    except InvalidOperation as error:
        raise StudyError(f"valor numérico ilegible: {text!r}") from error


@dataclass(frozen=True)
class RunKey:
    strategy_id: str
    period: str
    scenario: str

    @property
    def folder(self) -> str:
        return f"{self.strategy_id}/{self.period}/{self.scenario}"


@dataclass(frozen=True)
class Study:
    directory: Path  # never shown or exported
    name: str  # the folder's name
    manifest: Mapping[str, Any]
    bars: Mapping[str, Sequence[PriceBar]]  # instrument -> bars, oldest first, labels in UTC

    @property
    def study_sha256(self) -> str:
        return str(self.manifest["study_sha256"])

    @property
    def keys(self) -> list[RunKey]:
        return [RunKey(*folder.split("/")) for folder in self.manifest["runs"]]

    @property
    def strategies(self) -> list[str]:
        return list(self.manifest["strategies"])

    def options(self, field: str) -> list[str]:
        return list(dict.fromkeys(getattr(key, field) for key in self.keys))


@dataclass(frozen=True)
class Run:
    key: RunKey
    data: Mapping[str, Any]  # run.json, verified
    summary: Mapping[str, Any]  # record.json's summary of the same run
    record: Mapping[str, Any]

    @property
    def trades(self) -> Sequence[Mapping[str, Any]]:
        return self.data["trades"]

    @property
    def decisions(self) -> Sequence[Mapping[str, Any]]:
        return self.data["decisions"]

    @property
    def equity(self) -> Sequence[Mapping[str, Any]]:
        return self.data["equity"]

    def trade(self, trade_id: str) -> Mapping[str, Any]:
        found = [t for t in self.trades if t["proposal"]["proposal_id"] == trade_id]
        if len(found) != 1:
            raise StudyError(f"la corrida {self.key.folder} tiene {len(found)} operaciones con el identificador {trade_id}")
        return found[0]


def _read(path: Path) -> str:
    if not path.is_file():
        raise StudyError(f"falta el archivo {path.name} del estudio")
    return path.read_text(encoding="utf-8")


def _json(path: Path) -> Any:
    try:
        return json.loads(_read(path))
    except json.JSONDecodeError as error:
        raise StudyError(f"{path.name} no es un JSON válido") from error


def load_study(directory: Path, snapshot: Path) -> Study:
    """The study in `directory`, with the bars of `snapshot`, after checking
    both against the manifest."""
    manifest = _json(directory / "manifest.json")
    reproducible = {k: v for k, v in manifest.items() if k not in ("study_sha256", "execution")}
    if _sha256(reproducible) != manifest.get("study_sha256"):
        raise StudyError("manifest.json no coincide con su propio hash de estudio: fue modificado")
    try:
        _, data = read_snapshot(snapshot)  # checks each file against the snapshot's own manifest
    except (SnapshotError, OSError, KeyError, ValueError) as error:
        raise StudyError(f"el snapshot no se puede leer o no coincide con sus hashes: {error}") from error
    used = {symbol: series for symbol, series in data.items() if instrument_for(symbol) is not None}
    if dataset_sha256(used) != manifest["dataset_sha256"]:
        raise StudyError("el snapshot no es el dataset con el que se ejecutó el estudio (hash distinto)")
    bars = {instrument(symbol): tuple(sorted((PriceBar(b.timestamp.astimezone(UTC), b.open, b.high, b.low, b.close, b.volume) for b in series),
                                             key=lambda b: b.timestamp)) for symbol, series in used.items()}
    return Study(directory, directory.name, manifest, bars)


def load_run(study: Study, key: RunKey) -> Run:
    if key.folder not in study.manifest["runs"]:
        raise StudyError(f"el estudio no contiene la corrida {key.folder}")
    folder = study.directory.joinpath(*key.folder.split("/"))
    data = _json(folder / "run.json")
    if _sha256(data) != study.manifest["runs"][key.folder]["results_sha256"]:
        raise StudyError(f"run.json de {key.folder} no coincide con el hash registrado en el manifiesto")
    record = _json(folder / "record.json")
    if record["reproducible"]["results_sha256"] != study.manifest["runs"][key.folder]["results_sha256"]:
        raise StudyError(f"record.json de {key.folder} describe otros resultados")
    return Run(key, data, record["reproducible"]["summary"], record["reproducible"])


Signals = Mapping[str, Mapping[datetime, Mapping[str, str]]]  # instrument -> candle close (UTC) -> the row of signals.csv


def load_signals(study: Study, strategy_id: str) -> Signals:
    """Every evaluation the strategy recorded, by instrument and by the
    instant its candle closed. Values stay as the text the engine wrote."""
    text = _read(study.directory / strategy_id / "signals.csv")
    out: dict[str, dict[datetime, Mapping[str, str]]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        moment = instant(row["at"])
        assert moment is not None
        out.setdefault(instrument(row["symbol"]), {})[moment] = row
    return out


def check_signals(run: Run, signals: Signals) -> None:
    """signals.csv must agree with the verified run on every decision:
    same instrument and instant, same outcome, same ATR."""
    for decision in run.decisions:
        moment, symbol = instant(decision["at"]), instrument(decision["symbol"])
        row = signals.get(symbol, {}).get(moment)  # type: ignore[arg-type]
        if row is None:
            raise StudyError(f"signals.csv no tiene la evaluación de {symbol} en {decision['at']}")
        atr = decision["proposal"]["atr"]
        same_atr = atr is None or number(row.get("atr")) == number(atr)  # None: rejected before the ATR was read
        if row.get("outcome") != decision["direction"] or not same_atr or row.get("strategy_id") != run.data["strategy_id"]:
            raise StudyError(f"signals.csv no coincide con la corrida en {symbol} {decision['at']}: los indicadores no son fiables")


def find_snapshot(study_directory: Path, snapshots: Path) -> Path | None:
    """The snapshot folder a study names in its manifest, if it exists under
    `snapshots` (the manifest holds the snapshot's name, never a path)."""
    description = str(_json(study_directory / "manifest.json").get("dataset_description", ""))
    if description.startswith("snapshot ") and ":" in description:
        candidate = snapshots / description[len("snapshot "):description.index(":")].strip()
        if candidate.is_dir():
            return candidate
    return None
