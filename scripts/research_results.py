"""The figures of a registered retrospective test, read from its stored
study: the engine's own summary of every scenario and the protocol's
statistics (docs/forex-research-protocol.md, sections 5 to 7). Reads only:
nothing is run again, so it can be repeated at will and always gives the
same numbers.

    python scripts/research_results.py --study data/reports/backtests/<name> --snapshot data/raw/ingest/<name>/normalizado

The study, its snapshot and every run are verified against their hashes
first. Output is JSON on standard output.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from investment_committee.interfaces.trade_viewer.study_store import RunKey, StudyError, load_run, load_study
from investment_committee.simulation.research_statistics import research_summary

SEED = 20261113  # registered in docs/research/ema-pullback-forex-0.1.0.md
THRESHOLD = 200  # valid closed trades below which the verdict is NO CONCLUYENTE


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--period-start", default="2025-07-14T00:00:00+00:00", help="inicio del período cuyas mitades se comparan")
    options = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        study = load_study(options.study, options.snapshot)
        out: dict[str, object] = {"study_sha256": study.study_sha256, "dataset_sha256": study.manifest["dataset_sha256"],
                                  "commit": study.manifest["commit"], "first_evaluable": study.manifest["common_start"], "end": study.manifest["end"],
                                  "runs": {}}
        start, end = datetime.fromisoformat(options.period_start).astimezone(timezone.utc), datetime.fromisoformat(study.manifest["end"])
        for key in study.keys:
            run = load_run(study, RunKey(key.strategy_id, key.period, key.scenario))
            s = run.summary
            valid = s["closed"]["TOTAL"]["n"]
            out["runs"][key.folder] = {  # type: ignore[index]
                "results_sha256": study.manifest["runs"][key.folder]["results_sha256"],
                "funnel": s["funnel"], "closed": s["closed"], "monthly": s["monthly"], "drawdown_balance": s["drawdown_balance"],
                "drawdown_equity": s["drawdown_equity"], "final_balance": s["final_balance"], "final_equity": s["final_equity"],
                "return_fraction": s["return_fraction"], "open_unrealized": s["open_unrealized"], "indeterminate_by_kind": s["indeterminate_by_kind"],
                "coverage": s["coverage"],
                "enough_trades_for_a_verdict": valid >= THRESHOLD,
                "statistics": research_summary(run.trades, start=start, end=end, seed=SEED),
            }
    except StudyError as error:
        print(f"No se pudo leer el estudio: {error}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
