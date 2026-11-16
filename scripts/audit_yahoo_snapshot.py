"""Data-quality diagnosis of an existing Yahoo H1 snapshot - NOT part of the
automated tests. Downloads nothing: it reads a snapshot already on disk
(its hashes are verified), normalizes the timestamps to UTC and runs the
provider-independent checks of market_intelligence.quality against the FX
week calendar (simulation.sessions.is_week_open).

Yahoo FX bars are a single indicative price series with no bid/ask. This
report describes them as such; nothing here is, or becomes, bid/ask data.

Usage, from the repository root:
    Windows:  .venv\\Scripts\\python.exe scripts\\audit_yahoo_snapshot.py
    macOS:    .venv/bin/python scripts/audit_yahoo_snapshot.py
    Options:  --snapshot data/raw/snapshots/<name>   --simbolos EURUSD=X GBPUSD=X
Output: a summary on screen and data/reports/data_quality/<snapshot>.json
(git-ignored).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median

from investment_committee.market_intelligence.quality import audit_bars, percentile
from investment_committee.market_intelligence.snapshots import DEFAULT_SNAPSHOT_ROOT, read_snapshot
from investment_committee.simulation.sessions import is_week_open

HOUR = timedelta(hours=1)
PIP = {"USDJPY=X": Decimal("0.01")}
DEFAULT_SNAPSHOT = DEFAULT_SNAPSHOT_ROOT / "fx_1h_20260331_20260930"


def _decimals(value: Decimal) -> int:
    return max(0, -value.as_tuple().exponent)  # type: ignore[operator]


def diagnose(symbol: str, bars) -> dict:  # type: ignore[no-untyped-def, type-arg]
    pip = PIP.get(symbol, Decimal("0.0001"))
    audit = audit_bars(bars, HOUR, is_week_open)
    trusted = audit.bars
    ranges = [(b.high - b.low) / pip for b in trusted]
    jumps = [abs(later.open - earlier.close) / pip for earlier, later in zip(trusted, trusted[1:]) if later.timestamp - earlier.timestamp == HOUR]
    prices = [p for b in bars for p in (b.open, b.high, b.low, b.close)]
    return {
        "bars_received": audit.received, "distinct_instants": audit.unique, "trusted_bars": len(trusted),
        "utc_offsets_in_labels": list(audit.utc_offsets), "first_bar_utc": audit.first.isoformat() if audit.first else None,
        "last_bar_utc": audit.last.isoformat() if audit.last else None, "problems": dict(audit.problems),
        "expected_open_market_bars": audit.expected, "present": audit.present, "coverage": str(audit.coverage),
        "missing_bars": sum(g.missing for g in audit.gaps), "gaps": len(audit.gaps),
        "largest_gaps": [{"start": g.start.isoformat(), "end": g.end.isoformat(), "missing": g.missing}
                         for g in sorted(audit.gaps, key=lambda g: (-g.missing, g.start))[:10]],
        "range_pips": {"median": str(round(median(ranges), 2)), "p95": str(round(percentile(ranges, Decimal("0.95")), 2)), "max": str(round(max(ranges), 2))},
        "open_vs_previous_close_pips": {"median": str(round(median(jumps), 3)), "p95": str(round(percentile(jumps, Decimal("0.95")), 3)),
                                        "max": str(round(max(jumps), 2)), "exactly_zero_share": str(round(Decimal(sum(1 for j in jumps if j == 0)) / len(jumps), 4))},
        "prices_with_more_than_5_decimals_share": str(round(Decimal(sum(1 for p in prices if _decimals(p) > 5)) / len(prices), 4)),
        "volume_always_zero": all(b.volume == 0 for b in bars),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--simbolos", nargs="+", default=["EURUSD=X", "GBPUSD=X"])
    args = parser.parse_args()

    manifest, bars_by_symbol = read_snapshot(args.snapshot)  # raises if any file differs from its recorded hash
    report = {
        "kind": "DIAGNOSTICO_DE_DATOS (precio indicativo único de Yahoo; no hay bid/ask)", "snapshot": args.snapshot.name, "source": manifest.source,
        "downloaded_at": manifest.downloaded_at, "combined_sha256": manifest.combined_sha256, "calendar": "semana Forex: domingo 17:00 a viernes 17:00 de Nueva York",
        "symbols": {symbol: diagnose(symbol, bars_by_symbol[symbol]) for symbol in args.simbolos},
    }
    out = Path("data") / "reports" / "data_quality"
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{args.snapshot.name}.json"
    target.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"Snapshot {args.snapshot.name} · {manifest.source}\nSHA-256 combinado {manifest.combined_sha256} (verificado)")
    for symbol, d in report["symbols"].items():  # type: ignore[union-attr]
        print(f"\n{symbol}: {d['bars_received']} velas recibidas, {d['trusted_bars']} utilizables · {d['first_bar_utc']} a {d['last_bar_utc']}")
        print(f"  desfases UTC en las etiquetas: {', '.join(d['utc_offsets_in_labels'])} · problemas: {d['problems'] or 'ninguno'}")
        print(f"  cobertura de mercado abierto: {d['present']}/{d['expected_open_market_bars']} ({Decimal(d['coverage']) * 100:.2f} %) · "
              f"{d['missing_bars']} velas faltantes en {d['gaps']} huecos")
        for gap in d["largest_gaps"][:5]:
            print(f"    hueco {gap['start']} -> {gap['end']} ({gap['missing']} velas)")
        print(f"  rango por vela (pips): mediana {d['range_pips']['median']}, p95 {d['range_pips']['p95']}, máx {d['range_pips']['max']}")
        j = d["open_vs_previous_close_pips"]
        print(f"  apertura vs cierre anterior (pips): mediana {j['median']}, p95 {j['p95']}, máx {j['max']}; idénticos {Decimal(j['exactly_zero_share']) * 100:.1f} %")
        print(f"  precios con más de 5 decimales: {Decimal(d['prices_with_more_than_5_decimals_share']) * 100:.1f} % · volumen siempre 0: {d['volume_always_zero']}")
    print(f"\nInforme: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
