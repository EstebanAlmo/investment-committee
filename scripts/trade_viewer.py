"""Local dashboard to review the simulated trades of a backtest study.

    pip install -e ".[viewer]"
    python -m streamlit run scripts/trade_viewer.py

Run it from the repository root, on Windows or macOS: .streamlit/config.toml
keeps the server on 127.0.0.1 (this computer only) and sends no usage
statistics. It reads the studies under data/reports/backtests and the
snapshots under data/raw/snapshots; it writes only review notes
(data/reviews) and exports (data/reports/exports). No backtest is run, no
study is changed, nothing is published.

The four folders can be changed with the environment variables
TRADE_VIEWER_STUDIES, TRADE_VIEWER_SNAPSHOTS, TRADE_VIEWER_REVIEWS and
TRADE_VIEWER_EXPORTS (used by the tests).
"""

from __future__ import annotations

import os
from pathlib import Path

from investment_committee.interfaces.trade_viewer.app import main

ROOT = Path(__file__).resolve().parents[1]


def _folder(variable: str, *default: str) -> Path:
    return Path(os.environ[variable]) if os.environ.get(variable) else ROOT.joinpath(*default)


main(
    studies=_folder("TRADE_VIEWER_STUDIES", "data", "reports", "backtests"),
    snapshots=_folder("TRADE_VIEWER_SNAPSHOTS", "data", "raw", "snapshots"),
    reviews=_folder("TRADE_VIEWER_REVIEWS", "data", "reviews"),
    exports=_folder("TRADE_VIEWER_EXPORTS", "data", "reports", "exports"),
)
