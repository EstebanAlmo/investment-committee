"""/oportunidades: command parsing, tool, and the Spanish report. Scans come
from the real service over fake market data - no network."""

from __future__ import annotations

from decimal import Decimal

from investment_committee.application import ScannerConfig
from investment_committee.conversation.command_parser import CommandParser
from investment_committee.conversation.models import IntentType
from investment_committee.conversation.opportunities_render import render_opportunity_scan
from investment_committee.conversation.tools.opportunities import OpportunitiesTool
from investment_committee.market_intelligence import MarketDataError
from investment_committee.simulation import AccountState, HistoricalValidation, ValidationStatus

from tests.application.test_opportunities import FakeAI, jpy_bars, scanner
from tests.conversation.tools._support import make_session
from tests.simulation._support import downtrend, sideways, uptrend


def test_command_needs_no_symbol_and_ignores_an_argument() -> None:
    parser = CommandParser()

    for text in ("/oportunidades", "/opportunities", "/oportunidades EUR/USD"):
        intent = parser.parse(text)
        assert intent is not None and intent.type is IntentType.OPPORTUNITIES and dict(intent.parameters) == {}


def test_tool_never_asks_for_ai_reviews() -> None:
    ai = FakeAI({"EURUSD=X": "FAVORABLE"})
    service, _ = scanner(ai_committee=ai, config=ScannerConfig())
    intent = CommandParser().parse("/oportunidades")
    assert intent is not None

    OpportunitiesTool(service).execute(intent, make_session())

    assert ai.calls == []


def test_ranked_report_separates_exploratory_candidates_from_authorized_operations() -> None:
    text = render_opportunity_scan(scanner()[0].scan())

    assert "Ranking exploratorio (máximo 3). NO son operaciones autorizadas ni oportunidades validadas" in text
    assert "1. EUR/USD · COMPRA (LONG) · CANDIDATA EXPLORATORIA" in text
    assert "2. GBP/USD · VENTA (SHORT) · CANDIDATA EXPLORATORIA" in text
    assert "Operaciones autorizadas: ninguna" in text
    assert "Expectativa neta: NO VALIDADA" in text
    assert "Señales técnicas detectadas: 2 · superan los controles: 2 · descartadas: 0 · errores: 0" in text
    assert "Sin señal técnica:\n- USD/JPY:" in text
    assert "probabilidad de" not in text and "%" in text  # no win probability anywhere


def test_zero_opportunities_is_explained_without_inventing_a_trade() -> None:
    text = render_opportunity_scan(scanner({"EURUSD=X": sideways(), "GBPUSD=X": sideways(), "USDJPY=X": sideways()})[0].scan())

    assert "No hay candidatas en este escaneo" in text and "no se fabrican operaciones" in text
    assert "Entrada" not in text and "Stop-loss" not in text


def test_usd_jpy_prices_use_three_decimals() -> None:
    text = render_opportunity_scan(scanner({"EURUSD=X": sideways(), "GBPUSD=X": sideways(), "USDJPY=X": jpy_bars()})[0].scan())

    line = next(l for l in text.splitlines() if l.strip().startswith("Entrada"))
    entry = line.split("Entrada ")[1].split(" ·")[0]
    assert entry.count(",") == 1 and len(entry.split(",")[1]) == 3
    assert "conversión conservadora" in text


def test_partial_errors_and_discards_are_listed() -> None:
    account = AccountState(equity=Decimal("9880"), day_start_equity=Decimal("10000"), realized_pnl_today=Decimal("-120"))
    service, _ = scanner(
        {"EURUSD=X": uptrend(), "GBPUSD=X": MarketDataError("no price history"), "USDJPY=X": jpy_bars()}, account_provider=lambda now: account
    )

    text = render_opportunity_scan(service.scan())

    assert "No elegibles o con errores (el resto del escaneo continuó):\n- GBP/USD: GBP/USD: sin datos de mercado" in text
    assert "Señales técnicas descartadas:" in text and "riesgo agregado" in text
    assert "quedan 30,00 USD" in text


def test_validated_candidates_get_their_own_section() -> None:
    evidence = {("tendencia-momentum-volatilidad", "1.0.0", "GBPUSD=X"): HistoricalValidation(ValidationStatus.VALIDADA, "fixture")}

    text = render_opportunity_scan(scanner(config=ScannerConfig(validations=evidence))[0].scan())

    assert "Con evidencia histórica favorable (siguen siendo simulación, no operaciones autorizadas):" in text
    assert "1. GBP/USD · VENTA (SHORT) · OPORTUNIDAD CON EVIDENCIA HISTÓRICA FAVORABLE" in text


def test_ai_review_lines() -> None:
    ai = FakeAI({"EURUSD=X": "NO_CONCLUYENTE", "GBPUSD=X": "DESFAVORABLE"})
    text = render_opportunity_scan(scanner(ai_committee=ai)[0].scan(ai_reviews=2))

    assert "objeción del comité de IA: conclusión NO CONCLUYENTE" in text
    assert "Comité de IA: no objetó (conclusión DESFAVORABLE (análisis ai-GBPUSD=X)); esto no autoriza la operación" in text
    assert "revisión del comité de IA: 2 análisis (solo puede objetar, nunca autorizar)" in text


def test_default_report_says_ai_is_off() -> None:
    text = render_opportunity_scan(scanner({"EURUSD=X": uptrend(), "GBPUSD=X": downtrend(), "USDJPY=X": sideways()})[0].scan())

    assert "comité de IA: desactivado en este escaneo (sin llamadas pagadas)" in text
