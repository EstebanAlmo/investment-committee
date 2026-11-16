"""Minimal internal dev/debug CLI for exercising ApplicationService.analyze_asset().

This is not a product-facing interface - Telegram (Epic 10) is the first
one. This CLI contains no business logic and no lower-layer orchestration;
it only calls investment_committee.application. See
docs/specs/spec-004-application-service.md, "CLI design".
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from investment_committee.application import (
    AnalysisResult,
    ApplicationService,
    build_default_application_service,
)
from investment_committee.application.errors import ApplicationServiceError


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    service = build_default_application_service()
    return _run_analyze(service, args.symbol)


def _run_analyze(service: ApplicationService, symbol: str) -> int:
    try:
        result = service.analyze_asset(symbol)
    except ApplicationServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(_format_result(result))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="investment_committee")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="Analyze one ticker")
    analyze_parser.add_argument("symbol", help="Ticker symbol, e.g. MSFT")

    return parser


def _format_result(result: AnalysisResult) -> str:
    decision = result.decision
    return (
        f"{decision.asset.ticker}: {decision.action.value} "
        f"(confidence {decision.confidence})\n"
        f"{decision.summary}"
    )
