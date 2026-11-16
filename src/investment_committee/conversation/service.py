"""ConversationService: the channel-agnostic message flow.

See docs/specs/spec-006-conversational-interface.md S9 (message flow) and
S22 (error model). handle_message never raises: every failure becomes a
friendly ConversationResponse.

MVP deviation from S18/S25: `transcriber` is optional. The deterministic
Telegram MVP ships without Speech-to-Text, so a voice message with no
transcriber gets a polite "please type" reply instead of failing.
Access is checked before transcription (S9 lists transcription first) so an
unauthorized sender never triggers any work at all.
"""

from __future__ import annotations

import logging

from investment_committee.application import (
    AICommitteeRecordingError,
    AICommitteeUnavailableError,
    ApplicationServiceError,
    BacktestUnavailableError,
    TradeExplanation,
    TradeJournalFailedError,
    TradeSignal,
    TradeSignalNotFoundError,
    TradingNotAvailableError,
    CommitteeDecisionFailedError,
    DecisionNotFoundInApplicationError,
    DecisionQueryFailedError,
    DecisionRecordingError,
    IndicatorCalculationFailedError,
    InvalidTickerError,
    MarketDataUnavailableError,
    OpportunityScanRecordingError,
    PortfolioUnavailableError,
    ReplayUnavailableError,
    TrackerUnavailableError,
)
from investment_committee.memory import DecisionRecord

from .access import AccessPolicy
from .command_parser import CommandParser
from .errors import SynthesisError, TranscriptionError
from .intent_router import IntentRouter
from .models import AudioPayload, ConversationMessage, ConversationResponse, ConversationSession, Intent, IntentType
from .session_store import SessionStore
from .speech import SpeechToTranscriber, TextToSpeechSynthesizer
from .summarizer import ResponseSummarizer
from .symbol_resolver import canonical_symbol
from .tools.base import ToolOutcome
from .tools.router import ToolRouter

_logger = logging.getLogger(__name__)

UNAUTHORIZED_REPLY = "Lo siento, no tienes autorización para usar este bot."
VOICE_UNSUPPORTED_REPLY = (
    "Todavía no se admiten mensajes de voz. Por favor escribe tu solicitud, por ejemplo /analyze AAPL."
)
TRANSCRIPTION_FAILED_REPLY = "Lo siento, no pude entender ese mensaje de voz. Inténtalo de nuevo o escríbelo."
GENERIC_FAILURE_REPLY = "Algo salió mal. Por favor inténtalo de nuevo."
_NOTHING_RECORDED = "No se registró ninguna decisión."

# "{subject}" is the request's symbol(s) when the intent carries one, else a
# generic phrase - see _friendly_application_error.
_APPLICATION_ERROR_REPLIES: tuple[tuple[type[ApplicationServiceError], str], ...] = (
    (InvalidTickerError, "{subject} no parece un símbolo válido. Prueba con algo como /analyze AAPL o /analyze EUR/USD."),
    (
        MarketDataUnavailableError,
        "No pude obtener datos de mercado para {subject}: el proveedor de datos no respondió o no devolvió "
        "precios válidos. Revisa el símbolo e inténtalo de nuevo en unos minutos. " + _NOTHING_RECORDED,
    ),
    (
        IndicatorCalculationFailedError,
        "No pude calcular los indicadores para {subject} (¿no hay suficiente historial de precios?). "
        + _NOTHING_RECORDED,
    ),
    (CommitteeDecisionFailedError, "El comité no pudo llegar a una decisión para {subject}. " + _NOTHING_RECORDED),
    (
        DecisionRecordingError,
        "El análisis se ejecutó, pero la decisión no se pudo guardar en el historial. Por favor inténtalo de nuevo.",
    ),
    (DecisionNotFoundInApplicationError, "No encontré esa decisión en el historial."),
    (DecisionQueryFailedError, "No pude leer el historial de decisiones en este momento. Por favor inténtalo de nuevo."),
    (
        TradingNotAvailableError,
        "El trading simulado por ahora solo está disponible para EUR/USD: prueba con /trade EUR/USD. "
        "Para la opinión de inversión del comité sobre {subject} usa /analyze {subject}.",
    ),
    (
        AICommitteeRecordingError,
        "El comité de IA terminó su análisis, pero no se pudo guardar en el registro de auditoría. Inténtalo de nuevo.",
    ),
    (TradeSignalNotFoundError, "Todavía no hay propuestas de trading simulado. Prueba primero con /trade EUR/USD."),
    (
        BacktestUnavailableError,
        "No hay suficientes datos históricos de velas de 1 hora para hacer un backtest de {subject} en este momento.",
    ),
    (ReplayUnavailableError, "No se pudo reproducir el mes: el proveedor no entregó velas de 1 hora suficientes para ese periodo."),
    (TrackerUnavailableError, "No pude leer o guardar el registro de seguimiento de candidatas. Inténtalo de nuevo."),
    (PortfolioUnavailableError, "No pude leer el registro del portafolio o de alertas. Inténtalo de nuevo."),
    (
        OpportunityScanRecordingError,
        "El escaneo de oportunidades terminó, pero no se pudo guardar en el registro de auditoría, así que no se "
        "muestra. Inténtalo de nuevo.",
    ),
    (
        TradeJournalFailedError,
        "No pude leer o guardar el registro de operaciones simuladas. Por favor inténtalo de nuevo.",
    ),
)
_GENERIC_SUBJECT = "ese símbolo"


