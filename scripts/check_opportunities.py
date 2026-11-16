"""Manual check of the Opportunity Scanner (/oportunidades) - NOT part of the
automated tests. Never starts Telegram, never trades, never opens a
simulated trade.

Usage, from the repository root:
    .venv\\Scripts\\python.exe scripts\\check_opportunities.py --simulado
        Synthetic fixture data, fixed clock, no network, nothing written.
        Prints a SIMULATED example report.
    .venv\\Scripts\\python.exe scripts\\check_opportunities.py
        Real Yahoo Finance 1-hour bars (free, read-only) for EUR/USD,
        GBP/USD and USD/JPY. No AI, no paid call. Records the scan and its
        ranked candidates in the prospective registry
        data/investment_committee.db (add --sin-registro to skip).
    ... --estrategia ruptura-rango-londres@0.1.0
        Scan with another registered strategy (default: v1.0.0).
    .venv\\Scripts\\python.exe scripts\\check_opportunities.py --con-ia 1
        Also asks the AI committee (DeepSeek, PAID: a few thousandths of a
        USD per review) to review at most N ranked candidates (max 3). It
        can only object. Needs DEEPSEEK_API_KEY in .env.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from dotenv import find_dotenv, load_dotenv

from investment_committee.application import OpportunityScannerService, build_default_ai_committee_service, build_default_opportunity_scanner
from investment_committee.application.opportunities import ScannerConfig
from investment_committee.conversation.opportunities_render import render_opportunity_scan
from investment_committee.domain import Asset, AssetType, MarketSnapshot, PriceBar
from investment_committee.market_intelligence import MarketIntelligenceService
from investment_committee.market_intelligence.providers.base import MarketDataError, MarketDataProvider
from investment_committee.simulation import InMemoryOpportunityScanLedger, InMemoryTradeJournal
from investment_committee.simulation.tracker import InMemoryTrackerLedger

FIXTURE_NOW = datetime(2026, 10, 13, 14, 20, tzinfo=timezone.utc)  # Tuesday: FX market open
HOUR = timedelta(hours=1)


class FixtureMarketData(MarketDataProvider):
    """Synthetic 1-hour bars ending just before FIXTURE_NOW: EUR/USD rising,
    GBP/USD falling, USD/JPY with a still-forming bar and one bar stamped in
    the future (both must be excluded). Deterministic, no network."""

    def get_daily_history(self, ticker: str) -> MarketSnapshot:
        raise MarketDataError("the fixture has no daily bars")

    def get_intraday_history(self, ticker: str, interval: str, period: str) -> MarketSnapshot:
        last_open = FIXTURE_NOW.replace(minute=0) - HOUR  # last CLOSED bar opened at 13:00
        if ticker == "EURUSD=X":
            bars = _bars(["0.0005", "-0.0004"], "1.10000", last_open, Decimal("0.0002"))
        elif ticker == "GBPUSD=X":
            bars = _bars(["-0.0006", "0.0005"], "1.30000", last_open, Decimal("0.0002"))
        elif ticker == "USDJPY=X":
            bars = _bars(["0.04", "-0.04"], "150.000", last_open, Decimal("0.02"))
            forming = last_open + HOUR  # opened 14:00, closes 15:00: still forming at 14:20
            future = last_open + 3 * HOUR
            bars += [PriceBar(t, bars[-1].close, bars[-1].close + Decimal("0.5"), bars[-1].close, bars[-1].close + Decimal("0.4"), 0) for t in (forming, future)]
        else:
            raise MarketDataError(f"no fixture for {ticker}")
        asset = Asset(ticker=ticker, name=None, asset_type=AssetType.STOCK, exchange=None, currency=None)
        return MarketSnapshot(asset=asset, generated_at=FIXTURE_NOW, price_bars=bars)


def _bars(pattern: list[str], first_close: str, last_open: datetime, wick: Decimal, count: int = 120) -> list[PriceBar]:
    bars, close = [], Decimal(first_close)
    for index in range(count):
        open_ = close
        close = open_ + Decimal(pattern[index % len(pattern)])
        timestamp = last_open - (count - 1 - index) * HOUR
        bars.append(PriceBar(timestamp, open_, max(open_, close) + wick, min(open_, close) - wick, close, 0))
    return bars


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulado", action="store_true", help="fixture data, no network, nothing recorded")
    parser.add_argument("--sin-registro", action="store_true", dest="no_record", help="do not write the scan ledger")
    parser.add_argument("--con-ia", type=int, default=0, dest="ai_reviews", help="AI reviews (paid), 0-3; default 0")
    parser.add_argument("--estrategia", default="tendencia-momentum-volatilidad@1.0.0",
                        help="strategy id, e.g. ruptura-rango-londres@0.1.0 (all are experimental)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    if args.simulado:
        if args.ai_reviews:
            print("--con-ia no se usa con --simulado: el ejemplo nunca hace llamadas pagadas.", file=sys.stderr)
        scanner = OpportunityScannerService(
            MarketIntelligenceService(FixtureMarketData()),
            InMemoryOpportunityScanLedger(),
            ScannerConfig(data_source="datos sintéticos de prueba (fixture), no son precios de mercado"),
            clock=lambda: FIXTURE_NOW,
            id_factory=lambda: "ejemplo-simulado",
        )
        print("=== EJEMPLO SIMULADO: datos sintéticos de prueba, no son precios de mercado reales ===\n")
        print(render_opportunity_scan(scanner.scan(ai_reviews=0)))
        return 0

    load_dotenv(find_dotenv(usecwd=True))
    ai_service = None
    if args.ai_reviews:
        print(f"Aviso: se pedirán hasta {args.ai_reviews} revisiones PAGADAS al comité de IA.", file=sys.stderr)
        ai_service = build_default_ai_committee_service(trading_service=journal_free_trading_service())
    from investment_committee.simulation.strategies import strategy_by_id

    scanner = build_default_opportunity_scanner(
        strategy=strategy_by_id(args.estrategia),
        trading_journal=None,
        ai_committee_service=ai_service,
        ledger=InMemoryOpportunityScanLedger() if args.no_record else None,
        tracker=InMemoryTrackerLedger() if args.no_record else None,
    )
    print(render_opportunity_scan(scanner.scan(ai_reviews=args.ai_reviews)))
    return 0


def journal_free_trading_service():  # type: ignore[no-untyped-def]
    """The AI committee's evidence uses the trading strategy's live reading;
    an in-memory journal guarantees the review never records a trade."""
    from investment_committee.application import build_default_trading_service

    return build_default_trading_service(journal=InMemoryTradeJournal())


if __name__ == "__main__":
    sys.exit(main())
