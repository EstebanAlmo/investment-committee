"""Prospective monitor of the four frozen strategies - NOT part of the
automated tests and NOT scheduled by anything in this repository.

It reads free Yahoo 1-hour bars, evaluates the strategies at each closed
candle of the protocol window (2026-10-19 00:00 UTC to 2027-01-15 21:00 UTC),
and writes everything to data/investment_committee.db. It never trades
(real or simulated), never calls an AI, never starts Telegram and never
sends a message: notifications are only queued (scripts/send_notifications.py
sends them, when you decide to).

Usage, from the repository root:
    .venv\\Scripts\\python.exe scripts\\monitor.py --una-vez
        One cycle (for Windows Task Scheduler). Safe to run as often as you like:
        a candle already processed is skipped.
    .venv\\Scripts\\python.exe scripts\\monitor.py --continuo [--intervalo 300]
        Continuous mode: one cycle every 5 minutes until Ctrl+C.
    .venv\\Scripts\\python.exe scripts\\monitor.py --una-vez --ensayo --base data\\processed\\ensayo.db
        Rehearsal OUTSIDE the protocol window, on a separate database (never
        the protocol's). Writes nothing to data/investment_committee.db.
Logs: data/processed/monitor.log (git-ignored).
See docs/prospective-monitoring.md for the Task Scheduler commands.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from investment_committee.application.factory import build_default_monitor
from investment_committee.application.monitor import MonitorSettings
from investment_committee.application.portfolio import PortfolioGuardianService
from investment_committee.market_intelligence import MarketIntelligenceService
from investment_committee.memory.database import DEFAULT_DB_PATH
from investment_committee.portfolio.sqlite_store import SqliteAlertStore
from investment_committee.portfolio.store import JsonLinesPositionStore


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--una-vez", action="store_true", dest="once")
    mode.add_argument("--continuo", action="store_true", dest="forever")
    parser.add_argument("--intervalo", type=int, default=300, help="seconds between cycles in continuous mode")
    parser.add_argument("--base", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--ensayo", action="store_true", help="rehearsal outside the protocol window, on a separate database")
    args = parser.parse_args()

    log_dir = Path("data") / "processed"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=[logging.FileHandler(log_dir / "monitor.log", encoding="utf-8"), logging.StreamHandler()])

    settings = MonitorSettings()
    if args.ensayo:
        if args.base.resolve() == DEFAULT_DB_PATH.resolve():
            print("--ensayo exige --base con otra base de datos: el ensayo nunca escribe en la del protocolo.", file=sys.stderr)
            return 2
        now = datetime.now(timezone.utc)
        settings = MonitorSettings(window_start=now - timedelta(days=1), window_end=now + timedelta(days=1))

    guardian = PortfolioGuardianService(MarketIntelligenceService(), JsonLinesPositionStore(), SqliteAlertStore(args.base))
    monitor = build_default_monitor(db_path=args.base, settings=settings, guardian=guardian)
    if args.once:
        result = monitor.run_once()
        print(f"{result.moment:%Y-%m-%d %H:%M} UTC · {result.status} · estrategias {len(result.evaluated)} · "
              f"recomendaciones nuevas {result.new_recommendations} · incidencias {result.incidents} · huecos {result.gaps_recorded} · "
              f"notificaciones en cola {result.notifications_queued} {result.detail}")
        return 0
    monitor.run_forever(timedelta(seconds=args.intervalo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
