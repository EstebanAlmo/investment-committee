"""Spanish console report of a multi-month evaluation (scripts/replay_period.py).

Presentation only. Continuous and normalized results are shown in separate
sections and never added together. Every cost scenario is shown, with no
"best" one picked, and every instrument stays in the report.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from investment_committee.application.period import COST_SCENARIOS, MonthRow, PeriodReport

from .replay_render import NAMES
from .trading_render import num

POLICIES = {"A": "A (todas las elegibles)", "B": "B (solo la mejor)", "C": "C (solo validadas)"}


def _s(value: Decimal | None, places: int = 2) -> str:
    if value is None:
        return "-"
    return ("+" if value > 0 else "") + num(value, places)


def _pct(fraction: Decimal | None) -> str:
    return "-" if fraction is None else _s(fraction * 100) + " %"


def _rate(fraction: Decimal | None) -> str:
    return "-" if fraction is None else num(fraction * 100) + " %"


def _table(rows: Sequence[MonthRow]) -> list[str]:
    out = ["    mes      | inicio     | P&L USD   | rent.    | ops | aciertos | expect. | PF   | DD máx.  | costos | rech. riesgo"]
    for r in rows:
        out.append(
            f"    {r.month}  | {num(r.start_balance):>10} | {_s(r.pnl):>9} | {_pct(r.net_return):>8} | {r.trades:>3} | "
            f"{_rate(r.win_rate):>8} | {_s(r.expectancy):>7} | {num(r.profit_factor) if r.profit_factor is not None else '-':>4} | "
            f"{_pct(r.max_drawdown_fraction):>8} | {num(r.costs):>6} | {r.risk_rejections}"
        )
    return out


def render_period(report: PeriodReport) -> str:
    base = report.continuous["base"]
    capital = base.config.limits.capital
    lines = [
        f"Evaluación multiperiodo · meses {report.first_month} a {report.last_month}",
        "TIPO: backtest retrospectivo sobre meses ya observados. NO es validación fuera de muestra ni seguimiento prospectivo; "
        "meses positivos no hacen VALIDADA a la estrategia. Parámetros, reglas de riesgo y políticas sin cambios.",
        f"Mes = días de sesión FX (17:00 a 17:00 de Nueva York): {report.windows[0].start:%Y-%m-%d %H:%M} UTC a "
        f"{report.windows[-1].end:%Y-%m-%d %H:%M} UTC. Estrategia {base.strategy_name} v{base.strategy_version}.",
        "",
        "Datos (copia local reproducible):",
        f"  fuente: {report.manifest.source} · descargados {report.manifest.downloaded_at} · {report.snapshot_dir}",
        f"  hash combinado SHA-256: {report.manifest.combined_sha256}",
    ]
    for s in report.manifest.series:
        gaps = report.gaps.get(s.symbol, [])
        hours = sum(g.hours for g in gaps)
        longest = max(gaps, key=lambda g: g.hours, default=None)
        lines.append(
            f"  {NAMES.get(s.symbol, s.symbol)}: {s.bars} velas ({s.first_bar} a {s.last_bar}) · sha256 {s.sha256[:12]}… · velas previas al "
            f"inicio {report.warmup_bars[s.symbol]} (mínimo {report.required_warmup}) · huecos en horas de mercado: {len(gaps)} intervalos, "
            f"{hours} h" + (f" (el mayor: {longest.start:%Y-%m-%d %H:%M} UTC, {longest.hours} h)" if longest else "")
        )
    lines.append("  No se inventa ninguna vela: un hueco impide entrar o retrasa una salida a la siguiente vela existente.")

    lines += ["", f"1) MODO CONTINUO (el capital pasa de un mes al siguiente) · costos base · no operar = {num(capital)} USD todo el periodo"]
    for policy in ("A", "B", "C"):
        rows = [r for r in report.continuous_rows if r.scenario == "base" and r.policy == policy]
        result = next(x for x in base.results if x.policy.value == policy)
        lines += [f"  Política {POLICIES[policy]}:", *_table(rows)]
        lines.append(
            f"    TOTAL: capital final {num(result.final_balance)} USD ({_s(result.metrics.net_pnl)} USD, {_pct(result.net_return)}) · "
            f"{result.metrics.trades} ops · PF {num(result.metrics.profit_factor) if result.metrics.profit_factor is not None else '-'} · "
            f"DD máx. {_pct(result.max_drawdown_fraction)} · costos {num(result.metrics.total_costs)} USD · frente a no operar "
            f"{_s(result.final_balance - capital)} USD · meses negativos: {sum(1 for r in rows if r.pnl < 0)} de {len(rows)}"
        )
        lines.append("    Curva de equity acumulada (cierre de cada mes): " + " → ".join(f"{r.month} {num(r.end_balance, 0)}" for r in rows))

    lines += ["", "2) MODO NORMALIZADO (cada mes por separado desde 10.000 USD, costos base; no se puede sumar ni componer)"]
    for policy in ("A", "B", "C"):
        rows = [r for r in report.normalized_rows if r.policy == policy]
        lines += [f"  Política {POLICIES[policy]}:", *_table(rows)]
        positives = sum(1 for r in rows if r.pnl > 0)
        lines.append(f"    meses positivos {positives} · negativos {sum(1 for r in rows if r.pnl < 0)} · sin cambio {len(rows) - positives - sum(1 for r in rows if r.pnl < 0)}")

    lines += ["", "3) ROBUSTEZ A COSTOS (modo continuo; spread y deslizamiento multiplicados; se muestran todos los escenarios)"]
    for policy in ("A", "B"):
        parts = []
        base_pnl = None
        for name, factor in COST_SCENARIOS:
            result = next(x for x in report.continuous[name].results if x.policy.value == policy)
            pnl = result.metrics.net_pnl
            base_pnl = pnl if base_pnl is None else base_pnl
            months = [r for r in report.continuous_rows if r.scenario == name and r.policy == policy]
            parts.append(
                f"    costos ×{num(factor, 1)}: {_s(pnl)} USD ({_pct(result.net_return)}) · {result.metrics.trades} ops · PF "
                f"{num(result.metrics.profit_factor) if result.metrics.profit_factor is not None else '-'} · costos {num(result.metrics.total_costs)} · "
                f"meses negativos {sum(1 for r in months if r.pnl < 0)}/{len(months)}"
            )
        lines += [f"  Política {POLICIES[policy]}:", *parts]
        worst = next(x for x in report.continuous["x2"].results if x.policy.value == policy).metrics.net_pnl
        if base_pnl is not None and base_pnl > 0 and worst <= 0:
            lines.append("    → la ganancia del escenario base desaparece al duplicar los costos")
        elif base_pnl is not None and base_pnl <= 0:
            lines.append("    → ya con los costos base el resultado no es positivo")
    lines.append("  Política C: sin evidencia validada previa, no opera en ningún escenario.")

    lines += ["", "4) POR INSTRUMENTO (modo continuo, costos base; ningún instrumento se elimina ni se recalibra)"]
    for policy in ("A", "B"):
        rows = [r for r in report.continuous_rows if r.scenario == "base" and r.policy == policy]
        lines.append(f"  Política {POLICIES[policy]}:")
        totals = {s: sum((r.by_instrument[s] for r in rows), Decimal("0")) for s in report.symbols}
        for s in report.symbols:
            monthly = " ".join(f"{r.month[5:]}:{_s(r.by_instrument[s], 0)}" for r in rows)
            negative = sum(1 for r in rows if r.by_instrument[s] < 0)
            ops = sum(r.trades_by_instrument[s] for r in rows)
            lines.append(f"    {NAMES.get(s, s)}: {_s(totals[s])} USD en {ops} ops · meses negativos {negative}/{len(rows)} · {monthly}")
        total = sum(totals.values(), Decimal("0"))
        best = max(totals, key=lambda s: totals[s])
        lines.append(
            f"    total {_s(total)} USD · el mejor instrumento ({NAMES.get(best, best)}) aporta {_s(totals[best])} USD; "
            f"sin él el resultado sería {_s(total - totals[best])} USD"
        )

    lines += [
        "",
        "5) INTEGRIDAD ESTADÍSTICA",
        "  - Todo lo anterior es retrospectivo: estos meses ya se miraron (septiembre se auditó en detalle), así que ninguno sirve ya "
        "como prueba fuera de muestra. Ninguna estrategia se marca VALIDADA.",
        "  - Indicadores y decisiones usan solo velas cerradas antes de cada decisión; controles temporales y de riesgo auditados sin cambios.",
        "  - Un walk-forward posterior debería separar en el tiempo: desarrollo (meses ya vistos, p. ej. abril-septiembre 2026), "
        "validación (meses posteriores, fijados antes de verlos) y prueba final (seguimiento prospectivo de candidatas desde una fecha "
        "fijada de antemano), sin reoptimizar entre etapas. Esta fase no optimiza nada.",
    ]
    return "\n".join(lines)
