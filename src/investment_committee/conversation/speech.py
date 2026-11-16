"""Voice support ports.

See docs/specs/spec-006-conversational-interface.md S18. Both ports live
here, not in interfaces/telegram/, so they stay reachable by any future
ConversationAdapter, not just a Telegram one. No concrete adapter is built
in this phase - a required Speech-to-Text adapter and an optional
Text-to-Speech adapter live behind integrations/speech/, out of scope here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .errors import SynthesisError, TranscriptionError  # noqa: F401 - re-exported for callers
from .models import AudioPayload


class SpeechToTranscriber(ABC):
    @abstractmethod
    def transcribe(self, audio: AudioPayload) -> str:
        """Raises TranscriptionError if the audio cannot be transcribed."""
        raise NotImplementedError


class TextToSpeechSynthesizer(ABC):
    @abstractmethod
    def synthesize(self, text: str) -> AudioPayload:
        """Raises SynthesisError if synthesis fails."""
        raise NotImplementedError
