"""Tests for conversation.service.ConversationService. See spec-006 S9, S22, S26.

Every collaborator is a fake; handle_message must never raise.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_committee.application import (
    CommitteeDecisionFailedError,
    DecisionNotFoundInApplicationError,
    DecisionQueryFailedError,
    DecisionRecordingError,
    IndicatorCalculationFailedError,
    InvalidTickerError,
    MarketDataUnavailableError,
)
from investment_committee.conversation import (
    AllowlistAccessPolicy,
    AudioPayload,
    ChannelType,
    CommandParser,
    ConversationIdentity,
    ConversationMessage,
    ConversationService,
    ConversationSession,
    InMemorySessionStore,
    Intent,
    IntentRouter,
    IntentType,
    SpeechToTranscriber,
    TemplateResponseSummarizer,
    TranscriptionError,
    UserIdentity,
)
from investment_committee.conversation.tools import HelpTool, Tool, ToolOutcome, ToolRouter

from .tools._support import make_record

_IDENTITY = ConversationIdentity(channel=ChannelType.TELEGRAM, channel_conversation_id="chat-1")
_NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


def _text(text: str, user_id: str = "42") -> ConversationMessage:
    return ConversationMessage(
        conversation_identity=_IDENTITY,
        user_identity=UserIdentity(user_id=user_id, display_name=None),
        text=text,
        audio=None,
        received_at=_NOW,
    )


def _voice(user_id: str = "42") -> ConversationMessage:
    return ConversationMessage(
        conversation_identity=_IDENTITY,
        user_identity=UserIdentity(user_id=user_id, display_name=None),
        text=None,
        audio=AudioPayload(data=b"ogg", mime_type="audio/ogg", duration_seconds=Decimal("1")),
        received_at=_NOW,
    )


class _RecordingRouter(IntentRouter):
    def __init__(self, intent_type: IntentType = IntentType.ANALYZE_ASSET) -> None:
        self.calls: list[str] = []
        self._intent_type = intent_type

    def route(self, text: str, session: ConversationSession) -> Intent:
        self.calls.append(text)
        return Intent(type=self._intent_type, parameters={"symbol": "AAPL"}, raw_text=text)


class _FakeTool(Tool):
    def __init__(self, outcome: ToolOutcome | None = None, error: Exception | None = None) -> None:
        self.calls: list[Intent] = []
        self._outcome = outcome if outcome is not None else make_record("AAPL", "rec-1")
        self._error = error

    def execute(self, intent: Intent, session: ConversationSession) -> ToolOutcome:
        self.calls.append(intent)
        if self._error is not None:
            raise self._error
        return self._outcome


class _CountingSessionStore(InMemorySessionStore):
    def __init__(self) -> None:
        super().__init__()
        self.gets = 0
        self.saves = 0

    def get(self, conversation_identity: ConversationIdentity) -> ConversationSession:
        self.gets += 1
        return super().get(conversation_identity)

    def save(self, session: ConversationSession) -> None:
        self.saves += 1
        super().save(session)


class _FakeTranscriber(SpeechToTranscriber):
    def __init__(self, text: str | None = None) -> None:
        self.calls = 0
        self._text = text

    def transcribe(self, audio: AudioPayload) -> str:
        self.calls += 1
        if self._text is None:
            raise TranscriptionError("unintelligible")
        return self._text


def _service(
    tool: Tool | None = None,
    router: IntentRouter | None = None,
    store: InMemorySessionStore | None = None,
    transcriber: SpeechToTranscriber | None = None,
) -> ConversationService:
    tool = tool if tool is not None else _FakeTool()
    tools = {intent_type: HelpTool() for intent_type in IntentType}
    tools[IntentType.ANALYZE_ASSET] = tool
    tools[IntentType.DECISION_HISTORY] = tool
    return ConversationService(
        access_policy=AllowlistAccessPolicy(frozenset({"42"})),
        command_parser=CommandParser(),
        intent_router=router if router is not None else _RecordingRouter(),
        tool_router=ToolRouter(tools),
        response_summarizer=TemplateResponseSummarizer(),
        session_store=store if store is not None else InMemorySessionStore(),
        transcriber=transcriber,
    )


def test_unauthorized_user_short_circuits_everything() -> None:
    tool, router, store = _FakeTool(), _RecordingRouter(), _CountingSessionStore()

    response = _service(tool, router, store).handle_message(_text("/analyze AAPL", user_id="999"))

    assert "no tienes autorización" in response.text
    assert "999" not in response.text
    assert (tool.calls, router.calls, store.gets, store.saves) == ([], [], 0, 0)


def test_free_text_goes_through_intent_router_and_updates_session() -> None:
    tool, router, store = _FakeTool(), _RecordingRouter(), _CountingSessionStore()

    response = _service(tool, router, store).handle_message(_text("Analyze Apple"))

    assert router.calls == ["Analyze Apple"]
    assert len(tool.calls) == 1
    assert response.text.startswith("Análisis del comité: AAPL")
    session = store.get(_IDENTITY)
    assert session.last_record_id == "rec-1"
    assert session.last_asset is not None and session.last_asset.ticker == "AAPL"
    assert store.saves == 1


def test_explicit_command_never_reaches_intent_router() -> None:
    tool, router = _FakeTool(), _RecordingRouter()

    _service(tool, router).handle_message(_text("/history AAPL"))

    assert router.calls == []
    assert tool.calls[0].type is IntentType.DECISION_HISTORY


@pytest.mark.parametrize(
    ("error", "expected_fragment"),
    [
        (InvalidTickerError("bad"), "AAPL no parece un símbolo válido"),
        (MarketDataUnavailableError("down"), "No pude obtener datos de mercado para AAPL"),
        (IndicatorCalculationFailedError("short"), "No pude calcular los indicadores para AAPL"),
        (CommitteeDecisionFailedError("no"), "El comité no pudo llegar a una decisión para AAPL"),
        (DecisionRecordingError("disk", analysis_result=None), "no se pudo guardar"),  # type: ignore[arg-type]
        (DecisionNotFoundInApplicationError("gone"), "No encontré esa decisión"),
        (DecisionQueryFailedError("io"), "historial de decisiones"),
    ],
)
def test_application_errors_become_friendly_replies(error: Exception, expected_fragment: str) -> None:
    response = _service(_FakeTool(error=error)).handle_message(_text("/analyze AAPL"))

    assert expected_fragment in response.text


def test_unexpected_exception_is_caught_with_generic_reply() -> None:
    response = _service(_FakeTool(error=RuntimeError("boom"))).handle_message(_text("/analyze AAPL"))

    assert "Algo salió mal" in response.text
    assert "boom" not in response.text


def test_voice_without_transcriber_asks_user_to_type() -> None:
    router, tool = _RecordingRouter(), _FakeTool()

    response = _service(tool, router).handle_message(_voice())

    assert "escribe tu solicitud" in response.text
    assert (router.calls, tool.calls) == ([], [])


def test_voice_with_transcriber_follows_the_text_path() -> None:
    transcriber, router = _FakeTranscriber("Analyze Apple"), _RecordingRouter()

    response = _service(router=router, transcriber=transcriber).handle_message(_voice())

    assert transcriber.calls == 1
    assert router.calls == ["Analyze Apple"]
    assert response.text.startswith("Análisis del comité: AAPL")


def test_transcription_error_short_circuits() -> None:
    router = _RecordingRouter()

    response = _service(router=router, transcriber=_FakeTranscriber(None)).handle_message(_voice())

    assert "no pude entender ese mensaje de voz" in response.text
    assert router.calls == []


def test_market_data_error_names_the_canonical_forex_symbol() -> None:
    response = _service(_FakeTool(error=MarketDataUnavailableError("down"))).handle_message(_text("/analyze EUR/USD"))

    assert response.text.startswith("No pude obtener datos de mercado para EURUSD=X")
    assert "No se registró ninguna decisión." in response.text


def test_application_error_without_a_symbol_uses_a_generic_subject() -> None:
    response = _service(_FakeTool(error=InvalidTickerError("blank"))).handle_message(_text("/analyze"))

    assert response.text.startswith("ese símbolo no parece un símbolo válido")
