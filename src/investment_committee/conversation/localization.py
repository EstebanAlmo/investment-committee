"""Spanish (es-CO) presentation labels and text for user-facing replies.

es-CO is the interface's default (and, for now, only) language. Translation
happens here, at presentation time only: enum values, evidence keys, and the
committee's own English reasoning text stay untouched in the domain and in
the Decision Ledger. Labels are keyed by enum *value* strings so this module
needs no import from committee/ (S8).

The committee's free text is produced by a fixed set of deterministic
templates (committee/analysts, committee/chair.py), so each template has an
exact or pattern translation below. Text matching no known template is
returned unchanged rather than guessed at.
"""

from __future__ import annotations

import re
from decimal import Decimal

INTERFACE_LOCALE = "es-CO"

ACTION_LABELS = {"BUY": "COMPRAR", "SELL": "VENDER", "HOLD": "MANTENER"}
STANCE_LABELS = {"SUPPORT": "A FAVOR", "NEUTRAL": "NEUTRAL", "OPPOSE": "EN CONTRA"}
ROLE_LABELS = {
    "TECHNICAL_ANALYST": "Analista técnico",
    "RISK_MANAGER": "Gestor de riesgo",
    "DEVILS_ADVOCATE": "Abogado del diablo",
    "COMMITTEE_CHAIR": "Presidente del comité",
}
SEVERITY_LABELS = {"LOW": "BAJO", "MEDIUM": "MEDIO", "HIGH": "ALTO"}


def action_label(value: str) -> str:
    return ACTION_LABELS.get(value, value)


def stance_label(value: str) -> str:
    return STANCE_LABELS.get(value, value)


def role_label(value: str) -> str:
    return ROLE_LABELS.get(value, value)


def severity_label(value: str) -> str:
    return SEVERITY_LABELS.get(value, value)


# --- Evidence interpretations (committee/analysts) ---

_EXACT_INTERPRETATIONS = {
    "price is above its 20-day average": "el precio está por encima de su promedio de 20 días",
    "price is not above its 20-day average": "el precio no está por encima de su promedio de 20 días",
    "SMA20 is above SMA50": "el promedio de 20 días (SMA20) está por encima del de 50 días (SMA50)",
    "SMA20 is not above SMA50": "el promedio de 20 días (SMA20) no está por encima del de 50 días (SMA50)",
    "SMA50 is above SMA200": "el promedio de 50 días (SMA50) está por encima del de 200 días (SMA200)",
    "SMA50 is not above SMA200": "el promedio de 50 días (SMA50) no está por encima del de 200 días (SMA200)",
    "RSI14 is within the healthy bullish band (45-70)": "el RSI de 14 días está dentro de la franja alcista sana (45-70)",
    "RSI14 is outside the healthy bullish band (45-70)": "el RSI de 14 días está fuera de la franja alcista sana (45-70)",
    "RSI14 is within the stable range (30-70)": "el RSI de 14 días está dentro del rango estable (30-70)",
    "RSI14 is outside the stable range (30-70)": "el RSI de 14 días está fuera del rango estable (30-70)",
    "RSI14 indicates overbought overextension": "el RSI de 14 días indica sobrecompra (movimiento sobreextendido)",
    "RSI14 indicates oversold overextension": "el RSI de 14 días indica sobreventa (movimiento sobreextendido)",
    "RSI14 is within the non-extended range (25-75)": "el RSI de 14 días está dentro del rango no extendido (25-75)",
    "maximum drawdown is severe": "la caída máxima desde un pico es severa",
    "maximum drawdown is not severe": "la caída máxima desde un pico no es severa",
    "long-term trend and short-term momentum disagree": "la tendencia de largo plazo y el impulso de corto plazo no coinciden",
    "long-term trend and short-term momentum agree": "la tendencia de largo plazo y el impulso de corto plazo coinciden",
    "moving averages are not cleanly ordered": "los promedios móviles no están ordenados de forma clara",
    "moving averages are cleanly ordered": "los promedios móviles están ordenados de forma clara",
}

