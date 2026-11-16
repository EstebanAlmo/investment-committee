"""Spanish console report of the strategy research (scripts/research_strategies.py).

Every strategy, policy, cost scenario, month and instrument is shown,
negative results included, next to "not trading". No strategy is picked
as a winner and none is called validated.
"""

from __future__ import annotations

from decimal import Decimal

from investment_committee.application.research import ResearchReport

from .replay_render import NAMES
from .trading_render import num


def _s(value: Decimal | None) -> str:
    return "-" if value is None else ("+" if value > 0 else "") + num(value)


def render_research(research: ResearchReport) -> str:
    any_report = next(iter(research.reports.values()))
    lines = [
        f"Investigación de estrategias · meses {any_report.first_month} a {any_report.last_month} (sesiones FX)",
        "DIAGNÓSTICO RETROSPECTIVO sobre meses ya inspeccionados: puede descartar una estrategia, nunca validarla. Parámetros fijados "
        "antes de correr ninguna estrategia V2; nada se ajustó a estos resultados. Ninguna estrategia está VALIDADA.",
        f"Datos: la misma copia local para todas (sha256 {research.data_sha256}). Capital 10.000 USD, mismos límites de riesgo y políticas.",
        "",
        "Totales por estrategia (modo continuo; no operar = 0,00 USD):",
        "  estrategia                          | pol. | costos | ops | P&L USD    | rent.    | PF   | DD máx.  | meses neg.",
    ]
    for strategy_id, report in research.reports.items():
        for scenario, rep in report.continuous.items():
            for result in rep.results:
                if result.policy.value == "C":
                    continue
                months = [r for r in report.continuous_rows if r.scenario == scenario and r.policy == result.policy.value]
                m = result.metrics
                lines.append(
                    f"  {strategy_id:<35} | {result.policy.value:^4} | {scenario:<6} | {m.trades:>3} | {_s(m.net_pnl):>10} | "
                    f"{_s(result.net_return * 100):>6} % | {num(m.profit_factor) if m.profit_factor is not None else '-':>4} | "
                    f"{_s(result.max_drawdown_fraction * 100):>6} % | {sum(1 for r in months if r.pnl < 0)}/{len(months)}"
                )
    lines.append("  Política C: no opera con ninguna estrategia (no hay evidencia validada previa).")

    lines += ["", "Por mes (continuo, costos base, P&L USD):"]
    for strategy_id, report in research.reports.items():
        for policy in ("A", "B"):
            rows = [r for r in report.continuous_rows if r.scenario == "base" and r.policy == policy]
            lines.append(f"  {strategy_id} {policy}: " + " · ".join(f"{r.month[5:]} {_s(r.pnl)} ({r.trades} op.)" for r in rows))

    lines += ["", "Por instrumento (continuo, costos base, P&L USD):"]
    for strategy_id, report in research.reports.items():
        for result in report.continuous["base"].results:
            if result.policy.value == "C":
                continue
            parts = [f"{NAMES.get(s, s)} {_s(x.net_pnl)} ({x.trades} op.)" for s, x in result.by_instrument.items()]
            lines.append(f"  {strategy_id} {result.policy.value}: " + " · ".join(parts))

    lines += [
        "",
        "Lectura: estos números describen el pasado ya visto. Para validar hace falta el protocolo pre-registrado "
        "(docs/strategy-research-v2.md): datos posteriores no usados para elegir nada.",
    ]
    return "\n".join(lines)
