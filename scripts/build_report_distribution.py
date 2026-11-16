"""Write the folder of reports to hand over, data/reports/Reportes_Trading:
an index page and one clearly named offline report per strategy and
scenario.

    python scripts/build_report_distribution.py

Reads only. No backtest is run, nothing is downloaded, and no original is
moved, renamed or rewritten: the reports are built again from the verified
studies (every hash checked) and then compared, trade by trade, with the
reports exported earlier, whose files are left as they are. See
docs/trade-viewer.md.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from investment_committee.interfaces.trade_viewer.offline_index import INDEX_NAME, ReportSpec, build_distribution, same_trades
from investment_committee.interfaces.trade_viewer.study_store import StudyError, find_snapshot, load_study
from investment_committee.simulation import ema_cross, ema_pullback, ema_trend_cross

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "data" / "reports"
AB = "fx_1h_20260331_20260930_107957e7_e6cf138"
C = "d2_ema-pullback-forex-0.1.0_ad7ef187_41706d5"
C_BARS = ROOT / "data" / "raw" / "ingest" / "yahoo_fx_1h_20241114_20260323" / "normalizado"
# distribution file -> (the report exported earlier that it must agree with, the count of valid closed trades it must hold)
EXPECTED = {
    "Estrategia_A_Cruce_EMA_2026.html": (REPORTS / "exports" / AB / "reporte_offline" / "investment_committee_trades.html", 60),
    "Estrategia_B_Cruce_Rapido_2026.html": (REPORTS / "exports" / AB / "reporte_offline" / "investment_committee_trades.html", 111),
    "Estrategia_C_Retrocesos_2025-2026_Base.html": (REPORTS / "exports" / C / "reporte_offline_base" / "strategy_C_trades_base.html", 90),
    "Estrategia_C_Retrocesos_2025-2026_Costos_Altos.html": (REPORTS / "exports" / C / "reporte_offline_costos_x1_5" / "strategy_C_trades_costos_x1_5.html", 88),
}


def _hashes(paths: list[Path]) -> dict[Path, str]:
    return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths if path.is_file()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=REPORTS / "Reportes_Trading")
    options = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # the same text on a Windows console and on macOS

    originals = sorted({path for path, _ in EXPECTED.values()} | {p for name in (AB, C) for p in (REPORTS / "backtests" / name).rglob("*") if p.is_file()})
    before = _hashes(originals)
    try:
        ab_snapshot = find_snapshot(REPORTS / "backtests" / AB, ROOT / "data" / "raw" / "snapshots")
        if ab_snapshot is None:
            raise StudyError("no se encontró el snapshot del estudio de A y B")
        ab, c = load_study(REPORTS / "backtests" / AB, ab_snapshot), load_study(REPORTS / "backtests" / C, C_BARS)
        names = list(EXPECTED)
        specs = [ReportSpec(names[0], ab, "COMUN", "BASE", ema_cross.STRATEGY_ID), ReportSpec(names[1], ab, "COMUN", "BASE", ema_trend_cross.STRATEGY_ID),
                 ReportSpec(names[2], c, "COMPLETO", "BASE", ema_pullback.STRATEGY_ID), ReportSpec(names[3], c, "COMPLETO", "COSTOS_X1_5", ema_pullback.STRATEGY_ID)]
        files, entries, payloads = build_distribution(specs)
        for entry, payload in zip(entries, payloads):
            original, count = EXPECTED[entry["file"]]
            if entry["n"] != count:
                raise StudyError(f"{entry['file']} tiene {entry['n']} operaciones cerradas válidas y se esperaban {count}")
            if not original.is_file():
                raise StudyError(f"falta el reporte original con el que comparar {entry['file']}")
            entry["same_as_original"] = same_trades(payload, original.read_text(encoding="utf-8"))
    except StudyError as error:
        print(f"No se generó la distribución: {error}", file=sys.stderr)
        return 1
    if _hashes(originals) != before:
        print("Un artefacto original cambió durante la generación: no se escribe nada.", file=sys.stderr)
        return 1

    options.out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (options.out / name).write_bytes(text.encode("utf-8"))
    print(f"Carpeta: {options.out}")
    print(f"  {INDEX_NAME}: {len(files[INDEX_NAME].encode('utf-8')):,} bytes")
    for entry in entries:
        print(f"  {entry['file']}: {entry['bytes']:,} bytes · {entry['n']} cerradas válidas · {entry['same_as_original']} operaciones idénticas al "
              f"reporte original · SHA-256 {entry['sha256']}")
    print(f"Artefactos originales comprobados sin cambios: {len(before)} archivos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
