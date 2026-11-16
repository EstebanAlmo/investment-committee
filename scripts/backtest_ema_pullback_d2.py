"""The single registered retrospective test of strategy C
(`ema-pullback-forex@0.1.0`) on D2, as registered in
docs/research/ema-pullback-forex-0.1.0.md. Reads files only: no download,
no order, no database.

    python scripts/backtest_ema_pullback_d2.py --check     # the checks only: no price of D2 is evaluated
    python scripts/backtest_ema_pullback_d2.py             # the run. ONCE.

It refuses to run unless every one of these holds:
  - the working tree is clean, so the recorded commit is the code that ran;
  - the registration file has the SHA-256 pinned in the test suite;
  - the strategy's fingerprint is the registered one;
  - the engine, proposal and strategy code are byte for byte the registered
    state (commit 51c60ab);
  - the dataset checks out end to end and its hash is the registered one;
  - no study of C on D2 exists yet: D2 is read once for this hypothesis.

The bars before 2025-07-14 00:00 UTC (D1) only warm the indicators up: the
first candle evaluated is the first one of D2. ESTIMATED execution model on
Yahoo's single indicative price: nothing it prints is a real net return or
an independent validation.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from investment_committee.market_intelligence.ingest import IngestError, verify_ingest
from investment_committee.market_intelligence.quotes import QuoteKind
from investment_committee.market_intelligence.snapshots import read_snapshot
from investment_committee.simulation import ema_pullback
from investment_committee.simulation.forex_backtest import STRATEGY_C
from investment_committee.simulation.forex_backtest_metrics import dataset_sha256, render, summarize
from investment_committee.simulation.forex_backtest_study import FULL, YAHOO_CALENDAR, write_study, yahoo_week
from investment_committee.simulation.registered_study import run_from

ROOT = Path(__file__).resolve().parents[1]
SYMBOLS = ("EURUSD=X", "GBPUSD=X")
SOURCE = "Yahoo Finance H1 (precio indicativo único, sin bid/ask)"
REGISTRATION = ROOT / "docs" / "research" / "ema-pullback-forex-0.1.0.md"
REGISTRATION_SHA256 = "56e2fedefdd1dc317ff203f89f8b305917b4b7ecd95b8aef2abfeb7f05f3e90a"
REGISTERED_FINGERPRINT = "c4891b951204320a8fffb22539f0a4ec399c5094f4cf4bb56039f3fa97debe71"
REGISTERED_DATASET = "a8f7111623dbd578d072ea1a04b4a218c4ac7d75fcbd83abeb06fe0404140b91"
REGISTERED_CODE = "51c60ab"
FROZEN_CODE = ("src/investment_committee/simulation", "src/investment_committee/market_intelligence", "src/investment_committee/domain")
ADDED_SINCE = ("src/investment_committee/simulation/registered_study.py", "src/investment_committee/simulation/research_statistics.py")
SPLIT = datetime(2025, 7, 14, tzinfo=timezone.utc)  # D2: bars labelled from this instant
PREFIX = "d2_ema-pullback-forex-0.1.0"


def _git(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *arguments], cwd=ROOT, capture_output=True, text=True)


def problems(ingest: Path, out: Path) -> list[str]:
    """Why the run may not start; empty when it may."""
    found = []
    if _git("status", "--porcelain", "--untracked-files=no").stdout.strip():
        found.append("el árbol de trabajo tiene cambios sin commit")
    if hashlib.sha256(REGISTRATION.read_bytes().replace(b"\r\n", b"\n")).hexdigest() != REGISTRATION_SHA256:
        found.append("el registro de la hipótesis no tiene el hash fijado")
    if ema_pullback.fingerprint() != REGISTERED_FINGERPRINT or STRATEGY_C.parameters_sha256 != REGISTERED_FINGERPRINT:
        found.append("la huella de parámetros de C no es la registrada")
    changed = _git("diff", "--name-only", REGISTERED_CODE, "HEAD", "--", *FROZEN_CODE).stdout.split()
    if _git("rev-parse", "--verify", REGISTERED_CODE).returncode != 0 or set(changed) - set(ADDED_SINCE):
        found.append(f"el código del motor o de la estrategia cambió desde {REGISTERED_CODE}: {sorted(set(changed) - set(ADDED_SINCE))}")
    try:
        record = verify_ingest(ingest, timeframe=timedelta(hours=1), calendar=yahoo_week)
        if record["dataset_sha256"] != REGISTERED_DATASET:
            found.append("el hash del dataset no es el registrado")
    except IngestError as error:
        found.append(f"el dataset no se verifica: {error}")
    if out.is_dir() and any(path.name.startswith(PREFIX) for path in out.iterdir()):
        found.append("ya existe un estudio de C sobre D2: D2 se lee una sola vez para esta hipótesis")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ingest", type=Path, default=ROOT / "data" / "raw" / "ingest" / "yahoo_fx_1h_20241114_20260323")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "reports" / "backtests")
    parser.add_argument("--check", action="store_true", help="solo las comprobaciones previas: no se evalúa ningún precio")
    options = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # the same text on a Windows console and on macOS

    found = problems(options.ingest, options.out)
    if found:
        print("La corrida registrada NO puede empezar:\n  - " + "\n  - ".join(found), file=sys.stderr)
        return 2
    commit = _git("rev-parse", "HEAD").stdout.strip()
    print(f"Comprobaciones previas superadas. Commit {commit}, registro {REGISTRATION_SHA256[:12]}, dataset {REGISTERED_DATASET[:12]}.")
    if options.check:
        return 0

    manifest, data = read_snapshot(options.ingest / "normalizado")
    bars = {symbol: data[symbol] for symbol in SYMBOLS}
    dataset = dataset_sha256(bars)
    target = options.out / f"{PREFIX}_{dataset[:8]}_{commit[:7]}"
    study = run_from(bars, STRATEGY_C, start=SPLIT + timedelta(hours=1), data_kind=QuoteKind.ESTIMATED, data_source=SOURCE, calendar=yahoo_week)
    description = (f"snapshot {options.ingest.name}/normalizado: {', '.join(SYMBOLS)}; {manifest.source}; descargado {manifest.downloaded_at}; "
                   f"hash combinado del snapshot {manifest.combined_sha256}; D2: velas etiquetadas desde {SPLIT.isoformat()}, las anteriores (D1) "
                   f"solo calientan los indicadores; registro docs/research/{REGISTRATION.name} sha256 {REGISTRATION_SHA256}")
    written = write_study(target, study, commit=commit, dataset=dataset, dataset_description=description, calendar_description=YAHOO_CALENDAR,
                          executed_at=datetime.now(timezone.utc))
    print(f"Estudio {written['study_sha256']}\nPrimera hora evaluable {study.common_start.isoformat()} | fin {study.end.isoformat()} | carpeta {target.name}\n")
    for scenario in ("BASE", "COSTOS_X1_5"):
        print(f"===== {STRATEGY_C.strategy_id} | D2 | {scenario} =====")
        print(render(summarize(study.runs[(STRATEGY_C.strategy_id, FULL, scenario)])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
