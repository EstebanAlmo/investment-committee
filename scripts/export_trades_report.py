"""Write the offline trade report of a backtest study: one self-contained
HTML file and one CSV per strategy, to hand to a reviewer who has neither
Python nor this repository.

    python scripts/export_trades_report.py
    python scripts/export_trades_report.py --study data/reports/backtests/<name> --out <folder>

Reads the study and its snapshot only, after verifying their hashes: no
backtest is run, no study file is changed, nothing is downloaded or
published. The files go to data/reports/exports/<study>/reporte_offline
(git-ignored). The HTML opens with a double click, without internet. See
docs/trade-viewer.md.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from investment_committee.interfaces.trade_viewer.offline_report import HTML_NAME, PERIOD, SCENARIO, build_files
from investment_committee.interfaces.trade_viewer.study_store import StudyError, find_snapshot, load_study

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STUDY = ROOT / "data" / "reports" / "backtests" / "fx_1h_20260331_20260930_107957e7_e6cf138"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--study", type=Path, default=DEFAULT_STUDY)
    parser.add_argument("--snapshots", type=Path, default=ROOT / "data" / "raw" / "snapshots")
    parser.add_argument("--snapshot", type=Path, default=None, help="carpeta exacta del snapshot, cuando no está bajo --snapshots con el nombre del manifiesto")
    parser.add_argument("--out", type=Path, default=None, help="carpeta de salida (por defecto data/reports/exports/<estudio>/reporte_offline)")
    parser.add_argument("--html-name", default=HTML_NAME, help="nombre del archivo HTML")
    parser.add_argument("--period", default=PERIOD)
    parser.add_argument("--scenario", default=SCENARIO)
    options = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # the same text on a Windows console and on macOS

    try:
        snapshot = options.snapshot or find_snapshot(options.study, options.snapshots)
        if snapshot is None:
            raise StudyError("no se encontró el snapshot que nombra el manifiesto del estudio")
        study = load_study(options.study, snapshot)
        files = build_files(study, period=options.period, scenario=options.scenario)
    except StudyError as error:
        print(f"No se generó el reporte: {error}", file=sys.stderr)
        return 1

    out = options.out or ROOT / "data" / "reports" / "exports" / study.name / "reporte_offline"
    out.mkdir(parents=True, exist_ok=True)
    print(f"Estudio {study.study_sha256} verificado. Período {options.period}, escenario {options.scenario}.")
    for name, text in files.items():
        data = text.encode("utf-8")
        name = options.html_name if name == HTML_NAME else name
        (out / name).write_bytes(data)
        print(f"  {name}: {len(data):,} bytes · SHA-256 {hashlib.sha256(data).hexdigest()}")
    print(f"Carpeta: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
