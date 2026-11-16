"""Portfolio Guardian from the command line - NOT part of the automated tests.
Monitoring only: nothing here sends an order or closes a position at a
broker (there is none). Prices: free, read-only Yahoo Finance.

Usage, from the repository root (.venv\\Scripts\\python.exe scripts\\portfolio.py ...):
    agregar --id eur1 --instrumento EUR/USD --direccion SHORT --cantidad 10000 --precio 1.12000 [--stop 1.12500]
            [--fecha 2026-10-13T09:00] [--costos 2.5] [--apalancamiento 30]
    agregar --id oro1 --instrumento XAU/USD --direccion LONG --cantidad 1 --precio 4000 --multiplicador 100
    agregar --id aapl --instrumento AAPL --tipo ACCION --direccion LONG --cantidad 10 --precio 230 --moneda USD
    cerrar --id eur1 [--nota "vendida en el bróker"]     (deja de vigilarla; no envía nada)
    listar                                               (valoración, como /portafolio)
    alertas [--usar-futuros-oro]                         (comprobación manual, como /alertas)

Scheduling (NOT enabled by default): to check alerts periodically you could
register `scripts\\portfolio.py alertas` with Windows Task Scheduler
(schtasks /Create ...), but nothing in the repository creates such a task,
and the check sends no Telegram message - it only prints and records.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from decimal import Decimal

from investment_committee.application import GuardianSettings, build_default_portfolio_guardian
from investment_committee.conversation.monitoring_render import render_alerts, render_portfolio
from investment_committee.portfolio.guardian import AssetKind, PortfolioPosition, Side, forex_position
from investment_committee.portfolio.store import JsonLinesPositionStore


def _when(text: str | None) -> datetime:
    if not text:
        return datetime.now(timezone.utc)
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("agregar")
    add.add_argument("--id", required=True)
    add.add_argument("--instrumento", required=True)
    add.add_argument("--tipo", choices=["FOREX", "ORO", "ACCION"], default=None)
    add.add_argument("--direccion", choices=["LONG", "SHORT"], required=True)
    add.add_argument("--cantidad", type=Decimal, required=True)
    add.add_argument("--precio", type=Decimal, required=True)
    add.add_argument("--moneda", default="USD")
    add.add_argument("--fecha", default=None)
    add.add_argument("--costos", type=Decimal, default=Decimal("0"))
    add.add_argument("--stop", type=Decimal, default=None)
    add.add_argument("--multiplicador", type=Decimal, default=Decimal("1"))
    add.add_argument("--apalancamiento", type=Decimal, default=Decimal("1"))
    close = sub.add_parser("cerrar")
    close.add_argument("--id", required=True)
    close.add_argument("--nota", default="")
    sub.add_parser("listar")
    alerts = sub.add_parser("alertas")
    alerts.add_argument("--usar-futuros-oro", action="store_true", dest="gold_proxy")
    args = parser.parse_args()

    store = JsonLinesPositionStore()
    if args.command == "agregar":
        name = args.instrumento.strip().upper()
        kind = args.tipo or ("ORO" if name in ("XAU/USD", "XAUUSD") else "FOREX" if "/" in name or name.endswith("=X") else "ACCION")
        side = Side(args.direccion)
        common = dict(costs_usd=args.costos, stop=args.stop, multiplier=args.multiplicador, leverage=args.apalancamiento)
        if kind == "FOREX":
            position = forex_position(args.id, name, side, args.cantidad, args.precio, _when(args.fecha), **common)
        elif kind == "ORO":
            position = PortfolioPosition(args.id, "XAUUSD", AssetKind.METAL, side, args.cantidad, args.precio, "USD", _when(args.fecha), **common)
        else:
            position = PortfolioPosition(args.id, name, AssetKind.EQUITY, side, args.cantidad, args.precio, args.moneda.upper(), _when(args.fecha), **common)
        store.add(position, datetime.now(timezone.utc))
        print(f"Registrada {position.position_id}: {position.instrument} {position.side.value} (solo seguimiento).")
        return 0
    if args.command == "cerrar":
        store.close(args.id, datetime.now(timezone.utc), args.nota)
        print(f"{args.id} ya no se vigila. No se envió ninguna orden.")
        return 0
    settings = GuardianSettings(allow_gold_futures_proxy=getattr(args, "gold_proxy", False))
    guardian = build_default_portfolio_guardian(settings=settings)
    print(render_portfolio(guardian.view()) if args.command == "listar" else render_alerts(guardian.check()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
