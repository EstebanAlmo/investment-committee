"""Build the AI committee's EvidencePacket from data Python already computed.

Inputs: the deterministic analysis (daily bars, IndicatorSnapshot, the
/analyze committee decision), the simulated-trading strategy's live reading
for Forex (1-hour bars), and any macro documents a provider returned. Every
value here is copied from those results - the only calculation is the daily
20-bar support/resistance range, done by simulation.technical like the
intraday one. Missing values become explicit "not available" entries.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Sequence

from investment_committee.committee.ai import EvidenceItem, EvidencePacket, TradingSignalSummary
from investment_committee.market_intelligence.macro import ExternalDocument
from investment_committee.simulation import TradeSignal, recent_range

from .models import AnalysisResult

MAX_DAILY_AGE = timedelta(days=4)  # covers a weekend plus a holiday
DAILY_RANGE_BARS = 20

_ACTIONS = {"BUY": "COMPRAR", "SELL": "VENDER", "HOLD": "MANTENER"}
_STANCES = {"SUPPORT": "A FAVOR", "NEUTRAL": "NEUTRAL", "OPPOSE": "EN CONTRA"}
# What each deterministic vote means - a rules check, never a buy/sell call.
_VOTE_MEANINGS = {
    "TECHNICAL_ANALYST": {
        "SUPPORT": "sus reglas técnicas se cumplen",
        "NEUTRAL": "sus reglas técnicas están mixtas",
        "OPPOSE": "sus reglas técnicas no se cumplen",
    },
    "RISK_MANAGER": {
        "SUPPORT": "riesgo aceptable según sus reglas; no es una recomendación de compra",
        "NEUTRAL": "riesgo intermedio según sus reglas",
        "OPPOSE": "riesgo no aceptable según sus reglas",
    },
    "DEVILS_ADVOCATE": {
        "SUPPORT": "no encontró objeciones según sus reglas",
        "NEUTRAL": "encontró una objeción según sus reglas",
        "OPPOSE": "encontró varias objeciones según sus reglas",
    },
}
_ROLES = {"TECHNICAL_ANALYST": "analista técnico", "RISK_MANAGER": "gestor de riesgo", "DEVILS_ADVOCATE": "abogado del diablo"}
_TRENDS = {"UP": "alcista", "DOWN": "bajista", "SIDEWAYS": "lateral"}
_REASONS = {
    "INSUFFICIENT_DATA": "datos insuficientes",
    "STALE_DATA": "datos antiguos",
    "INCONSISTENT_DATA": "datos inconsistentes",
    "MARKET_CLOSED": "mercado Forex cerrado",
    "TREND_UP": "tendencia alcista (precio y SMA20 sobre la SMA50)",
    "TREND_DOWN": "tendencia bajista (precio y SMA20 bajo la SMA50)",
    "TREND_SIDEWAYS": "tendencia lateral",
    "MOMENTUM_CONFIRMS": "el RSI14 confirma el impulso",
    "MOMENTUM_NOT_CONFIRMING": "el RSI14 no confirma el impulso",
    "VOLATILITY_OK": "volatilidad (ATR14) dentro de la franja permitida",
    "VOLATILITY_TOO_LOW": "volatilidad demasiado baja",
    "VOLATILITY_TOO_HIGH": "volatilidad demasiado alta",
    "COSTS_TOO_HIGH": "costos demasiado altos frente al riesgo",
    "REWARD_RISK_TOO_LOW": "relación riesgo/beneficio insuficiente tras costos",
    "POSITION_TOO_SMALL": "la cuenta hipotética no alcanza el tamaño mínimo",
    "INSUFFICIENT_MARGIN": "margen insuficiente",
}


class _Items:
    def __init__(self) -> None:
        self.items: list[EvidenceItem] = []
        self.missing: list[str] = []

    def add(self, category: str, label: str, value: Decimal | str | None, source: str, as_of: datetime | None, unit: str = "", places: int = 4) -> None:
        if value is None:
            self.missing.append(f"{label} ({category}): no disponible, historial insuficiente")
            return
        self.items.append(EvidenceItem(f"E{len(self.items) + 1}", category, label, value, unit, source, as_of, places))


def build_evidence_packet(
    *,
    symbol: str,
    display_name: str,
    is_forex: bool,
    analysis: AnalysisResult,
    trade_signal: TradeSignal | None,
    trading_unavailable_reason: str | None,
    documents: Sequence[ExternalDocument],
    macro_source: str,
    now: datetime,
) -> EvidencePacket:
    out = _Items()
    snapshot, decision = analysis.indicator_snapshot, analysis.decision
    bars = analysis.market_snapshot.price_bars
    daily_as_of = analysis.market_snapshot.latest_price_bar.timestamp

    # No bar dated after the analysis moment may become evidence. The
    # provider already drops such bars; if one still arrives, nothing is
    # sent to the agents and the packet is critical (no model call is made).
    future = [
        f"{name}: la última vela está fechada {_utc(moment)}, después del momento del análisis ({_utc(now)})"
        for name, moment in (("velas diarias", daily_as_of), ("velas de 1 hora", trade_signal.data_as_of if trade_signal else None))
        if moment is not None and moment > now
    ]
    if future:
        return EvidencePacket(
            instrument=symbol,
            display_name=display_name,
            asset_class="forex" if is_forex else "acciones/ETF",
            generated_at=now,
            data_as_of=None,
            stale=False,
            freshness_note="datos con fecha futura rechazados: " + "; ".join(future),
            items=(),
            missing=[*future, "no se usaron datos de mercado porque alguna vela tenía fecha posterior al análisis"],
            critical_missing=True,
            trading_signal=None,
            macro_source=macro_source,
            documents=(),
        )
    daily = "técnico diario"
    daily_source = "Yahoo Finance · velas diarias"
    price_places = 5 if is_forex else 2

    out.add(daily, "precio de cierre", snapshot.latest_close, daily_source, daily_as_of, places=price_places)
    out.add(daily, "SMA20", snapshot.sma_20, daily_source, daily_as_of, places=price_places)
    out.add(daily, "SMA50", snapshot.sma_50, daily_source, daily_as_of, places=price_places)
    out.add(daily, "SMA200", snapshot.sma_200, daily_source, daily_as_of, places=price_places)
    out.add(daily, "RSI14", snapshot.rsi_14, daily_source, daily_as_of, places=2)
    out.add(daily, "rendimiento de 30 días", _percent(snapshot.return_30d), daily_source, daily_as_of, "%", 2)
    out.add(daily, "rendimiento de 90 días", _percent(snapshot.return_90d), daily_source, daily_as_of, "%", 2)
    out.add(daily, "rendimiento en lo corrido del año", _percent(snapshot.return_ytd), daily_source, daily_as_of, "%", 2)
    out.add(daily, "volatilidad anualizada", _percent(snapshot.annualized_volatility), daily_source, daily_as_of, "%", 2)
    out.add(daily, "caída máxima del último año", _percent(snapshot.maximum_drawdown), daily_source, daily_as_of, "%", 2)
    daily_range = recent_range(bars, DAILY_RANGE_BARS)
    out.add(daily, f"soporte (mínimo de {DAILY_RANGE_BARS} velas diarias)", daily_range[0] if daily_range else None, daily_source, daily_as_of, places=price_places)
    out.add(daily, f"resistencia (máximo de {DAILY_RANGE_BARS} velas diarias)", daily_range[1] if daily_range else None, daily_source, daily_as_of, places=price_places)

    committee = "comité determinístico (votos de reglas, no posturas del comité IA)"
    committee_source = "comité determinístico de reglas (/analyze)"
    out.add(committee, "decisión", _ACTIONS.get(decision.action.value, decision.action.value), committee_source, decision.generated_at)
    out.add(committee, "confianza interna (no es probabilidad)", decision.confidence, committee_source, decision.generated_at, places=2)
    for report in decision.agent_reports:
        vote = _STANCES.get(report.stance.value, report.stance.value)
        meaning = _VOTE_MEANINGS.get(report.role.value, {}).get(report.stance.value)
        out.add(
            committee,
            f"voto del {_ROLES.get(report.role.value, report.role.value)} determinístico",
            f"{vote} ({meaning})" if meaning else vote,
            committee_source,
            decision.generated_at,
        )

    summary = None
    freshness = [f"última vela diaria: {daily_as_of:%Y-%m-%d}"]
    if trade_signal is not None:
        reading = trade_signal.reading
        intraday = "técnico 1 hora"
        intraday_source = "Yahoo Finance · velas de 1 hora"
        as_of = trade_signal.data_as_of
        out.add(intraday, "precio (cierre de la última vela)", reading.quote_price, intraday_source, as_of, places=5)
        out.add(intraday, "SMA20", reading.sma_fast, intraday_source, as_of, places=5)
        out.add(intraday, "SMA50", reading.sma_slow, intraday_source, as_of, places=5)
        out.add(intraday, "RSI14", reading.rsi, intraday_source, as_of, places=2)
        out.add(intraday, "ATR14", reading.atr_pips, intraday_source, as_of, "pips", 1)
        out.add(intraday, "soporte (mínimo de 24 velas)", reading.support, intraday_source, as_of, places=5)
        out.add(intraday, "resistencia (máximo de 24 velas)", reading.resistance, intraday_source, as_of, places=5)
        out.add(intraday, "tendencia", _TRENDS.get(reading.trend.value) if reading.trend else None, intraday_source, as_of)

        strategy = "estrategia Python"
        strategy_name = f"{trade_signal.strategy_name} v{trade_signal.strategy_version}"
        parameters = trade_signal.parameters
        out.add(strategy, "stop-loss a N veces el ATR14", parameters.stop_atr_multiple, strategy_name, None, places=1)
        out.add(strategy, "take-profit a N veces el ATR14", parameters.target_atr_multiple, strategy_name, None, places=1)
        out.add(strategy, "riesgo por operación de la cuenta hipotética", trade_signal.account.risk_fraction * 100, strategy_name, None, "%", 2)
        out.add(strategy, "relación riesgo/beneficio mínima tras costos", parameters.min_net_reward_risk, strategy_name, None, places=1)
        out.add(strategy, "spread supuesto", trade_signal.costs.spread_pips, strategy_name, None, "pips", 1)
        plan = trade_signal.plan
        if plan is not None:
            out.add(strategy, "entrada de referencia", plan.entry_price, strategy_name, as_of, places=5)
            out.add(strategy, "stop-loss", plan.stop_loss, strategy_name, as_of, places=5)
            out.add(strategy, "take-profit", plan.take_profit, strategy_name, as_of, places=5)
            out.add(strategy, "relación riesgo/beneficio tras costos", plan.net_reward_risk, strategy_name, as_of, places=2)
            out.add(strategy, "riesgo monetario estimado", plan.risk_amount, strategy_name, as_of, "USD", 2)
        summary = TradingSignalSummary(
            direction=trade_signal.direction.value,
            reasons=tuple(_REASONS.get(r.value, r.value) for r in trade_signal.reasons),
            strategy=strategy_name,
        )
        if as_of is not None:
            minutes = int((now - as_of).total_seconds() // 60)
            freshness.append(f"última vela de 1 hora cerrada hace {minutes} min")
    elif is_forex:
        out.missing.append(f"datos de 1 hora y señal de la estrategia: {trading_unavailable_reason or 'no disponibles'}")
    else:
        out.missing.append("datos de 1 hora y señal de trading: la estrategia simulada solo cubre EUR/USD")

    if is_forex:
        out.missing.append("volumen: Yahoo Finance no lo publica para Forex")
    if not documents:
        out.missing.append("noticias, tasas de interés, inflación, empleo y calendario económico: no hay proveedor integrado")

    daily_age = now - (daily_as_of + timedelta(days=1))
    stale = daily_age > MAX_DAILY_AGE
    if stale:
        freshness.append("los datos diarios están DESACTUALIZADOS")
    data_as_of = max([d for d in (daily_as_of, trade_signal.data_as_of if trade_signal else None) if d is not None])
    critical = snapshot.latest_close is None or (snapshot.sma_50 is None and snapshot.rsi_14 is None)

    return EvidencePacket(
        instrument=symbol,
        display_name=display_name,
        asset_class="forex" if is_forex else "acciones/ETF",
        generated_at=now,
        data_as_of=data_as_of,
        stale=stale,
        freshness_note="; ".join(freshness),
        items=out.items,
        missing=out.missing,
        critical_missing=critical,
        trading_signal=summary,
        macro_source=macro_source,
        documents=tuple(documents),
    )


def _utc(moment: datetime) -> str:
    return f"{moment.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC"


def _percent(fraction: Decimal | None) -> Decimal | None:
    return None if fraction is None else fraction * 100
