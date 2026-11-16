"""Signal DETECTION of ema-3-9-20-50-120-forex@0.1.0 over an existing Yahoo
H1 snapshot - a diagnosis, NOT part of the automated tests, NOT a backtest.

It counts, hour by hour, what simulation.ema_trend_cross.evaluate returns,
and next to it what simulation.ema_cross.evaluate (ema-rsi-atr-forex@0.1.0)
returns for the same hours, to compare how often each one signals. There
are no entries, stops, targets, trades, profits or losses here, and the
prices are Yahoo's single indicative series (QuoteKind.ESTIMATED), on months
already inspected in this project: nothing printed is evidence for or
against either strategy. It downloads nothing and writes nothing.

Calendar: the week observed in this snapshot (docs/data-layer.md), which
opens two hours later than the New York convention. With the New York
calendar every evaluation of this provider is rejected as incomplete
(docs/ema-rsi-atr-signals.md), so only the provider's own calendar is used.

Each evaluation receives the bars closed before its moment, limited to the
most recent 1 000 (340 for the 300-hour strategy): all a window of 720 (300)
expected hours can read unless more than 280 (40) of its hours had no bar,
a case that is rejected either way.

Usage, from the repository root (it takes a few minutes):
    Windows:  .venv\\Scripts\\python.exe scripts\\diagnose_ema_trend_cross_signals.py
    macOS:    .venv/bin/python scripts/diagnose_ema_trend_cross_signals.py
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable, Sequence

from investment_committee.domain import PriceBar
from investment_committee.market_intelligence.quotes import QuoteKind, to_utc
from investment_committee.market_intelligence.snapshots import DEFAULT_SNAPSHOT_ROOT, read_snapshot
from investment_committee.simulation import ema_cross, ema_trend_cross
from investment_committee.simulation.ema_trend_cross import Reason
from investment_committee.simulation.sessions import is_week_open
from investment_committee.simulation.signals import Outcome

HOUR = timedelta(hours=1)
TICK = Decimal("0.00001")  # a tenth of a pip
SOURCE = "Yahoo Finance H1 (precio indicativo único, sin bid/ask)"
SIGNALS = (Outcome.LONG, Outcome.SHORT)


def yahoo_week(moment: datetime) -> bool:
    return is_week_open(moment) and is_week_open(moment - 2 * HOUR)


def diagnose(symbol: str, bars: Sequence[PriceBar], calendar: Callable[[datetime], bool]) -> dict[str, object]:
    """Counts of both strategies over every hour from the first bar's close
    to the last bar's close. Detection only."""
    bars = sorted(bars, key=lambda b: to_utc(b.timestamp))
    labels = [to_utc(b.timestamp) for b in bars]
    moment, end, upto = labels[0] + HOUR, labels[-1] + HOUR, 0
    b_counts: Counter[str] = Counter()
    a_counts: Counter[str] = Counter()
    a_common: Counter[str] = Counter()
    b_monthly: Counter[str] = Counter()
    a_monthly: Counter[str] = Counter()
    evaluable_monthly: Counter[str] = Counter()
    cross_gaps: list[Decimal] = []
    signal_gaps: list[Decimal] = []
    signal_trend_gaps: list[Decimal] = []
    examples: dict[str, list[str]] = {}
    hours = 0
    while moment <= end:
        while upto < len(bars) and labels[upto] + HOUR <= moment:
            upto += 1
        hours += 1
        b = ema_trend_cross.evaluate(bars[max(0, upto - 1000):upto], moment, symbol=symbol, data_kind=QuoteKind.ESTIMATED, data_source=SOURCE, is_open=calendar)
        a = ema_cross.evaluate(bars[max(0, upto - 340):upto], moment, symbol=symbol, data_kind=QuoteKind.ESTIMATED, data_source=SOURCE, is_open=calendar)
        month = f"{b.candle_close:%Y-%m}"
        a_counts[f"RECHAZADO:{a.reasons[0].value}" if a.outcome is Outcome.DATA_REJECTED else a.outcome.value] += 1
        if a.outcome in SIGNALS:
            a_monthly[month] += 1
        if b.outcome is Outcome.DATA_REJECTED:
            b_counts[f"RECHAZADO:{b.reasons[0].value}"] += 1
            moment += HOUR
            continue
        b_counts["EVALUABLE"] += 1
        evaluable_monthly[month] += 1
        b_counts[b.outcome.value] += 1
        a_common["EVALUABLE" if a.outcome is not Outcome.DATA_REJECTED else "RECHAZADO"] += 1
        if a.outcome in SIGNALS:
            a_common[a.outcome.value] += 1
        if b.outcome is Outcome.NO_CROSS:
            moment += HOUR
            continue
        assert b.ema_cross_fast is not None and b.ema_cross_slow is not None and b.ema_trend_short is not None
        assert b.ema_trend_mid is not None and b.ema_trend_long is not None and b.rsi is not None and b.atr is not None
        reasons = set(b.reasons)
        b_counts["CRUCE_ALCISTA" if Reason.CROSS_UP in reasons else "CRUCE_BAJISTA"] += 1
        gap = abs(b.ema_cross_fast - b.ema_cross_slow)
        cross_gaps.append(gap)
        trend_fails, rsi_fails = Reason.TREND_NOT_ALIGNED in reasons, Reason.RSI_DOES_NOT_CONFIRM in reasons
        if b.outcome in SIGNALS:
            b_monthly[month] += 1
            signal_gaps.append(gap)
            signal_trend_gaps.append(min(abs(b.ema_trend_short - b.ema_trend_mid), abs(b.ema_trend_mid - b.ema_trend_long)))
            kind = b.outcome.value
        else:
            kind = "SOLO_TENDENCIA" if trend_fails and not rsi_fails else "SOLO_RSI" if rsi_fails and not trend_fails else "TENDENCIA_Y_RSI"
            b_counts[f"NO_CONFIRMADO:{kind}"] += 1
        if trend_fails:
            b_counts["RECHAZO_POR_TENDENCIA"] += 1
        if rsi_fails:
            b_counts["RECHAZO_POR_RSI"] += 1
        if len(examples.setdefault(kind, [])) < 2:
            examples[kind].append(
                f"vela que cierra {b.candle_close:%Y-%m-%d %H:%M} UTC · EMA3 {b.ema_cross_fast:.5f} / EMA9 {b.ema_cross_slow:.5f} "
                f"(antes {b.previous_ema_cross_fast:.5f} / {b.previous_ema_cross_slow:.5f}) · EMA20 {b.ema_trend_short:.5f} / EMA50 {b.ema_trend_mid:.5f} / "
                f"EMA120 {b.ema_trend_long:.5f} · RSI14 {b.rsi:.2f} · ATR14 {b.atr * 10000:.1f} pips")
        moment += HOUR
    return dict(
        symbol=symbol, hours=hours, first=labels[0], last=labels[-1] + HOUR, b=b_counts, a=a_counts, a_common=a_common, b_monthly=b_monthly, a_monthly=a_monthly,
        evaluable_monthly=evaluable_monthly, cross_gaps=cross_gaps, signal_gaps=signal_gaps, signal_trend_gaps=signal_trend_gaps, examples=examples,
    )


