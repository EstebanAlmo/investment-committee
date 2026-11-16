"""ResponseSummarizer port and its deterministic baseline, TemplateResponseSummarizer.

See docs/specs/spec-006-conversational-interface.md S17.
LLMResponseSummarizer and FallbackResponseSummarizer (the optional LLM
path) are not built - the deterministic Telegram MVP renders every reply
from templates alone.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from typing import Sequence

from investment_committee.application import (
    AICommitteeReport,
    AlertCheck,
    Lesson,
    LessonCatalog,
    OpportunityScan,
    PortfolioView,
    ReplayReport,
    SegmentedBacktest,
    TrackerView,
    TradeExplanation,
    TradeJournalView,
    TradeSignal,
)
from investment_committee.memory import DecisionRecord

from .ai_render import render_ai_committee
from .localization import (
    action_label,
    decision_adjustment,
    format_confidence,
    format_decimal,
    format_evidence_value,
    role_label,
    severity_label,
    stance_label,
    translate_interpretation,
    translate_report_summary,
)
from .models import Intent, IntentType
from .monitoring_render import render_alerts, render_portfolio, render_tracker
from .opportunities_render import render_opportunity_scan
from .replay_render import render_replay
from .symbol_resolver import canonical_symbol
from .tools.base import HelpContent, NotYetAvailable, PolicyDeclined, ToolOutcome
from .trading_render import (
    render_backtest,
    render_lesson,
    render_lesson_catalog,
    render_trade_explanation,
    render_trade_journal,
    render_trade_signal,
)

_DISCLAIMER = (
    "Aviso: análisis educativo, no es asesoría financiera. No se ejecutó ninguna operación "
    "y el sistema no está conectado a ningún bróker."
)
_CONFIDENCE_NOTE = (
    "Sobre la confianza: es una puntuación interna de qué tan de acuerdo están las reglas del "
    "comité. Una confianza del 90 % no significa un 90 % de probabilidad de obtener ganancias."
)
_COVERAGE_KEY = "checklist_coverage"
_TALLY_PHRASES = ("how many", "cuántas", "cuantas", "cuántos", "cuantos")


class ResponseSummarizer(ABC):
    @abstractmethod
    def summarize(self, outcome: ToolOutcome, intent: Intent) -> str:
        """Turn an already-computed ToolOutcome into human-readable text.

        Never computes a new number, never fetches anything, never
        introduces a claim not already present on `outcome` - see "Tool
        Routing" constraint (Epic prompt) and the LLM-backed variant's
        contract (S17).
        """
        raise NotImplementedError


class TemplateResponseSummarizer(ResponseSummarizer):
    """Deterministic, plain-text rendering in Spanish (es-CO, see
    localization.py): one branch per ToolOutcome member. Plain text (no
    Markdown) so no channel has to escape anything. A tuple of exactly two
    records is ambiguous by type alone (history of two vs. a comparison), so
    intent.type decides which it is. Shows only values already present on
    the outcome - no price, level, or target the engine did not compute.
    """

    def summarize(self, outcome: ToolOutcome, intent: Intent) -> str:
        match outcome:
            case DecisionRecord():
                return _render_record(outcome, with_evidence_values=intent.type is IntentType.EXPLAIN_DECISION)
            case HelpContent():
                return _render_help(outcome, intent)
            case NotYetAvailable():
                return f"{outcome.feature_name} todavía no está disponible. {outcome.message}"
            case PolicyDeclined():
                return outcome.message
            case TradeSignal():
                return render_trade_signal(outcome)
            case TradeExplanation():
                return render_trade_explanation(outcome)
            case SegmentedBacktest():
                return render_backtest(outcome)
            case TradeJournalView():
                return render_trade_journal(outcome)
            case Lesson():
                return render_lesson(outcome)
            case LessonCatalog():
                return render_lesson_catalog(outcome)
            case AICommitteeReport():
                return render_ai_committee(outcome)
            case OpportunityScan():
                return render_opportunity_scan(outcome)
            case ReplayReport():
                return render_replay(outcome)
            case TrackerView():
                return render_tracker(outcome)
            case PortfolioView():
                return render_portfolio(outcome)
            case AlertCheck():
                return render_alerts(outcome)
            case _ if intent.type is IntentType.COMPARE_ASSETS:
                return _render_comparison(outcome)  # type: ignore[arg-type]
            case _:
                return _render_history(outcome, intent)  # type: ignore[arg-type]


def _headline(record: DecisionRecord) -> str:
    decision = record.decision
    return (
        f"{decision.asset.ticker}: {action_label(decision.action.value)} "
        f"(confianza {format_decimal(decision.confidence, 2)})"
    )


def _render_record(record: DecisionRecord, with_evidence_values: bool) -> str:
    decision = record.decision
    latest_bar = record.market_snapshot.latest_price_bar
    latest_close = record.indicator_snapshot.latest_close
    market_line = f"Datos de mercado hasta: {latest_bar.timestamp:%Y-%m-%d}"
    if latest_close is not None:
        market_line += f" · Último cierre: {format_decimal(latest_close)}"

    lines = [
        f"Análisis del comité: {decision.asset.ticker}",
        f"Fecha del análisis: {record.recorded_at:%Y-%m-%d %H:%M} UTC",
        market_line,
        "",
        f"Decisión del comité: {action_label(decision.action.value)}",
        f"Confianza interna: {format_confidence(decision.confidence)}",
    ]
    adjustment = decision_adjustment(decision.summary, decision.action.value)
    if adjustment:
        lines.append(adjustment)

    lines.append("")
    lines.append("Opinión de cada integrante del comité:")
    for report in decision.agent_reports:
        lines.append(
            f"- {role_label(report.role.value)}: {stance_label(report.stance.value)} "
            f"(confianza {format_decimal(report.confidence, 2)}) - {translate_report_summary(report.summary).rstrip('.')}."
        )
        reasons = [item for item in report.evidence if item.key != _COVERAGE_KEY or with_evidence_values]
        if reasons:
            lines.append("  Razones principales:")
        for item in reasons:
            reason = translate_interpretation(item.key, item.interpretation)
            if with_evidence_values:
                reason += f" (valor: {format_evidence_value(item.key, item.value)})"
            lines.append(f"    * {reason}")

    lines.append("")
    if decision.risks:
        lines.append("Riesgos identificados:")
        for risk in decision.risks:
            lines.append(
                f"- {severity_label(risk.severity.value)}: {translate_interpretation(risk.key, risk.description)} "
                f"(señalado por: {role_label(risk.source_role.value)})"
            )
    else:
        lines.append("Riesgos identificados: ninguno en las reglas evaluadas.")

    lines.append("")
    lines.append(f"Registro {record.record_id} (guardado en el historial de decisiones).")
    lines.append(_CONFIDENCE_NOTE)
    lines.append(_DISCLAIMER)
    return "\n".join(lines)


def _render_comparison(records: Sequence[DecisionRecord]) -> str:
    blocks = [_render_record(record, with_evidence_values=False) for record in records]
    return "Comparación (dos decisiones independientes del comité):\n\n" + "\n\n---\n\n".join(blocks)


def _render_history(records: Sequence[DecisionRecord], intent: Intent) -> str:
    symbol = canonical_symbol(intent.parameters.get("symbol", ""))
    if not records:
        if symbol:
            return f"Todavía no hay decisiones registradas para {symbol}. Prueba con /analyze {symbol}."
        return "Todavía no hay decisiones registradas. Prueba con /analyze AAPL."

    title = f"Decisiones registradas para {symbol}" if symbol else "Decisiones registradas más recientes"
    lines = [f"{title} (de la más reciente a la más antigua):"]
    for record in records:
        lines.append(f"- {record.recorded_at:%Y-%m-%d %H:%M} UTC · {_headline(record)}")
    if any(phrase in intent.raw_text.lower() for phrase in _TALLY_PHRASES):
        tally = Counter(action_label(record.decision.action.value) for record in records)
        lines.append("")
        lines.append("Conteo: " + ", ".join(f"{action}: {count}" for action, count in sorted(tally.items())))
    lines.append("")
    lines.append("Usa /explain <símbolo> para ver el razonamiento de la decisión más reciente.")
    lines.append(_DISCLAIMER)
    return "\n".join(lines)


def _render_help(content: HelpContent, intent: Intent) -> str:
    clarification = intent.parameters.get("clarification")
    if intent.type is IntentType.UNKNOWN and clarification:
        return clarification
    lines = [content.description]
    if content.available_intents:
        lines.append("")
        lines.extend(f"- {item}" for item in content.available_intents)
    return "\n".join(lines)
