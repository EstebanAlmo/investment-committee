"""Tests for investment_committee.conversation.speech ports.

See docs/specs/spec-006-conversational-interface.md S18. No concrete
adapter exists yet (out of scope for this phase) - these tests confirm the
port shape only: neither ABC can be instantiated directly, and a minimal
duck-typed implementation satisfies the contract.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from investment_committee.conversation.errors import SynthesisError, TranscriptionError
from investment_committee.conversation.models import AudioPayload
from investment_committee.conversation.speech import SpeechToTranscriber, TextToSpeechSynthesizer


class _FakeTranscriber(SpeechToTranscriber):
    def transcribe(self, audio: AudioPayload) -> str:
        return "transcribed text"


class _FailingTranscriber(SpeechToTranscriber):
    def transcribe(self, audio: AudioPayload) -> str:
        raise TranscriptionError("could not transcribe")


class _FakeSynthesizer(TextToSpeechSynthesizer):
    def synthesize(self, text: str) -> AudioPayload:
        return AudioPayload(data=b"audio-bytes", mime_type="audio/ogg", duration_seconds=None)


def test_speech_to_transcriber_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        SpeechToTranscriber()  # type: ignore[abstract]


def test_text_to_speech_synthesizer_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        TextToSpeechSynthesizer()  # type: ignore[abstract]


def test_concrete_transcriber_returns_text() -> None:
    audio = AudioPayload(data=b"raw", mime_type="audio/ogg", duration_seconds=Decimal("1.5"))

    assert _FakeTranscriber().transcribe(audio) == "transcribed text"


def test_concrete_transcriber_may_raise_transcription_error() -> None:
    audio = AudioPayload(data=b"raw", mime_type="audio/ogg", duration_seconds=None)

    with pytest.raises(TranscriptionError):
        _FailingTranscriber().transcribe(audio)


def test_concrete_synthesizer_returns_audio_payload() -> None:
    result = _FakeSynthesizer().synthesize("pong")

    assert isinstance(result, AudioPayload)


def test_synthesis_error_is_a_conversation_error_subclass() -> None:
    from investment_committee.conversation.errors import ConversationError

    assert issubclass(SynthesisError, ConversationError)