def _below(values: Sequence[Decimal], limit: Decimal) -> int:
    return sum(1 for value in values if value < limit)


def report(result: dict[str, object]) -> list[str]:
    b, a, common = result["b"], result["a"], result["a_common"]
    assert isinstance(b, Counter) and isinstance(a, Counter) and isinstance(common, Counter)
    gaps, signal_gaps, trend_gaps = result["cross_gaps"], result["signal_gaps"], result["signal_trend_gaps"]
    assert isinstance(gaps, list) and isinstance(signal_gaps, list) and isinstance(trend_gaps, list)
    crosses, signals = b["CRUCE_ALCISTA"] + b["CRUCE_BAJISTA"], b["LONG"] + b["SHORT"]
    rejected = {key.split(":", 1)[1]: count for key, count in sorted(b.items()) if key.startswith("RECHAZADO:")}
    lines = [
        f"== {result['symbol']} · {result['first']:%Y-%m-%d %H:%M} a {result['last']:%Y-%m-%d %H:%M} UTC · {result['hours']} horas recorridas ==",
        f"  B  horas evaluables: {b['EVALUABLE']}",
        "  B  no evaluables: " + ", ".join(f"{name} {count}" for name, count in rejected.items()),
        f"  B  cruces EMA3/EMA9: {crosses} (alcistas {b['CRUCE_ALCISTA']}, bajistas {b['CRUCE_BAJISTA']}) · sin cruce {b['SIN_CRUCE']}",
        f"  B  señales confirmadas: {signals} (LONG {b['LONG']}, SHORT {b['SHORT']})",
        f"  B  cruces no confirmados: {b['CRUCE_SIN_CONFIRMACION']} (solo tendencia {b['NO_CONFIRMADO:SOLO_TENDENCIA']}, solo RSI {b['NO_CONFIRMADO:SOLO_RSI']}, "
        f"tendencia y RSI {b['NO_CONFIRMADO:TENDENCIA_Y_RSI']})",
        f"  B  rechazos por tendencia: {b['RECHAZO_POR_TENDENCIA']} · rechazos por RSI: {b['RECHAZO_POR_RSI']} (un cruce puede fallar ambos)",
    ]
    if gaps:
        lines.append(
            f"  B  separación |EMA3 - EMA9| en la vela del cruce: menos de 0,1 pip en {_below(gaps, TICK)} de {len(gaps)} cruces, menos de 0,01 pip en "
            f"{_below(gaps, TICK / 10)}; mínima {min(gaps):.8f}, mediana {sorted(gaps)[len(gaps) // 2]:.6f}")
    if signal_gaps:
        lines.append(
            f"  B  entre las señales confirmadas: menos de 0,1 pip en {_below(signal_gaps, TICK)} de {len(signal_gaps)}; separación mínima entre EMA20/EMA50/EMA120 "
            f"menor de 0,1 pip en {_below(trend_gaps, TICK)}, menor de 1 pip en {_below(trend_gaps, TICK * 10)}")
    months = sorted(set(result["evaluable_monthly"]) | set(result["a_monthly"]))  # type: ignore[call-overload]
    lines.append("  Señales por mes (cierre de la vela, UTC) - mes: horas evaluables de B · señales de B · señales de A")
    for month in months:
        lines.append(f"    {month}: {result['evaluable_monthly'][month]:>4} · B {result['b_monthly'][month]:>3} · A {result['a_monthly'][month]:>3}")  # type: ignore[index]
    per_100 = (lambda count, base: f"{100 * count / base:.2f}" if base else "n/d")
    a_evaluable = sum(count for key, count in a.items() if not key.startswith("RECHAZADO:"))
    a_signals, a_common_signals = a["LONG"] + a["SHORT"], common["LONG"] + common["SHORT"]
    lines += [
        f"  A  en todas sus horas evaluables ({a_evaluable}): {a_signals} señales (LONG {a['LONG']}, SHORT {a['SHORT']}) = {per_100(a_signals, a_evaluable)} por 100 horas",
        f"  A  en las horas evaluables de B donde A también lo es ({common['EVALUABLE']}): {a_common_signals} señales = {per_100(a_common_signals, common['EVALUABLE'])} por 100 horas",
        f"  B  en sus horas evaluables ({b['EVALUABLE']}): {signals} señales = {per_100(signals, b['EVALUABLE'])} por 100 horas; cruces brutos {per_100(crosses, b['EVALUABLE'])} por 100 horas",
    ]
    for kind, examples in result["examples"].items():  # type: ignore[union-attr]
        for line in examples:
            lines.append(f"    {kind}: {line}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT_ROOT / "fx_1h_20260331_20260930")
    parser.add_argument("--simbolos", nargs="+", default=["EURUSD=X", "GBPUSD=X"])
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    manifest, data = read_snapshot(args.snapshot)
    print(f"B = {ema_trend_cross.STRATEGY_ID} · parámetros {ema_trend_cross.fingerprint()[:12]}…")
    print(f"A = {ema_cross.STRATEGY_ID} · parámetros {ema_cross.fingerprint()[:12]}…")
    print(f"snapshot {manifest.combined_sha256[:12]}… (verificado) · calendario observado en Yahoo · datos {QuoteKind.ESTIMATED.value}")
    print("DIAGNÓSTICO de detección sobre precios ESTIMADOS y meses ya inspeccionados: sin entradas, sin operaciones, sin resultados.\n")
    for symbol in args.simbolos:
        print("\n".join(report(diagnose(symbol, data[symbol], yahoo_week))), end="\n\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
