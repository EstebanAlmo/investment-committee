"""Acceptance tests for the deterministic Telegram MVP.

Each test drives a Telegram-shaped update through the real stack:
TelegramConversationAdapter -> ConversationService (built by the production
composition root) -> tools -> RecordDecisionUseCase / DecisionQueryService
-> ApplicationService -> IndicatorEngine -> DeterministicCommitteeEngine ->
JsonLinesDecisionLedger (in a temp dir). Only two things are fake: the
market data provider (synthetic price history) and the Telegram Bot API.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from investment_committee.application import (
    ApplicationService,
    DecisionQueryService,
    RecordDecisionResult,
    RecordDecisionUseCase,
)
from investment_committee.committee import (
    DeterministicCommitteeChair,
    DeterministicCommitteeEngine,
    DeterministicDevilsAdvocate,
    DeterministicRiskManager,
    DeterministicTechnicalAnalyst,
)
from investment_committee.conversation import build_default_conversation_service
from investment_committee.domain import Asset, AssetType, MarketSnapshot, PriceBar
from investment_committee.indicators import IndicatorEngine
from investment_committee.interfaces.telegram.adapter import TelegramConversationAdapter
from investment_committee.market_intelligence import MarketIntelligenceService
from investment_committee.market_intelligence.providers.base import MarketDataError, MarketDataProvider
from investment_committee.memory import DETERMINISTIC_STRATEGY_METADATA, JsonLinesDecisionLedger

from investment_committee.application import AICommitteeService, OpportunityScannerService, TradingService, account_state_from_journal
from investment_committee.committee.ai import AICommitteeCoordinator
from investment_committee.integrations.llm import LLMClient
from investment_committee.memory.ai_ledger import InMemoryAICommitteeLedger
from investment_committee.simulation import InMemoryOpportunityScanLedger, JsonLinesTradeJournal
from investment_committee.simulation.tracker import InMemoryTrackerLedger
from investment_committee.portfolio.store import InMemoryAlertStore, InMemoryPositionStore

from tests.interfaces.telegram.test_adapter import FakeBot, make_update
from tests.simulation._support import bars_from_moves, closing_time, regime_series, sideways, uptrend

OWNER_ID = 42
_LAST_SESSION = datetime(2026, 10, 9, 21, 0, tzinfo=timezone.utc)


class SyntheticMarketData(MarketDataProvider):
    """A deterministic year of daily bars with a steady uptrend - enough
    history for every indicator (SMA 200 included). Records every fetch."""

    def __init__(self) -> None:
        self.fetched: list[str] = []

    def get_daily_history(self, ticker: str) -> MarketSnapshot:
        self.fetched.append(ticker)
        bars = []
        for day in range(260):
            close = Decimal("100") + Decimal(day) * Decimal("0.25") + Decimal((day % 7) - 3) * Decimal("0.4")
            bars.append(
                PriceBar(
                    timestamp=_LAST_SESSION - timedelta(days=259 - day),
                    open=close,
                    high=close + Decimal("1"),
                    low=close - Decimal("1"),
                    close=close,
                    volume=1_000_000,
                )
            )
        asset = Asset(ticker=ticker, name=None, asset_type=AssetType.STOCK, exchange=None, currency=None)
        return MarketSnapshot(asset=asset, generated_at=_LAST_SESSION, price_bars=bars)


class SyntheticIntradayData(SyntheticMarketData):
    """Hourly EUR/USD bars (tests/simulation/_support.py); `now` is the
    close of the last bar, so the data is fresh and the market open."""

    def __init__(self) -> None:
        super().__init__()
        self.bars = uptrend()
        self.intraday_fetched: list[tuple[str, str, str]] = []

    @property
    def now(self) -> datetime:
        return closing_time(self.bars)

    def get_intraday_history(self, ticker: str, interval: str, period: str) -> MarketSnapshot:
        self.intraday_fetched.append((ticker, interval, period))
        asset = Asset(ticker=ticker, name=None, asset_type=AssetType.STOCK, exchange=None, currency=None)
        return MarketSnapshot(asset=asset, generated_at=self.now, price_bars=self.bars)


class SpyRecordDecisionUseCase(RecordDecisionUseCase):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.analyzed: list[str] = []

    def analyze_and_record(self, symbol: str) -> RecordDecisionResult:
        self.analyzed.append(symbol)
        return super().analyze_and_record(symbol)


class Harness:
    def __init__(self, ledger_path: Path, market_data: MarketDataProvider | None = None, ai_llm: LLMClient | None = None) -> None:
        self.ledger_path = ledger_path
        self.market_data = market_data if market_data is not None else SyntheticMarketData()
        ledger = JsonLinesDecisionLedger(ledger_path)
        application_service = ApplicationService(
            market_intelligence_service=MarketIntelligenceService(self.market_data),
            indicator_engine=IndicatorEngine(),
            committee_engine=DeterministicCommitteeEngine(
                technical_analyst=DeterministicTechnicalAnalyst(),
                risk_manager=DeterministicRiskManager(),
                devils_advocate=DeterministicDevilsAdvocate(),
                chair=DeterministicCommitteeChair(),
            ),
        )
        self.use_case = SpyRecordDecisionUseCase(application_service, ledger, DETERMINISTIC_STRATEGY_METADATA)
        self.intraday = SyntheticIntradayData()
        self.ai_ledger = InMemoryAICommitteeLedger()
        self.trading_journal = JsonLinesTradeJournal(ledger_path.parent / "simulated_trades.jsonl")
        self.trading = TradingService(
            MarketIntelligenceService(self.intraday),
            self.trading_journal,
            clock=lambda: self.intraday.now,
        )
        self.opportunity_ledger = InMemoryOpportunityScanLedger()
        self.tracker_ledger = InMemoryTrackerLedger()
        self.positions = InMemoryPositionStore()
        self.scanner = OpportunityScannerService(
            MarketIntelligenceService(self.intraday),
            self.opportunity_ledger,
            account_provider=lambda now: account_state_from_journal(self.trading_journal, Decimal("10000"), now),
            tracker=self.tracker_ledger,
            clock=lambda: self.intraday.now,
        )
        from investment_committee.application import HistoricalReplayService, OpportunityTrackerService, PortfolioGuardianService
        from tests.application.test_opportunities import FakeMarket
        from tests.simulation.test_replay import MONDAY, market

        self.replay_market = FakeMarket(market())
        replay = HistoricalReplayService(MarketIntelligenceService(self.replay_market), clock=lambda: MONDAY + timedelta(days=40))
        tracker = OpportunityTrackerService(MarketIntelligenceService(self.intraday), self.tracker_ledger, clock=lambda: self.intraday.now)
        guardian = PortfolioGuardianService(MarketIntelligenceService(self.intraday), self.positions, InMemoryAlertStore(),
                                            journal=self.trading_journal, clock=lambda: self.intraday.now)
        service = build_default_conversation_service(
            allowed_user_ids=frozenset({str(OWNER_ID)}),
            record_decision_use_case=self.use_case,
            decision_query_service=DecisionQueryService(ledger),
            trading_service=self.trading,
            ai_committee_service=AICommitteeService(
                application_service,
                self.ai_ledger,
                AICommitteeCoordinator(ai_llm, clock=lambda: self.intraday.now) if ai_llm is not None else None,
                self.trading,
                clock=lambda: self.intraday.now,
            ),
            opportunity_scanner=self.scanner,
            replay_service=replay,
            tracker_service=tracker,
            portfolio_guardian=guardian,
        )
        self.bot = FakeBot()
        self.adapter = TelegramConversationAdapter(service, self.bot)

    async def send(self, text: str, user_id: int = OWNER_ID) -> str:
        before = len(self.bot.messages)
        await self.adapter.on_update(make_update(text, user_id=user_id), None)
        return "\n".join(reply for _, reply in self.bot.messages[before:])

    def ledger_lines(self) -> list[str]:
        if not self.ledger_path.exists():
            return []
        return [line for line in self.ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path / "ledger" / "decisions.jsonl")


def _headline(reply: str) -> str:
    return reply.splitlines()[0]


def _decision_line(reply: str) -> str:
    return next(line for line in reply.splitlines() if line.startswith("Decisión del comité:"))


# 1. /analyze AAPL
async def test_analyze_command_returns_committee_analysis_and_records_it(harness: Harness) -> None:
    reply = await harness.send("/analyze AAPL")

    assert _headline(reply) == "Análisis del comité: AAPL"
    assert any(f"Decisión del comité: {action}" in reply for action in ("COMPRAR", "MANTENER", "VENDER"))
    assert "Opinión de cada integrante del comité:" in reply
    assert "No se ejecutó ninguna operación" in reply
    assert harness.market_data.fetched == ["AAPL"]
    assert len(harness.ledger_lines()) == 1


# 2. Analyze Apple (deterministic intent routing)
async def test_natural_language_request_is_equivalent_to_the_command(harness: Harness) -> None:
    command_reply = await harness.send("/analyze AAPL")
    natural_reply = await harness.send("Analyze Apple")

    assert _decision_line(natural_reply) == _decision_line(command_reply)
    assert harness.use_case.analyzed == ["AAPL", "AAPL"]
    assert len(harness.ledger_lines()) == 2


# 3. /history AAPL
async def test_history_returns_recorded_decisions(harness: Harness) -> None:
    await harness.send("/analyze AAPL")
    await harness.send("Analyze Apple")

    reply = await harness.send("/history AAPL")

    assert reply.startswith("Decisiones registradas para AAPL")
    assert reply.count("AAPL: ") == 2
    assert harness.market_data.fetched == ["AAPL", "AAPL"]  # history never re-fetches market data


async def test_history_survives_a_restart(harness: Harness) -> None:
    await harness.send("/analyze AAPL")

    restarted = Harness(harness.ledger_path)
    reply = await restarted.send("/history AAPL")

    assert reply.count("AAPL: ") == 1
    assert restarted.market_data.fetched == []


async def test_history_for_unanalyzed_asset_suggests_analyzing(harness: Harness) -> None:
    reply = await harness.send("/history MSFT")

    assert "Todavía no hay decisiones registradas para MSFT" in reply


# 4. /help
async def test_help_lists_available_commands(harness: Harness) -> None:
    reply = await harness.send("/help")

    for command in ("/analyze", "/history", "/explain", "/compare", "/help"):
        assert command in reply
    assert "nunca ejecuto operaciones" in reply
    assert harness.use_case.analyzed == []


# 5. /analyze BTC-USD
@pytest.mark.parametrize("text", ["/analyze BTC-USD", "Analyze BTC-USD", "/analyze XAUUSD=X", "/compare AAPL BTC-USD"])
async def test_unsupported_asset_is_rejected_without_recording(harness: Harness, text: str) -> None:
    reply = await harness.send(text)

    assert "no es una clase de activo admitida" in reply
    assert "No se analizó ni se registró nada" in reply
    assert harness.use_case.analyzed == []
    assert harness.market_data.fetched == []
    assert harness.ledger_lines() == []


# Safety properties around the five interactions
async def test_trade_request_is_declined(harness: Harness) -> None:
    reply = await harness.send("Buy Apple")

    assert "No ejecuto operaciones" in reply
    assert harness.use_case.analyzed == []


async def test_unauthorized_user_gets_nothing(harness: Harness) -> None:
    reply = await harness.send("/analyze AAPL", user_id=999)

    assert "no tienes autorización" in reply
    assert harness.use_case.analyzed == []
    assert harness.ledger_lines() == []


async def test_explain_after_analyze_uses_recorded_decision(harness: Harness) -> None:
    analysis = await harness.send("/analyze AAPL")

    reply = await harness.send("/explain AAPL")

    assert _headline(reply) == _headline(analysis)
    assert _decision_line(reply) == _decision_line(analysis)
    assert "(valor: " in reply  # indicator evidence lines
    assert harness.market_data.fetched == ["AAPL"]


# EUR/USD: both spellings reach one canonical asset (EURUSD=X)
class UnavailableMarketData(SyntheticMarketData):
    def get_daily_history(self, ticker: str) -> MarketSnapshot:
        self.fetched.append(ticker)
        raise MarketDataError(f"no price history returned for {ticker}")


def _analysis_body(reply: str) -> list[str]:
    """The reply without the lines that legitimately differ between two
    otherwise identical analyses (timestamp and record id)."""
    return [line for line in reply.splitlines() if not line.startswith(("Fecha del análisis:", "Registro "))]


@pytest.mark.parametrize("text", ["/analyze EUR/USD", "/analyze EURUSD=X", "/analyze eur/usd", "Analiza EUR/USD"])
async def test_analyze_forex_pair_in_any_spelling_uses_the_canonical_symbol(harness: Harness, text: str) -> None:
    reply = await harness.send(text)

    assert _headline(reply) == "Análisis del comité: EURUSD=X"
    assert harness.market_data.fetched == ["EURUSD=X"]
    assert harness.use_case.analyzed == ["EURUSD=X"]
    assert len(harness.ledger_lines()) == 1
    assert '"ticker": "EURUSD=X"' in harness.ledger_lines()[0]


async def test_both_forex_spellings_produce_the_same_analysis(harness: Harness) -> None:
    slash_reply = await harness.send("/analyze EUR/USD")
    yahoo_reply = await harness.send("/analyze EURUSD=X")

    assert _analysis_body(slash_reply) == _analysis_body(yahoo_reply)
    assert harness.market_data.fetched == ["EURUSD=X", "EURUSD=X"]


@pytest.mark.parametrize("history_command", ["/history EUR/USD", "/history EURUSD=X"])
async def test_forex_history_finds_decisions_recorded_under_either_spelling(
    harness: Harness, history_command: str
) -> None:
    await harness.send("/analyze EUR/USD")
    await harness.send("/analyze EURUSD=X")

    reply = await harness.send(history_command)

    assert reply.startswith("Decisiones registradas para EURUSD=X")
    assert reply.count("EURUSD=X: ") == 2


async def test_forex_history_survives_a_restart(harness: Harness) -> None:
    await harness.send("/analyze EUR/USD")

    restarted = Harness(harness.ledger_path)
    reply = await restarted.send("/history EUR/USD")

    assert reply.count("EURUSD=X: ") == 1
    assert restarted.market_data.fetched == []


async def test_explain_forex_pair_uses_recorded_decision(harness: Harness) -> None:
    analysis = await harness.send("/analyze EURUSD=X")

    reply = await harness.send("/explain EUR/USD")

    assert _decision_line(reply) == _decision_line(analysis)
    assert "(valor: " in reply
    assert harness.market_data.fetched == ["EURUSD=X"]


async def test_compare_forex_pair_with_a_stock(harness: Harness) -> None:
    reply = await harness.send("/compare EUR/USD AAPL")

    assert reply.startswith("Comparación")
    assert "Análisis del comité: EURUSD=X" in reply and "Análisis del comité: AAPL" in reply
    assert harness.market_data.fetched == ["EURUSD=X", "AAPL"]


async def test_gold_is_still_rejected_in_slash_form(harness: Harness) -> None:
    reply = await harness.send("/analyze XAU/USD")

    assert "XAUUSD=X no es una clase de activo admitida" in reply
    assert harness.market_data.fetched == []
    assert harness.ledger_lines() == []


async def test_market_data_failure_is_explained_in_spanish_and_nothing_is_recorded(tmp_path: Path) -> None:
    harness = Harness(tmp_path / "ledger" / "decisions.jsonl", market_data=UnavailableMarketData())

    reply = await harness.send("/analyze EUR/USD")

    assert reply.startswith("No pude obtener datos de mercado para EURUSD=X")
    assert "No se registró ninguna decisión." in reply
    assert harness.market_data.fetched == ["EURUSD=X"]
    assert harness.ledger_lines() == []


# Every user-visible reply is Spanish
_ENGLISH_REPLY_MARKERS = (
    "Committee:", "confidence", "Recorded", "No trade", "not financial advice", "rules passed", "Risks",
    "BUY", "SELL", "HOLD", "SUPPORT", "OPPOSE", "TECHNICAL_ANALYST", "RISK_MANAGER", "DEVILS_ADVOCATE",
    "Nothing was", "isn't", "doesn't", "I don't", "couldn't", "Sorry", "Try ", "try ",
)


@pytest.mark.parametrize(
    "text",
    [
        "/start", "/help", "/analyze AAPL", "/analyze EUR/USD", "/analyze", "/analyze BTC-USD", "/analyze FOO_BAR",
        "/history", "/history AAPL", "/history MSFT", "/explain", "/explain AAPL", "/explain TSLA",
        "/compare AAPL", "/compare EUR/USD AAPL", "/portfolio", "/performance", "/unknowncommand",
        "Buy Apple", "Compra Apple", "Analiza foo corp", "hola",
    ],
)
async def test_every_reply_is_in_spanish(harness: Harness, text: str) -> None:
    await harness.send("/analyze AAPL")

    reply = await harness.send(text)

    leaked = [marker for marker in _ENGLISH_REPLY_MARKERS if marker in reply]
    assert reply.strip()
    assert leaked == [], f"English leaked into the reply to {text!r}: {leaked}\n{reply}"


# --- Simulated trading and lessons ---


async def test_trade_eur_usd_proposes_a_simulated_buy_with_levels(harness: Harness) -> None:
    reply = await harness.send("/trade EUR/USD")

    assert reply.startswith("Trading simulado · EUR/USD · velas de 1 hora")
    assert "Dirección: COMPRA" in reply
    for line in ("Entrada de referencia:", "Stop-loss:", "Take-profit:", "Riesgo/beneficio: 1:", "Costos estimados:",
                 "Ejecución simulada: en la apertura de la siguiente vela"):
        assert line in reply
    assert "SIMULACIÓN" in reply and "independiente de la decisión de inversión del comité" in reply
    assert harness.intraday.intraday_fetched == [("EURUSD=X", "1h", "30d")]
    assert len(harness.trading_journal.signals()) == 1
    assert harness.use_case.analyzed == [] and harness.ledger_lines() == []  # the committee is untouched


async def test_trade_without_symbol_defaults_to_eur_usd(harness: Harness) -> None:
    reply = await harness.send("/trade")

    assert reply.startswith("Trading simulado · EUR/USD")


async def test_trade_in_a_sideways_market_says_no_operar_without_levels(harness: Harness) -> None:
    harness.intraday.bars = sideways()

    reply = await harness.send("/trade EURUSD=X")

    assert "Dirección: NO OPERAR" in reply
    assert "Entrada de referencia:" not in reply and "Stop-loss:" not in reply
    assert "No operar también es una decisión" in reply


async def test_trade_for_an_unconfigured_symbol_is_declined(harness: Harness) -> None:
    reply = await harness.send("/trade AAPL")

    assert reply.startswith("El trading simulado por ahora solo está disponible para EUR/USD")
    assert "/analyze AAPL" in reply
    assert harness.intraday.intraday_fetched == []


async def test_explain_trade_walks_through_each_concept_with_values(harness: Harness) -> None:
    await harness.send("/trade EUR/USD")

    reply = await harness.send("/explicar_trade")

    assert reply.startswith("Explicación paso a paso · EUR/USD · COMPRA")
    for title in ("Tendencia", "Medias móviles", "RSI", "Volatilidad y ATR", "Spread y pips", "Relación riesgo/beneficio"):
        assert title in reply
    assert "Valor: RSI14 = " in reply
    assert "no disponible en esta señal" not in reply


async def test_explain_trade_before_any_trade_asks_for_one(harness: Harness) -> None:
    reply = await harness.send("/explicar_trade")

    assert reply == "Todavía no hay propuestas de trading simulado. Prueba primero con /trade EUR/USD."


async def test_operaciones_tracks_the_trade_until_take_profit(harness: Harness) -> None:
    await harness.send("/trade EUR/USD")
    open_reply = await harness.send("/operaciones")
    assert "esperando la apertura de la siguiente vela" in open_reply

    history = harness.intraday.bars
    rally = bars_from_moves(["0.0010"] * 6, start=closing_time(history), first_close=str(history[-1].close))
    harness.intraday.bars = history + rally
    reply = await harness.send("/operaciones")

    assert "TAKE-PROFIT alcanzado: +" in reply
    assert "entrada simulada " in reply  # the actual next-open fill, not the reference
    assert "USD simulados" in reply
    assert len(harness.trading_journal.final_outcomes()) == 1


async def test_operaciones_when_empty(harness: Harness) -> None:
    reply = await harness.send("/operaciones")

    assert reply.startswith("Todavía no hay operaciones simuladas.")


async def test_backtest_reports_metrics_and_limitations(harness: Harness) -> None:
    harness.intraday.bars = regime_series()

    reply = await harness.send("/backtest EUR/USD")

    assert reply.startswith("Backtest (resultados históricos simulados) · EUR/USD")
    for fragment in ("Capital inicial:", "saldo final:", "Estado final de la cuenta:", "Operaciones:", "Drawdown máximo:",
                     "Costos:", "Por periodos", "Limitaciones:"):
        assert fragment in reply
    assert "Esto NO son ganancias reales" in reply
    assert harness.intraday.intraday_fetched == [("EURUSD=X", "1h", "730d")]
    assert harness.trading_journal.signals() == ()  # backtests never write the trade journal


async def test_aprender_uses_the_latest_trade_values(harness: Harness) -> None:
    await harness.send("/trade EUR/USD")

    reply = await harness.send("/aprender RSI")

    assert reply.startswith("Aprender: RSI, sobrecompra y sobreventa")
    assert "Valor actual: RSI14 = " in reply
    assert "Qué significa para EUR/USD:" in reply
    assert "Limitaciones:" in reply


async def test_aprender_after_a_committee_analysis_uses_its_values(harness: Harness) -> None:
    await harness.send("/analyze AAPL")

    reply = await harness.send("/aprender confianza")

    assert "Valor actual: confianza interna" in reply
    assert "No es la probabilidad de ganar" in reply


async def test_aprender_without_data_explains_in_general(harness: Harness) -> None:
    reply = await harness.send("/aprender apalancamiento")

    assert reply.startswith("Aprender: Tamaño de posición, margen y apalancamiento")
    assert "Valor actual: no disponible." in reply


@pytest.mark.parametrize("text", ["/aprender", "/aprender astrologia"])
async def test_aprender_lists_the_concepts(harness: Harness, text: str) -> None:
    reply = await harness.send(text)

    assert "Conceptos que puedo explicarte:" in reply
    assert reply.count("/aprender ") >= 12


_TRADING_ENGLISH = (
    "LONG", "SHORT", "NO_TRADE", "TAKE_PROFIT", "STOP_LOSS", "EXPIRED", "TREND_", "MOMENTUM_", "VOLATILITY_", "None",
)


@pytest.mark.parametrize(
    "text",
    [
        "/trade EUR/USD", "/trade", "/trade AAPL", "/explicar_trade", "/operaciones", "/backtest EUR/USD",
        "/aprender", "/aprender rsi", "/aprender cfd", "/aprender drawdown",
    ],
)
async def test_every_trading_reply_is_in_spanish(harness: Harness, text: str) -> None:
    await harness.send("/trade EUR/USD")
    if text.startswith("/backtest"):
        harness.intraday.bars = regime_series()

    reply = await harness.send(text)

    leaked = [marker for marker in _ENGLISH_REPLY_MARKERS + _TRADING_ENGLISH if marker in reply]
    assert reply.strip()
    assert leaked == [], f"English leaked into the reply to {text!r}: {leaked}\n{reply}"


# --- /committee (AI committee, fake LLM) ---


def _ai_harness(tmp_path: Path, **overrides: object) -> Harness:
    from tests.committee.ai._support import FakeLLM, replies

    return Harness(tmp_path / "ledger" / "decisions.jsonl", ai_llm=FakeLLM(replies(**overrides)))


async def test_committee_without_configuration_points_to_analyze(harness: Harness) -> None:
    reply = await harness.send("/committee EUR/USD")

    assert reply.startswith("El comité de IA no está disponible: el proveedor de IA no está configurado.")
    assert "/analyze EURUSD=X" in reply
    assert harness.ai_ledger.records() == ()


async def test_committee_reports_every_section_in_spanish(tmp_path: Path) -> None:
    harness = _ai_harness(tmp_path)

    reply = await harness.send("/committee EUR/USD")

    assert reply.startswith("Comité de IA · EUR/USD · ")
    for fragment in (
        "Datos hasta:", "Conclusión de inversión:", "Postura de trading:", "evaluación cualitativa del modelo, no es una probabilidad",
        "Resumen del coordinador de IA:", "Opinión de cada agente de IA:", "- Analista técnico:", "- Analista macroeconómico (sin datos para analizar)",
        "- Gestor de riesgo:", "- Abogado del diablo:", "Riesgos:", "Información faltante:", "En palabras sencillas:",
        "No se ejecutó ninguna operación", "Auditoría: análisis",
    ):
        assert fragment in reply, fragment
    assert len(harness.ai_ledger.records()) == 1
    assert harness.trading_journal.signals() == ()  # no simulated trade was created
    assert harness.ledger_lines() == []  # nor a deterministic decision record
    leaked = [m for m in _ENGLISH_REPLY_MARKERS if m in reply]
    assert leaked == [], leaked


async def test_committee_shows_disagreements_and_controls(tmp_path: Path) -> None:
    harness = _ai_harness(tmp_path, risk={"nivel_riesgo": "ALTO"})

    reply = await harness.send("/committee EURUSD=X")

    assert "Conclusión de inversión: NO CONCLUYENTE" in reply
    assert "Desacuerdos:" in reply and "califica el riesgo como ALTO" in reply
    assert "Controles de Python aplicados:" in reply


async def test_committee_reports_a_repaired_reply(tmp_path: Path) -> None:
    from tests.committee.ai._support import GOOD_REPLIES, FakeLLM, replies

    answers = [
        {**GOOD_REPLIES["devils_advocate"], "invalidacion": [f"Escenario {i}." for i in range(1, 8)]},
        GOOD_REPLIES["devils_advocate"],
    ]
    harness = Harness(tmp_path / "ledger" / "decisions.jsonl", ai_llm=FakeLLM(replies(devils_advocate=lambda messages: answers.pop(0))))

    reply = await harness.send("/committee EUR/USD")

    assert "- Abogado del diablo: no encontró objeciones de fondo." in reply
    assert "Formato de las respuestas: 1 respuesta(s) con formato inválido se corrigieron" in reply
    (record,) = harness.ai_ledger.records()
    devil = next(a for a in record["agents"] if a["role"] == "DEVILS_ADVOCATE")
    assert devil["repaired"] is True
    assert devil["validation_errors"] == ["'invalidacion' has 7 items; at most 6 are allowed"]


async def test_committee_for_a_stock_has_no_trading_stance(tmp_path: Path) -> None:
    harness = _ai_harness(tmp_path)

    reply = await harness.send("/committee AAPL")

    assert reply.startswith("Comité de IA · AAPL")
    assert "Postura de trading: no aplica" in reply


async def test_committee_rejects_unsupported_assets_before_any_model_call(tmp_path: Path) -> None:
    harness = _ai_harness(tmp_path)

    reply = await harness.send("/committee BTC-USD")

    assert "no es una clase de activo admitida" in reply
    assert harness.ai_ledger.records() == ()


async def test_committee_without_symbol_asks_for_one(tmp_path: Path) -> None:
    reply = await _ai_harness(tmp_path).send("/committee")

    assert reply.startswith("¿Qué activo quieres que analice el comité de IA?")


@pytest.mark.parametrize("command", ["/analyze AAPL", "/trade EUR/USD", "/history AAPL", "/operaciones", "/aprender rsi", "/explicar_trade"])
async def test_existing_commands_still_work_with_the_ai_committee_enabled(tmp_path: Path, command: str) -> None:
    harness = _ai_harness(tmp_path)
    await harness.send("/trade EUR/USD")

    reply = await harness.send(command)

    assert reply.strip() and not reply.startswith("Algo salió mal")
    assert harness.ai_ledger.records() == ()


# --- /oportunidades (deterministic scan, fake market data) ---


async def test_opportunities_command_scans_without_a_symbol_and_records_the_scan(harness: Harness) -> None:
    reply = await harness.send("/oportunidades")

    assert reply.startswith("Escáner de oportunidades · ")
    for fragment in (
        "SIMULACIÓN EXPLORATORIA", "Instrumentos escaneados (3): EUR/USD, GBP/USD, USD/JPY", "Señales técnicas detectadas:",
        "Ranking exploratorio", "1. EUR/USD · COMPRA (LONG) · CANDIDATA EXPLORATORIA", "Stop-loss", "Take-profit",
        "Riesgo/beneficio tras costos:", "Riesgo hipotético:", "Tamaño:", "Validación histórica: NO VALIDADA",
        "Expectativa neta: NO VALIDADA", "velas de 1 hora", "Operaciones autorizadas: ninguna", "no garantiza",
    ):
        assert fragment in reply, fragment
    assert len(harness.opportunity_ledger.records()) == 1
    assert harness.trading_journal.signals() == ()  # nothing was opened or recorded as a trade
    assert {ticker for ticker, _, _ in harness.intraday.intraday_fetched} == {"EURUSD=X", "GBPUSD=X", "USDJPY=X"}


async def test_opportunities_report_is_split_into_telegram_sized_messages(harness: Harness) -> None:
    from investment_committee.interfaces.telegram.adapter import TELEGRAM_MESSAGE_LIMIT

    await harness.send("/oportunidades")

    assert harness.bot.messages and all(len(text) <= TELEGRAM_MESSAGE_LIMIT for _, text in harness.bot.messages)


# --- /simular_mes, /seguimiento, /portafolio, /alertas (fakes only) ---


async def test_replay_month_compares_the_three_policies(harness: Harness) -> None:
    reply = await harness.send("/simular_mes")

    assert reply.startswith("Simulación histórica · ")
    for fragment in ("backtest retrospectivo", "No operar (referencia): 10.000,00 USD", "Política A", "Política B", "Política C",
                     "Comité de IA: no se usaron decisiones de DeepSeek", "Sin operaciones: capital final 10.000,00 USD (igual que no operar)"):
        assert fragment in reply, fragment
    assert harness.trading_journal.signals() == ()


async def test_tracker_follows_candidates_emitted_by_the_scanner(harness: Harness) -> None:
    empty = await harness.send("/seguimiento")
    assert "Todavía no hay candidatas registradas" in empty

    await harness.send("/oportunidades")
    reply = await harness.send("/seguimiento")

    assert "CANDIDATAS OBSERVADAS, NO OPERACIONES" in reply
    assert "- EUR/USD COMPRA (LONG) · detectada" in reply and "PENDIENTE" in reply
    assert harness.trading_journal.signals() == ()


async def test_portfolio_and_alerts_commands(harness: Harness) -> None:
    from investment_committee.portfolio.guardian import Side, forex_position

    empty = await harness.send("/portafolio")
    assert "No hay posiciones registradas" in empty

    now = harness.intraday.now
    harness.positions.add(forex_position("p1", "EUR/USD", Side.SHORT, Decimal("10000"), Decimal("1.20000"), now), now)
    reply = await harness.send("/portafolio")
    assert "EURUSD=X SHORT" in reply and "P&L no realizado +" in reply and "no se envía ninguna orden" in reply

    alerts = await harness.send("/alertas")
    assert "Comprobación manual" in alerts and "Reglas configuradas:" in alerts
    assert "ACTIVADA: EURUSD=X SHORT: el activo cayó" in alerts and "favorable para esta posición SHORT" in alerts
    again = await harness.send("/alertas")
    assert "Sin novedades en esta comprobación." in again  # deduplicated


async def test_old_portfolio_command_keeps_its_meaning(harness: Harness) -> None:
    reply = await harness.send("/portfolio")

    assert "todavía no está disponible" in reply
