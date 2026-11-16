"""Manual check of the AI committee - NOT part of the automated tests.

Usage, from the repository root:
    .venv\\Scripts\\python.exe scripts\\check_ai_committee.py --solo-evidencia EUR/USD
        Fetches market data (read-only) and prints the evidence packet the
        agents would receive. No model call, no cost.
    .venv\\Scripts\\python.exe scripts\\check_ai_committee.py EUR/USD
        Runs ONE real /committee analysis with DeepSeek (paid: a few
        thousandths of a USD, see the audit line), prints the Spanish report
        and records it in data/ledger/ai_committee.jsonl. Needs
        DEEPSEEK_API_KEY in .env. Never starts Telegram, never trades.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from dotenv import find_dotenv, load_dotenv

from investment_committee.application import build_default_ai_committee_service, build_default_trading_service
from investment_committee.application.ai_evidence import build_evidence_packet
from investment_committee.application.factory import build_default_application_service
from investment_committee.conversation.ai_render import render_ai_committee
from investment_committee.conversation.symbol_resolver import canonical_symbol
from investment_committee.simulation import InMemoryTradeJournal


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol", nargs="?", default="EUR/USD")
    parser.add_argument("--solo-evidencia", action="store_true", dest="evidence_only")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    # One safe line per provider attempt (status, finish_reason, request id,
    # tokens - never the key, prompts or replies), to diagnose failures.
    logging.getLogger("investment_committee.integrations.llm").setLevel(logging.INFO)
    load_dotenv(find_dotenv(usecwd=True))
    symbol = canonical_symbol(args.symbol)
    trading = build_default_trading_service(journal=InMemoryTradeJournal())  # never writes the trade journal

    if args.evidence_only:
        analysis = build_default_application_service().analyze_asset(symbol)
        is_forex = symbol.endswith("=X")
        signal = trading.preview_signal(symbol) if is_forex else None
        packet = build_evidence_packet(
            symbol=symbol, display_name=args.symbol, is_forex=is_forex, analysis=analysis, trade_signal=signal,
            trading_unavailable_reason=None, documents=(), macro_source="no integrado", now=datetime.now(timezone.utc),
        )
        print(packet.render())
        return 0

    service = build_default_ai_committee_service(trading_service=trading)
    if not service.available:
        print(f"El comité de IA no está disponible: {service.unavailable_reason}.", file=sys.stderr)
        return 1
    print(render_ai_committee(service.analyze(symbol)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
