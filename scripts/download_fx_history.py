"""Download hourly EUR/USD and GBP/USD history that is OLDER than the
period already inspected, and ingest it as a verifiable dataset (B5.1).

    python scripts/download_fx_history.py

Source: Yahoo Finance through yfinance, the provider this project already
uses - one indicative mid price per hour, NOT bid/ask. Yahoo serves 1h bars
only for the last 730 days, so the dataset starts about two years before
today; it ends where the existing snapshot begins (2026-03-23 00:00 UTC,
exclusive). Nothing at or after that instant is requested: the months
already inspected and everything later stay out of it.

Two requests in total (one per pair), retried at most twice. Idempotent: if
the dataset folder already exists nothing is downloaded - it is verified
and its report is printed. Originals are never overwritten. See
docs/data-ingest.md. This script computes no signal and no return.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from investment_committee.market_intelligence.ingest import verify_ingest, with_retries, write_ingest
from investment_committee.market_intelligence.providers.base import MarketDataError
from investment_committee.market_intelligence.providers.yfinance_provider import YFinanceProvider
from investment_committee.simulation.forex_backtest_study import YAHOO_CALENDAR, yahoo_week

ROOT = Path(__file__).resolve().parents[1]
SYMBOLS = ("EURUSD=X", "GBPUSD=X")
HOUR = timedelta(hours=1)
END = datetime(2026, 3, 23, 0, 0, tzinfo=timezone.utc)  # the first bar of the snapshot already inspected; exclusive
YAHOO_LIMIT_DAYS = 729  # Yahoo: "the requested range must be within the last 730 days" for 1h bars
PROVENANCE = {
    "provider": "Yahoo Finance (biblioteca yfinance)",
    "official_url": "https://finance.yahoo.com/quote/EURUSD=X/history/ y https://finance.yahoo.com/quote/GBPUSD=X/history/",
    "resolution": "1h",
    "price_kind": "MID_INDICATIVO: un solo precio por hora, sin bid ni ask; no es un precio ejecutable",
    "source_time_zone": "Europe/London; cada etiqueta llega con su desfase UTC y es la hora de APERTURA de la vela",
    "terms": ("Sin cuenta ni credenciales. Yahoo no publica una licencia de datos para esta vía; se usa solo para investigación personal y local, "
              "sin redistribuir los datos. Las condiciones de Yahoo no se han verificado cláusula por cláusula."),
    "calendar": YAHOO_CALENDAR,
    "weekends_and_holidays": "sin velas el fin de semana; los festivos no se modelan: aparecen como horas esperadas sin vela",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "raw" / "ingest")
    options = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    existing = sorted(p for p in options.out.glob("yahoo_fx_1h_*_20260323") if (p / "procedencia.json").exists()) if options.out.is_dir() else []
    if existing:
        record = verify_ingest(existing[0], timeframe=HOUR, calendar=yahoo_week)
        print(f"{existing[0].name}: ya existe y pasa la verificación; no se descarga nada.")
    else:
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=YAHOO_LIMIT_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        provider, originals = YFinanceProvider(), {}
        for symbol in SYMBOLS:
            received, _ = with_retries(lambda s=symbol: provider.get_intraday_range(s, "1h", start, END), attempts=3, delays=(5.0, 15.0), sleep=time.sleep,
                                       retry_on=(MarketDataError,))
            originals[symbol] = received.encode("utf-8")
        target = options.out / f"yahoo_fx_1h_{start:%Y%m%d}_{END:%Y%m%d}"
        record = write_ingest(target, originals, provenance=PROVENANCE, timeframe=HOUR, calendar=yahoo_week, fetched_at=datetime.now(timezone.utc),
                              requested_from=start, not_after=END)
        existing = [target]
        print(f"{target.name}: descargado e ingerido.")

    quality = json.loads((existing[0] / "calidad.json").read_text(encoding="utf-8"))
    print(f"Proveedor: {record['provider']} | descargado {record['downloaded_at']} | período {record['requested_from']} a {record['requested_to_exclusive']} (exclusivo)")
    print(f"Tipo de precio: {record['price_kind']}")
    print(f"Hash del dataset normalizado: {record['dataset_sha256']}")
    for symbol in SYMBOLS:
        o, n, q = record["originals"][symbol], record["normalized"][symbol], quality[symbol]
        print(f"\n[{symbol}]")
        print(f"  original   {o['file']}  {o['bytes']} bytes  sha256 {o['sha256']}")
        print(f"  normalizado {n['file']}  {n['bytes']} bytes  sha256 {n['sha256']}")
        print(f"  filas en el original {q['rows_in_original']} | velas conservadas {q['bars_kept']} | {q['first_bar_utc']} a {q['last_bar_utc']}")
        print(f"  horas esperadas {q['expected_open_market_bars']} | presentes {q['present']} | cobertura {q['coverage']} | faltantes {q['missing_bars']} en {q['gaps']} huecos")
        print(f"  problemas {q['problems']} | desfases UTC en las etiquetas {q['utc_offsets_in_labels']}")
        for gap in q["largest_gaps"][:5]:
            print(f"    hueco {gap['start']} -> {gap['end']} ({gap['missing']} velas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
