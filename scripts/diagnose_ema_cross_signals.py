"""Signal DETECTION of ema-rsi-atr-forex@0.1.0 over an existing Yahoo H1
snapshot - a diagnosis, NOT part of the automated tests, NOT a backtest.

It counts, hour by hour, what simulation.ema_cross.evaluate returns. There
are no entries, stops, targets or results here, and the prices are Yahoo's
single indicative series (QuoteKind.ESTIMATED), on months already inspected
in this project: nothing printed is evidence for or against the strategy.
It downloads nothing and writes nothing.

Two calendars are compared:
  nueva-york  the FX week used by the project (Sunday 17:00 to Friday 17:00
              New York);
  yahoo       the week observed in this snapshot (docs/data-layer.md): it
              opens two hours later. Used only to read this provider's data.

Usage, from the repository root:
    Windows:  .venv\\Scripts\\python.exe scripts\\diagnose_ema_cross_signals.py
    macOS:    .venv/bin/python scripts/diagnose_ema_cross_signals.py
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from investment_committee.market_intelligence.quotes import QuoteKind, to_utc
from investment_committee.market_intelligence.snapshots import DEFAULT_SNAPSHOT_ROOT, read_snapshot
from investment_committee.simulation.ema_cross import STRATEGY_ID, Outcome, evaluate, fingerprint
from investment_committee.simulation.sessions import is_week_open

HOUR = timedelta(hours=1)
SOURCE = "Yahoo Finance H1 (precio indicativo único, sin bid/ask)"


def yahoo_week(moment: datetime) -> bool:
    return is_week_open(moment) and is_week_open(moment - 2 * HOUR)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT_ROOT / "fx_1h_20260331_20260930")
    parser.add_argument("--simbolos", nargs="+", default=["EURUSD=X", "GBPUSD=X"])
    parser.add_argument("--ejemplos", type=int, default=2)
    args = parser.parse_args()

    manifest, data = read_snapshot(args.snapshot)
    print(f"{STRATEGY_ID} · parámetros {fingerprint()[:12]}… · snapshot {manifest.combined_sha256[:12]}… (verificado)")
    print("DIAGNÓSTICO de detección sobre precios ESTIMADOS y meses ya inspeccionados: sin entradas, sin resultados.\n")
    for name, calendar in (("nueva-york", is_week_open), ("yahoo", yahoo_week)):
        for symbol in args.simbolos:
            bars = sorted(data[symbol], key=lambda b: to_utc(b.timestamp))
            moment, end = to_utc(bars[0].timestamp) + HOUR, to_utc(bars[-1].timestamp) + HOUR
            counts: Counter[str] = Counter()
            examples: dict[str, list[str]] = {}
            while moment <= end:
                e = evaluate(bars, moment, symbol=symbol, data_kind=QuoteKind.ESTIMATED, data_source=SOURCE, is_open=calendar)
                counts[e.outcome.value if e.outcome is not Outcome.DATA_REJECTED else f"RECHAZADO:{e.reasons[0].value}"] += 1
                if e.outcome in (Outcome.LONG, Outcome.SHORT, Outcome.NOT_CONFIRMED) and len(examples.setdefault(e.outcome.value, [])) < args.ejemplos:
                    examples[e.outcome.value].append(
                        f"vela que cierra {e.candle_close:%Y-%m-%d %H:%M} UTC · EMA20 {e.ema_fast:.5f} / EMA50 {e.ema_slow:.5f} "
                        f"(vela anterior {e.previous_ema_fast:.5f} / {e.previous_ema_slow:.5f}) · RSI14 {e.rsi:.2f} · ATR14 {e.atr * 10000:.1f} pips")
                moment += HOUR
            print(f"[{name}] {symbol}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
            for outcome, lines in examples.items():
                for line in lines:
                    print(f"    {outcome}: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
