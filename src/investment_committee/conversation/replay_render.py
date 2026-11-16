"""Spanish (es-CO) rendering of a historical replay (/simular_mes).

Presentation only: every figure was computed by simulation.replay. The
report states what kind of evidence it is (a retrospective backtest), puts
"not trading" next to every policy, and never names a winning policy.
"""

from __future__ import annotations

from datetime import timezone
from decimal import Decimal

from investment_committee.application import PolicyResult, ReplayReport

from .trading_render import num

POLICY_TITLES = {
    "A": "Política A — todas las candidatas que superan los controles",
    "B": "Política B — solo la mejor candidata del ranking en cada decisión",
    "C": "Política C — solo candidatas con evidencia histórica favorable previa y sin objeciones",
}
NAMES = {"EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD", "USDJPY=X": "USD/JPY"}
KIND_NOTICE = (
    "TIPO DE EVIDENCIA: backtest retrospectivo sobre datos ya conocidos. No es validación fuera de muestra ni "
    "seguimiento prospectivo, y comparar las políticas después de verlas no demuestra que alguna sea rentable."
)


def _signed(value: Decimal) -> str:
    return ("+" if value > 0 else "") + num(value)


def _pct(fraction: Decimal) -> str:
    return ("+" if fraction > 0 else "") + num(fraction * 100) + " %"


def render_replay(report: ReplayReport) -> str:
    config, limits = report.config, report.config.limits
    lines = [
        f"Simulación histórica · {config.start:%Y-%m-%d} a {config.end:%Y-%m-%d} (UTC, fin excluido)",
        KIND_NOTICE,
        "",
        f"Estrategia: {report.strategy_name} v{report.strategy_version} (base experimental, sin evidencia favorable) · velas de 1 hora",
        f"Cuenta hipotética: {num(limits.capital)} USD · riesgo por operación {num(limits.risk_per_trade * 100)} % · pérdida diaria máx. "
        f"{num(limits.max_daily_loss * 100)} % · exposición compartida al USD máx. {num(limits.max_usd_direction_risk * 100)} %",
        f"Reglas: entrada a la apertura de la vela siguiente a la decisión, con spread y deslizamiento; stop y objetivo en la misma "
        f"vela se resuelven como stop; sin entradas desde las {config.sessions.last_entry:%H:%M} de Nueva York y todo cerrado a las "
        f"{config.sessions.flat_by:%H:%M} de Nueva York (horario de verano incluido); festivos no modelados.",
        "Datos: " + "; ".join(
            f"{NAMES.get(c.symbol, c.symbol)} {c.bars_in_period} velas (horas de mercado sin vela: {c.open_hours_without_bar})" for c in report.coverage
        ),
        f"Momentos de decisión: {report.decision_moments} ({report.entry_decision_moments} con entradas permitidas)",
        f"Comité de IA: no se usaron decisiones de DeepSeek; registros contemporáneos aplicados: {report.ai_decisions_used}",
        "",
        f"No operar (referencia): {num(report.no_trade_baseline)} USD · 0,00 %",
    ]
    for result in report.results:
        lines += ["", *_policy_block(result)]
    lines += [
        "",
        "Aviso: simulación con precios medios indicativos de Yahoo Finance; el spread y el deslizamiento son supuestos. No se "
        "envió ninguna orden ni se abrió ninguna operación simulada en el diario. Resultado pasado, no garantiza nada futuro.",
    ]
    return "\n".join(lines)


def _policy_block(result: PolicyResult) -> list[str]:
    m = result.metrics
    lines = [POLICY_TITLES[result.policy.value]]
    if m.trades == 0:
        reasons = "; ".join(f"{k}: {v}" for k, v in sorted(result.rejection_counts.items(), key=lambda kv: -kv[1])[:4])
        lines.append(f"  Sin operaciones: capital final {num(result.final_balance)} USD (igual que no operar). Motivos: {reasons or 'no hubo señales'}")
        return lines
    pf = num(m.profit_factor) if m.profit_factor is not None else "sin pérdidas"
    lines += [
        f"  Capital final {num(result.final_balance)} USD ({_signed(m.net_pnl)} USD, {_pct(result.net_return)}) · "
        f"frente a no operar: {_signed(result.final_balance - result.initial_capital)} USD",
        f"  Operaciones {m.trades}: {m.wins} ganadoras, {m.losses} perdedoras"
        + (f", {m.trades - m.wins - m.losses} en cero" if m.trades - m.wins - m.losses else "")
        + " · expectativa neta en ESTE periodo "
        f"{_signed(m.expectancy or Decimal('0'))} USD por operación (no validada) · profit factor {pf}",
        f"  Drawdown máximo {num(result.max_drawdown)} USD ({_pct(result.max_drawdown_fraction)}) · costos totales "
        f"{num(m.total_costs)} USD (ya incluidos en el resultado)",
        f"  Exposición simultánea máxima: {result.max_open_positions} posiciones · riesgo abierto {num(result.max_open_risk)} USD · "
        f"nocional {num(result.max_notional)} USD",
        "  Salidas: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in sorted(m.exits.items()))
        + (f" · stop y objetivo en la misma vela (resuelto como stop): {m.same_bar_conflicts}" if m.same_bar_conflicts else ""),
        "  Por instrumento: " + "; ".join(
            f"{NAMES.get(s, s)} {x.trades} op. {_signed(x.net_pnl)} USD" for s, x in result.by_instrument.items()
        ),
        "  Por estrategia: " + "; ".join(f"{s} {x.trades} op. {_signed(x.net_pnl)} USD" for s, x in result.by_strategy.items()),
        "  Resultados diarios (día de sesión, cierre 17:00 NY): " + "; ".join(f"{d:%m-%d} {_signed(v)}" for d, v in result.daily.items()),
        "  Curva de equity (último valor de cada día): " + "; ".join(_daily_equity(result)),
    ]
    if result.rejection_counts:
        lines.append("  Rechazos: " + "; ".join(f"{k} {v}" for k, v in sorted(result.rejection_counts.items(), key=lambda kv: -kv[1])))
    return lines


def _daily_equity(result: PolicyResult) -> list[str]:
    last: dict[str, Decimal] = {}
    for point in result.equity_curve:
        last[f"{point.at.astimezone(timezone.utc):%m-%d}"] = point.equity
    return [f"{day} {num(value, 0)}" for day, value in last.items()]
