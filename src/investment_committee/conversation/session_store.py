"""Conversation session storage.

See docs/specs/spec-006-conversational-interface.md S20.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import ConversationIdentity, ConversationSession


class SessionStore(ABC):
    @abstractmethod
    def get(self, conversation_identity: ConversationIdentity) -> ConversationSession:
        """Returns a fresh, empty ConversationSession if none exists yet - never raises."""
        raise NotImplementedError

    @abstractmethod
    def save(self, session: ConversationSession) -> None:
        raise NotImplementedError


class InMemorySessionStore(SessionStore):
    """Process-local, non-durable. The only adapter this Epic ships -
    a durable session store is an explicit non-goal (S5), not merely
    deferred: this state is meant to be disposable.
    """

    def __init__(self) -> None:
        self._sessions: dict[ConversationIdentity, ConversationSession] = {}

    def get(self, conversation_identity: ConversationIdentity) -> ConversationSession:
        return self._sessions.get(
            conversation_identity, ConversationSession(conversation_identity=conversation_identity)
        )

    def save(self, session: ConversationSession) -> None:
        self._sessions[session.conversation_identity] = session