_RETURN_PERIODS = {
    "return_30d": "de 30 días",
    "return_90d": "de 90 días",
    "return_ytd": "en lo corrido del año",
}

_BANDS = {
    "low": "baja",
    "moderate": "moderada",
    "high": "alta",
    "severe": "severa",
    "shallow": "leve",
    "contained": "contenido",
    "elevated": "elevado",
    "extreme": "extremo",
}
_MODERATE_MASCULINE = {"moderada": "moderado"}

_INDICATOR_NAMES = {
    "price_above_sma20": "precio frente a su promedio de 20 días",
    "sma20_above_sma50": "promedio de 20 días frente al de 50 días",
    "sma50_above_sma200": "promedio de 50 días frente al de 200 días",
    "rsi14_healthy_band": "franja sana del RSI de 14 días",
    "rsi_14": "RSI de 14 días",
    "return_30d_positive": "rendimiento de 30 días",
    "return_90d_positive": "rendimiento de 90 días",
    "return_ytd_positive": "rendimiento en lo corrido del año",
    "return_30d": "movimiento de 30 días",
    "annualized_volatility": "volatilidad anualizada",
    "maximum_drawdown": "caída máxima",
    "overextension": "sobreextensión",
    "drawdown_concern": "caída severa",
    "trend_momentum_conflict": "coincidencia entre tendencia e impulso",
    "weak_trend_confirmation": "confirmación de la tendencia",
}

_COVERAGE_PATTERN = re.compile(r"^(\d+) of (\d+) (technical|risk) rules were evaluable$")
_VOLATILITY_PATTERN = re.compile(r"^(low|moderate|high) annualized volatility$")
_DRAWDOWN_PATTERN = re.compile(r"^(severe|moderate|shallow) maximum drawdown$")
_MOVE_PATTERN = re.compile(r"^(contained|elevated|extreme) 30-day return magnitude$")
_ONLY_MISSING_PATTERN = re.compile(r"^only (\d+) of (\d+) named indicators are unavailable$")
_MISSING_PATTERN = re.compile(r"^(\d+) of (\d+) named indicators are unavailable$")
_UNAVAILABLE_PATTERN = re.compile(r"^(\w+) could not be evaluated: insufficient history$")


def translate_interpretation(key: str, text: str) -> str:
    """Spanish for one EvidenceItem/RiskItem interpretation. `key` is the
    evidence key, used where the English text alone is ambiguous (which
    return period a "return is positive" refers to)."""
    if text in _EXACT_INTERPRETATIONS:
        return _EXACT_INTERPRETATIONS[text]
    if text in ("return is positive", "return is not positive"):
        period = next((label for prefix, label in _RETURN_PERIODS.items() if key.startswith(prefix)), "")
        verb = "es positivo" if text == "return is positive" else "no es positivo"
        return " ".join(part for part in ("el rendimiento", period, verb) if part)
    if match := _COVERAGE_PATTERN.match(text):
        kind = "técnicas" if match.group(3) == "technical" else "de riesgo"
        return f"se pudieron evaluar {match.group(1)} de {match.group(2)} reglas {kind}"
    if match := _VOLATILITY_PATTERN.match(text):
        return f"volatilidad anualizada {_BANDS[match.group(1)]}"
    if match := _DRAWDOWN_PATTERN.match(text):
        return f"caída máxima {_BANDS[match.group(1)]}"
    if match := _MOVE_PATTERN.match(text):
        band = _BANDS[match.group(1)]
        return f"movimiento de 30 días {_MODERATE_MASCULINE.get(band, band)}"
    if match := _ONLY_MISSING_PATTERN.match(text):
        return f"solo {match.group(1)} de {match.group(2)} indicadores no están disponibles"
    if match := _MISSING_PATTERN.match(text):
        return f"{match.group(1)} de {match.group(2)} indicadores no están disponibles"
    if match := _UNAVAILABLE_PATTERN.match(text):
        name = _INDICATOR_NAMES.get(match.group(1), match.group(1))
        return f"{name}: no se pudo evaluar por falta de historial suficiente"
    return text


