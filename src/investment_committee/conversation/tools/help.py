"""HelpTool, registered for both HELP and UNKNOWN. See spec-006 S15.7."""

from __future__ import annotations

from ..models import ConversationSession, Intent
from .base import HelpContent, Tool


class HelpTool(Tool):
    def execute(self, intent: Intent, session: ConversationSession) -> HelpContent:
        return HelpContent(
            available_intents=(
                "/analyze AAPL - el comité analiza una acción, un ETF o un par Forex "
                '(por ejemplo /analyze EUR/USD, o escribe "Analiza Apple")',
                "/history AAPL - decisiones registradas para un activo (/history solo: las más recientes)",
                "/explain AAPL - por qué la última decisión sobre un activo salió así",
                "/compare MSFT AAPL - analiza dos activos y muestra sus decisiones lado a lado",
                "/trade EUR/USD - propuesta de trading simulado (COMPRA, VENTA o NO OPERAR) en velas de 1 hora",
                "/explicar_trade - explicación paso a paso de la última propuesta de trading",
                "/operaciones - operaciones simuladas y cómo terminaron",
                "/backtest EUR/USD - resultados históricos simulados de la estrategia",
                "/aprender rsi - te explico un concepto (/aprender solo: lista de conceptos)",
                "/committee EUR/USD - comité de IA (DeepSeek): opiniones de cuatro agentes, riesgos y desacuerdos",
                "/oportunidades - busca candidatas de day trading en EUR/USD, GBP/USD y USD/JPY (exploratorio: no "
                "abre operaciones)",
                "/simular_mes - qué habría pasado el mes pasado siguiendo al escáner (políticas A, B y C; backtest retrospectivo)",
                "/seguimiento - resultado hipotético de las candidatas que emitió el escáner",
                "/portafolio - valoración y riesgos de las posiciones vigiladas (no envía órdenes)",
                "/alertas - comprueba ahora las alertas del portafolio y muestra su estado",
                "/help - este mensaje",
                "Resúmenes de portafolio y de rendimiento (próximamente)",
            ),
            description="¡Hola! Soy el asistente del Comité de Inversión. Analizo acciones, ETF y pares "
            "Forex con un comité determinista de analistas, registro cada decisión y te muestro "
            "el historial de recomendaciones. También simulo operaciones de trading con EUR/USD y "
            "te explico cada concepto desde cero. Es un análisis educativo: nunca ejecuto operaciones "
            "reales ni me conecto a un bróker.",
        )
