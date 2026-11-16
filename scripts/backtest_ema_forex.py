"""Diagnostic backtest of the two experimental Forex strategies on an
existing hourly snapshot (B4.3). Reads files only: no download, no order,
no database.

    python scripts/backtest_ema_forex.py
    python scripts/backtest_ema_forex.py --snapshot data/raw/snapshots/<name> --out data/reports/backtests

ESTIMATED execution model on Yahoo's single indicative price: nothing it
prints is a real net return, an independent validation or a winner. The
snapshot's hashes are verified before anything runs; the working tree must
be clean so that the recorded commit is the code that ran; an existing
output folder is never rewritten. See docs/ema-forex-backtest.md.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from investment_committee.market_intelligence.quotes import QuoteKind
from investment_committee.market_intelligence.snapshots import read_snapshot
from investment_committee.simulation.forex_backtest_metrics import dataset_sha256, render, summarize
from investment_committee.simulation.forex_backtest_study import COMMON, FULL, YAHOO_CALENDAR, run_study, write_study, yahoo_week

ROOT = Path(__file__).resolve().parents[1]
SYMBOLS = ("EURUSD=X", "GBPUSD=X")
SOURCE = "Yahoo Finance H1 (precio indicativo único, sin bid/ask)"


def _git(*arguments: str) -> str:
    return subprocess.run(["git", *arguments], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", type=Path, default=ROOT / "data" / "raw" / "snapshots" / "fx_1h_20260331_20260930")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "reports" / "backtests")
    parser.add_argument("--allow-dirty", action="store_true", help="solo para ensayos con datos sintéticos: el commit registrado no describe el código")
    parser.add_argument("--kind", choices=[k.value for k in QuoteKind if k is not QuoteKind.OBSERVED], default=QuoteKind.ESTIMATED.value)
    options = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # the same text on a Windows console and on macOS

    commit = _git("rev-parse", "HEAD")
    dirty = bool(_git("status", "--porcelain", "--untracked-files=no"))
    if dirty and not options.allow_dirty:
        print("El árbol de trabajo tiene cambios sin commit: el commit registrado no describiría el código ejecutado.", file=sys.stderr)
        return 2
    manifest, data = read_snapshot(options.snapshot)  # raises if a file does not match its recorded hash
    missing = [symbol for symbol in SYMBOLS if symbol not in data]
    if missing:
        print(f"El snapshot no contiene {missing}.", file=sys.stderr)
        return 2
    bars = {symbol: data[symbol] for symbol in SYMBOLS}
    dataset = dataset_sha256(bars)
    target = options.out / f"{options.snapshot.name}_{dataset[:8]}_{commit[:7]}{'_SUCIO' if dirty else ''}"
    if target.exists():
        print(f"{target.name} ya existe: los resultados de un estudio no se reescriben.", file=sys.stderr)
        return 2

    kind = QuoteKind(options.kind)
    study = run_study(bars, data_kind=kind, data_source=SOURCE if kind is QuoteKind.ESTIMATED else "serie sintética de ensayo", calendar=yahoo_week)
    description = (f"snapshot {options.snapshot.name}: {', '.join(SYMBOLS)}; {manifest.source}; descargado {manifest.downloaded_at}; "
                   f"hash combinado del snapshot {manifest.combined_sha256}")
    written = write_study(target, study, commit=commit + ("+cambios-sin-commit" if dirty else ""), dataset=dataset, dataset_description=description,
                          calendar_description=YAHOO_CALENDAR, executed_at=datetime.now(timezone.utc))

    print(f"Estudio {written['study_sha256']}\nDatos {kind.value} | dataset {dataset} | commit {written['commit']}")
    print(f"Inicio común {study.common_start.isoformat()} | fin {study.end.isoformat()} | carpeta {target.name}\n")
    for strategy in study.strategies:
        for period in (FULL, COMMON):
            print(f"===== {strategy} | período {period} =====")
            print(render(summarize(study.runs[(strategy, period, "BASE")])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
