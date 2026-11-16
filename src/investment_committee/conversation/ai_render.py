"""Spanish rendering of an AI committee report (/committee).

Summary first (instrument, data freshness, conclusion, executive summary -
labelled as the AI coordinator's or as Python's contingency summary when
there was no valid synthesis), then each AI agent's opinion, arguments, risks, disagreements, missing
information, change conditions, a beginner explanation, the deterministic
committee's decision and votes in their own section (rules, never AI
positions), and the audit footer. Shows only structured, validated fields - never raw model output
or hidden reasoning.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from investment_committee.application import AICommitteeReport

CONCLUSIONS = {
    "FAVORABLE": "FAVORABLE",
    "DESFAVORABLE": "DESFAVORABLE",
    "NEUTRAL": "NEUTRAL",
    "NO_CONCLUYENTE": "NO CONCLUYENTE",
}
TRADING = {
    "COMPRA": "COMPRA (coincide con la señal de la estrategia Python)",
    "VENTA": "VENTA (coincide con la señal de la estrategia Python)",
    "NO_OPERAR": "NO OPERAR",
    "NO_APLICA": "no aplica (no hay señal de trading para este instrumento)",
}
STATUS = {
    "OK": "",
    "NOT_AVAILABLE": " (sin datos para analizar)",
    "FAILED": " (falló)",
    "SKIPPED_BUDGET": " (omitido por límite de presupuesto)",
    "SKIPPED": " (omitido)",
}
VALIDATION = {
    "COMPLETE": "completa",
    "ADJUSTED": "con ajustes de los controles de Python",
    "PARTIAL": "parcial (algún agente no respondió)",
    "FAILED": "fallida (sin conclusión del modelo)",
}
ROLE_TITLES = {
    "TECHNICAL_ANALYST": "Analista técnico",
    "MACRO_ANALYST": "Analista macroeconómico",
    "RISK_MANAGER": "Gestor de riesgo",
    "DEVILS_ADVOCATE": "Abogado del diablo",
    "COORDINATOR": "Coordinador",
}


def _when(moment: datetime) -> str:
    return f"{moment.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC"


def _bullets(title: str, items, limit: int = 4) -> list[str]:  # type: ignore[no-untyped-def]
    items = [item for item in items if item][:limit]
    return [title, *(f"- {item}" for item in items)] if items else []


def _agent_line(result) -> str:  # type: ignore[no-untyped-def]
    title = ROLE_TITLES.get(result.role.value, result.role.value)
    suffix = STATUS.get(result.status.value, "")
    opinion = result.opinion
    if opinion is None:
        return f"- {title}{suffix}: {result.error or 'sin opinión'}."
    role = result.role.value
    if role == "TECHNICAL_ANALYST":
        first = opinion.arguments[0].text if opinion.arguments else opinion.neutral_scenario
        return f"- {title}: {opinion.stance} (confianza {opinion.confidence.lower()}). {first}"
    if role == "MACRO_ANALYST":
        state = "información disponible" if opinion.status == "DISPONIBLE" else "información no disponible"
        return f"- {title}{suffix}: {state}. {opinion.summary}"
    if role == "RISK_MANAGER":
        return f"- {title}: riesgo {opinion.risk_level} (confianza {opinion.confidence.lower()}). {opinion.engine_comment}"
    if role == "DEVILS_ADVOCATE":
        verdict = "planteó una objeción material" if opinion.material_objection else "no encontró objeciones de fondo"
        point = next(iter([*opinion.contradictions, *opinion.challenged_assumptions, *opinion.adverse_scenarios]), "")
        return f"- {title}: {verdict}." + (f" {point}" if point else "")
    return f"- {title}: conclusión propuesta {opinion.conclusion}."


def render_ai_committee(report: AICommitteeReport) -> str:
    evidence, decision = report.evidence, report.decision
    lines = [
        f"Comité de IA · {evidence.display_name} · {_when(report.created_at)}",
        f"Datos hasta: {_when(evidence.data_as_of) if evidence.data_as_of else 'sin datos'} ({evidence.freshness_note})",
        "",
        f"Conclusión de inversión: {CONCLUSIONS.get(decision.conclusion, decision.conclusion)}",
        f"Postura de trading: {TRADING.get(decision.trading_stance, decision.trading_stance)}",
        f"Confianza: {decision.confidence} (evaluación cualitativa del modelo, no es una probabilidad de ganancia)",
        "",
        f"{_summary_label(decision.summary_source)}: {decision.executive_summary}",
        "",
        "Opinión de cada agente de IA:",
        *(_agent_line(result) for result in report.agents if result.role.value != "COORDINATOR"),
    ]
    sections = [
        _bullets("A favor:", decision.evidence_for),
        _bullets("En contra:", decision.evidence_against),
        _bullets("Riesgos:", decision.risks),
        _bullets("Desacuerdos:", decision.disagreements),
        _bullets("Información faltante:", decision.missing_information, limit=5),
        _bullets("Qué podría cambiar la conclusión:", decision.change_conditions),
        _bullets("Controles de Python aplicados:", report.controls),
    ]
    for section in sections:
        if section:
            lines += ["", *section]
    if report.issues:
        lines += ["", f"Se eliminaron {len(report.issues)} afirmaciones que no pasaron los controles (cifras sin respaldo en la evidencia o votos del comité determinístico presentados como posturas de la IA)."]
    repaired = [r for r in report.agents if r.repaired]
    normalised = sum(len([n for n in r.notes if not n.startswith("la primera respuesta")]) for r in report.agents)
    unrecovered = [r for r in report.agents if r.validation_errors and not r.repaired]
    if repaired or normalised or unrecovered:
        parts = []
        if repaired:
            parts.append(f"{len(repaired)} respuesta(s) con formato inválido se corrigieron tras una solicitud de corrección")
        if unrecovered:
            parts.append(f"{len(unrecovered)} no se pudo(eron) recuperar")
        if normalised:
            parts.append(f"{normalised} campo(s) se normalizaron sin cambiar su contenido")
        lines += ["", "Formato de las respuestas: " + "; ".join(parts) + "."]
    lines += [
        "",
        f"En palabras sencillas: {decision.beginner_explanation}",
        "",
        "Comité determinístico (/analyze, reglas fijas; sus votos no son opiniones de la IA):",
        f"- Decisión: {report.deterministic_baseline or 'sin dato'}",
        *(f"- {item.label[0].upper()}{item.label[1:]}: {item.display_value()}" for item in _deterministic_votes(report)),
        "La señal de trading simulado se consulta con /trade; este comité no crea operaciones.",
        f"Auditoría: análisis {report.analysis_id} · {report.committee_version} · {report.provider}/{report.model} · "
        f"validación {VALIDATION.get(report.validation_status.value, report.validation_status.value)} · "
        f"{report.calls} llamadas · {report.usage.total_tokens} tokens"
        + (f" · costo estimado {_usd(report.usage.estimated_cost_usd)}" if report.usage.estimated_cost_usd is not None else ""),
        "Aviso: análisis educativo, no es asesoría financiera. No se ejecutó ninguna operación ni hay conexión con un bróker.",
    ]
    return "\n".join(lines)


def _summary_label(source: str) -> str:
    if source == "CONTINGENCIA_PYTHON":
        return "Resumen de contingencia (generado por Python, no por la IA)"
    return "Resumen del coordinador de IA"


def _deterministic_votes(report: AICommitteeReport) -> list:  # type: ignore[type-arg]
    return [
        item
        for item in report.evidence.items
        if item.category.startswith("comité determinístico") and item.label.startswith("voto del")
    ]


def _usd(value: Decimal) -> str:
    return f"{value:.4f} USD".replace(".", ",")