_VALUE_WORDS = {
    "not cleanly ordered": "no ordenados",
    "cleanly ordered": "ordenados",
    "insufficient_history": "historial insuficiente",
}

# Evidence keys whose Decimal value is a fraction (0.05 = 5 %): returns,
# drawdowns, and volatility (indicators/engine.py).
_PERCENT_KEY_MARKERS = ("return", "drawdown", "volatility", "trend_momentum")


def format_decimal(value: Decimal, places: int = 4) -> str:
    """Colombian number formatting: decimal comma."""
    return f"{value:.{places}f}".replace(".", ",")


def format_evidence_value(key: str, value: Decimal | str) -> str:
    if isinstance(value, Decimal):
        if any(marker in key for marker in _PERCENT_KEY_MARKERS):
            return f"{format_decimal(value * 100, 2)} %"
        return format_decimal(value)
    return _VALUE_WORDS.get(value, value)


def format_confidence(confidence: Decimal) -> str:
    """'0,90 (90 %)' - the score as recorded, plus the same figure as a percent."""
    return f"{format_decimal(confidence, 2)} ({format_decimal(confidence * 100, 0)} %)"


# --- AgentReport.summary (committee/analysts) ---

_CHECKLIST_SUMMARY_PATTERN = re.compile(
    r"^(?:SUPPORT|NEUTRAL|OPPOSE): (\d+)/(\d+) applicable (technical|risk) rules passed \((\d+) not applicable\)\.$"
)
_CHECKS_SUMMARY_PATTERN = re.compile(
    r"^(?:SUPPORT|NEUTRAL|OPPOSE): (\d+) of (\d+) applicable checks raised a concern\.$"
)


def translate_report_summary(text: str) -> str:
    """Spanish for an analyst's one-line summary, without the stance prefix
    (the reply shows the stance label separately)."""
    if match := _CHECKLIST_SUMMARY_PATTERN.match(text):
        passed, applicable, kind, not_applicable = match.groups()
        kind_label = "técnicas" if kind == "technical" else "de riesgo"
        return f"se cumplieron {passed} de {applicable} reglas {kind_label} aplicables ({not_applicable} no aplicables)"
    if match := _CHECKS_SUMMARY_PATTERN.match(text):
        concerns, applicable = match.groups()
        return f"{concerns} de {applicable} verificaciones aplicables señalaron una preocupación"
    return text


# --- InvestmentDecision.summary (committee/chair.py) ---

_DOWNGRADE_PATTERN = re.compile(r"Downgraded from (BUY|SELL|HOLD) \(Step 1 baseline\) due to a HIGH-severity risk\.")
_FLOOR_PATTERN = re.compile(
    r"Reduced from (BUY|SELL|HOLD) \(Step 1 baseline\) because an analyst's confidence was below the ([\d.]+) floor\."
)


def decision_adjustment(summary: str, final_action: str) -> str | None:
    """Spanish note for a chair override recorded in the decision summary,
    or None if the base decision stood."""
    if match := _DOWNGRADE_PATTERN.search(summary):
        return (
            f"La decisión base era {action_label(match.group(1))}, pero se rebajó a "
            f"{action_label(final_action)} por un riesgo de severidad ALTA."
        )
    if match := _FLOOR_PATTERN.search(summary):
        floor = format_decimal(Decimal(match.group(2)), 2)
        return (
            f"La decisión base era {action_label(match.group(1))}, pero se redujo a "
            f"{action_label(final_action)} porque la confianza de un analista quedó por debajo "
            f"del mínimo de {floor}."
        )
    return None
