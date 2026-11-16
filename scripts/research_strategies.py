"""Strategy research on already-inspected months (diagnosis only) - NOT part
of the automated tests. Uses the multi-period snapshot (downloads it only if
missing), runs v1.0.0 and the three experimental V2 strategies with frozen
parameters, prints the comparison, writes CSV files and records the
summaries in SQLite as RETROSPECTIVO. No AI call, no Telegram, no broker.

Usage, from the repository root:
    .venv\\Scripts\\python.exe scripts\\research_strategies.py --desde 2026-04-01 --hasta 2026-10-01 [--sin-registro]
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime
from pathlib import Path

from investment_committee.application.period import PeriodEvaluationService
from investment_committee.application.research import monthly_table, record_retrospective, run_research, totals_table
from investment_committee.conversation.research_render import render_research
from investment_committee.market_intelligence import MarketIntelligenceService


def _day(text: str):  # type: ignore[no-untyped-def]
    return datetime.strptime(text, "%Y-%m-%d").date()


def _write_csv(path: Path, rows: list[dict]) -> None:  # type: ignore[type-arg]
    with path.open("w", encoding="utf-8", newline="") as handle:
        if rows:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--desde", type=_day, required=True)
    parser.add_argument("--hasta", type=_day, required=True)
    parser.add_argument("--sin-registro", action="store_true", dest="no_record")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    research = run_research(PeriodEvaluationService(MarketIntelligenceService()), args.desde, args.hasta)
    print(render_research(research))
    out = Path("data") / "reports" / f"research_{args.desde:%Y%m%d}_{args.hasta:%Y%m%d}"
    out.mkdir(parents=True, exist_ok=True)
    _write_csv(out / "totals.csv", totals_table(research))
    _write_csv(out / "monthly.csv", monthly_table(research))
    print(f"\nArchivos: {out}")
    if not args.no_record:
        print(f"Registro retrospectivo en SQLite: {record_retrospective(research)} filas nuevas o ya existentes (backtest_runs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
