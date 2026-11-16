"""Reproduce a historical month with the Opportunity Scanner - NOT part of the
automated tests. Free, read-only Yahoo Finance 1-hour data; no AI call, no
Telegram, no trade, nothing written to any journal.

Usage, from the repository root:
    .venv\\Scripts\\python.exe scripts\\replay_month.py
        The last complete calendar month (UTC).
    .venv\\Scripts\\python.exe scripts\\replay_month.py --desde 2026-09-01 --hasta 2026-10-01
        Any period inside Yahoo's 730-day window for 1-hour bars ("hasta" excluded).
    ... --operaciones
        Also lists every simulated trade of each policy.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from investment_committee.application import build_default_replay_service
from investment_committee.conversation.replay_render import NAMES, render_replay
from investment_committee.conversation.trading_render import num


def _day(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--desde", type=_day, default=None)
    parser.add_argument("--hasta", type=_day, default=None)
    parser.add_argument("--operaciones", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    if (args.desde is None) != (args.hasta is None):
        print("Indica --desde y --hasta juntos, o ninguno.", file=sys.stderr)
        return 2

    report = build_default_replay_service().replay(args.desde, args.hasta)
    print(render_replay(report))
    if args.operaciones:
        for result in report.results:
            print(f"\nOperaciones de la política {result.policy.value}:")
            for t in sorted(result.trades, key=lambda x: (x.decided_at, x.symbol)):
                print(  # every time in UTC
                    f"  {t.decided_at:%m-%d %H:%M} {NAMES.get(t.symbol, t.symbol)} {t.direction.value} entrada {t.entry_price} "
                    f"stop {t.stop_loss} objetivo {t.take_profit} · {t.units} u · riesgo {num(t.risk_usd)} USD · salida "
                    f"{t.exit_time:%m-%d %H:%M} {t.exit_reason.value} {t.exit_price} · neto {num(t.net_pnl_usd)} USD (costos {num(t.costs_usd)})"
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
