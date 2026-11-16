"""Regenerate the Excel report from the SQLite registry - NOT part of the
automated tests. Excel is only an output: the file is rebuilt from
data/investment_committee.db on every run (nothing is lost, nothing is
duplicated). No network.

Usage, from the repository root:
    .venv\\Scripts\\python.exe scripts\\export_excel.py [--salida data\\reports\\investment_committee.xlsx]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from investment_committee.memory.database import DEFAULT_DB_PATH, audit, connect
from investment_committee.memory.excel_export import DEFAULT_XLSX_PATH, export_excel


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--salida", type=Path, default=DEFAULT_XLSX_PATH)
    args = parser.parse_args()
    target = export_excel(args.base, args.salida)
    with connect(args.base) as conn:
        audit(conn, datetime.now(timezone.utc), "EXPORTACION_EXCEL", "report", str(target), {})
        counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("recommendations", "candidate_events", "strategies", "backtest_runs", "alert_events")}
    print(f"Excel regenerado desde {args.base}: {target}")
    print("Filas en la base: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