class ConversationService:
    """Orchestrates one inbound message end to end (S9). Synchronous: any
    async boundary belongs to the channel adapter (S21)."""

    def __init__(
        self,
        access_policy: AccessPolicy,
        command_parser: CommandParser,
        intent_router: IntentRouter,
        tool_router: ToolRouter,
        response_summarizer: ResponseSummarizer,
        session_store: SessionStore,
        transcriber: SpeechToTranscriber | None = None,
        synthesizer: TextToSpeechSynthesizer | None = None,
    ) -> None:
        self._access_policy = access_policy
        self._command_parser = command_parser
        self._intent_router = intent_router
        self._tool_router = tool_router
        self._response_summarizer = response_summarizer
        self._session_store = session_store
        self._transcriber = transcriber
        self._synthesizer = synthesizer

    def handle_message(self, message: ConversationMessage) -> ConversationResponse:
        """Never raises (S22)."""
        try:
            return self._handle(message)
        except Exception:  # noqa: BLE001 - the one permitted broad catch (S22): a human is on the other end.
            _logger.exception("Unexpected error while handling a conversation message")
            return ConversationResponse(text=GENERIC_FAILURE_REPLY, audio=None)

    def _handle(self, message: ConversationMessage) -> ConversationResponse:
        if not self._access_policy.is_authorized(message.user_identity):
            _logger.info("Rejected message from unauthorized user")
            return _reply(UNAUTHORIZED_REPLY)

        text = message.text
        if message.audio is not None:
            if self._transcriber is None:
                return _reply(VOICE_UNSUPPORTED_REPLY)
            try:
                text = self._transcriber.transcribe(message.audio)
            except TranscriptionError:
                _logger.info("Voice transcription failed", exc_info=True)
                return _reply(TRANSCRIPTION_FAILED_REPLY)
        if text is None or not text.strip():
            return _reply(TRANSCRIPTION_FAILED_REPLY)

        session = self._session_store.get(message.conversation_identity)
        intent = self._command_parser.parse(text)
        if intent is None:
            intent = self._intent_router.route(text, session)
        _logger.info("Routed message to intent %s", intent.type.value)

        tool = self._tool_router.tool_for(intent.type)
        try:
            outcome = tool.execute(intent, session)
        except ApplicationServiceError as exc:
            _logger.warning("Application error for intent %s: %s: %s", intent.type.value, type(exc).__name__, exc)
            return _reply(_friendly_application_error(exc, intent))

        _remember(session, outcome, intent)
        text_reply = self._response_summarizer.summarize(outcome, intent)
        audio = self._synthesize(text_reply)
        self._session_store.save(session)
        return ConversationResponse(text=text_reply, audio=audio)

    def _synthesize(self, text: str) -> AudioPayload | None:
        if self._synthesizer is None:
            return None
        try:
            return self._synthesizer.synthesize(text)
        except SynthesisError:
            _logger.info("Speech synthesis failed; replying with text only", exc_info=True)
            return None


def _reply(text: str) -> ConversationResponse:
    return ConversationResponse(text=text, audio=None)


def _friendly_application_error(exc: ApplicationServiceError, intent: Intent) -> str:
    if isinstance(exc, AICommitteeUnavailableError):
        symbol = canonical_symbol(intent.parameters.get("symbol", "")) or "AAPL"
        return (
            f"El comité de IA no está disponible: {exc.reason}. Mientras tanto puedes usar /analyze {symbol} "
            "(comité determinístico de reglas), /trade EUR/USD y /backtest EUR/USD."
        )
    raw_subject = intent.parameters.get("symbol") or intent.parameters.get("symbols", "").replace(",", " / ")
    subject = canonical_symbol(raw_subject) if "symbol" in intent.parameters else raw_subject
    for error_type, reply in _APPLICATION_ERROR_REPLIES:
        if isinstance(exc, error_type):
            return reply.format(subject=subject.strip() or _GENERIC_SUBJECT)
    return GENERIC_FAILURE_REPLY


def _remember(session: ConversationSession, outcome: ToolOutcome, intent: Intent) -> None:
    """Update the session's two context slots after an outcome that carries
    a freshly produced or explained DecisionRecord (S20). For a comparison,
    the second record wins. History listings don't change the context."""
    records: list[DecisionRecord] = []
    if isinstance(outcome, DecisionRecord):
        records = [outcome]
    elif intent.type is IntentType.COMPARE_ASSETS and isinstance(outcome, tuple):
        records = [r for r in outcome if isinstance(r, DecisionRecord)]
    if records:
        session.last_asset = records[-1].asset
        session.last_record_id = records[-1].record_id
    if isinstance(outcome, TradeSignal):
        session.last_trade_signal_id = outcome.signal_id
    elif isinstance(outcome, TradeExplanation):
        session.last_trade_signal_id = outcome.signal.signal_id
