"""ConversationError hierarchy.

See docs/specs/spec-006-conversational-interface.md S22, S23.
`LLMInterpretationError` is shared by `LLMIntentRouter` and
`LLMResponseSummarizer` (S12, S17) - neither is built in this phase, but
the exception type is part of this module's full S23 scope regardless of
which raisers exist yet. `SessionStoreError` is likewise unused by
`InMemorySessionStore` (S20's only adapter never raises) and exists for a
future durable SessionStore adapter.
"""

from __future__ import annotations


class ConversationError(Exception):
    """Base class for every error the conversation layer can raise."""


class TranscriptionError(ConversationError):
    """Raised by SpeechToTranscriber.transcribe when audio cannot be transcribed."""


class SynthesisError(ConversationError):
    """Raised by TextToSpeechSynthesizer.synthesize when synthesis fails."""


class SessionStoreError(ConversationError):
    """Raised by a SessionStore adapter that can fail (e.g. a durable one)."""


class LLMInterpretationError(ConversationError):
    """Raised when an LLM-backed collaborator's call or output fails validation."""
