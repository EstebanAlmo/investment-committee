"""Update the Opportunity Tracker's pending evaluations - NOT part of the
automated tests. Reads data/ledger/opportunity_tracker.jsonl, fetches recent
1-hour Yahoo bars (free, read-only) only for candidates still PENDIENTE or
ACTIVADA, and appends a STATUS event when a status changed. Never trades,
never starts Telegram, makes no AI call, and runs only when you run it.

Usage, from the repository root:
    .venv\\Scripts\\python.exe scripts\\update_tracker.py
"""

from __future__ import annotations

import logging
import sys

from investment_committee.application import build_default_tracker_service
from investment_committee.conversation.monitoring_render import render_tracker


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    print(render_tracker(build_default_tracker_service().update()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
