"""Multi-month robustness evaluation of the Opportunity Scanner - NOT part of
the automated tests. Free, read-only Yahoo data, downloaded once and kept as
a hashed snapshot under data/raw/snapshots/. No AI call, no Telegram, no
broker, no trade.

Usage, from the repository root:
    .venv\\Scripts\\python.exe scripts\\replay_period.py --desde 2026-04-01 --hasta 2026-10-01
        Session months April-September 2026. Console report plus CSV/JSON in
        data/reports/period_<desde>_<hasta>/ (git-ignored).
    ... --refrescar   download again instead of reusing the snapshot
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from investment_committee.application.period import (
    PeriodEvaluationService,
    equity_table,
    month_table,
    rejections_table,
    summary,
    trades_table,
)
from investment_committee.conversation.period_render import render_period
from investment_committee.market_intelligence import MarketIntelligenceService


def _day(text: str):  # type: ignore[no-untyped-def]
    return datetime.strptime(text, "%Y-%m-%d").date()


def _write_csv(path: Path, rows: list[dict]) -> None:  # type: ignore[type-arg]
    with path.open("w", encoding="utf-8", newline="") as handle:
        if not rows:
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--desde", type=_day, required=True)
    parser.add_argument("--hasta", type=_day, required=True)
    parser.add_argument("--refrescar", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    report = PeriodEvaluationService(MarketIntelligenceService()).evaluate(args.desde, args.hasta, refresh=args.refrescar)
    print(render_period(report))

    out = Path("data") / "reports" / f"period_{args.desde:%Y%m%d}_{args.hasta:%Y%m%d}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary(report), indent=1, ensure_ascii=False), encoding="utf-8")
    _write_csv(out / "monthly_continuous.csv", month_table(report.continuous_rows, report.symbols))
    _write_csv(out / "monthly_normalized.csv", month_table(report.normalized_rows, report.symbols))
    _write_csv(out / "trades.csv", trades_table(report))
    _write_csv(out / "equity_curve.csv", equity_table(report))
    _write_csv(out / "rejections.csv", rejections_table(report))
    print(f"\nArchivos: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
