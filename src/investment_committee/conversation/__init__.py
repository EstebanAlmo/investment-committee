"""Conversational Interface: turns free-form and voice messages into
Application Service calls and grounded, human-readable replies.

See docs/architecture.md and docs/specs/spec-006-conversational-interface.md.
`conversation/` depends on `application/` for every behavioral call, and on
`domain/`/`memory/` for type references only (S8) - never on `committee/`,
`indicators/`, `market_intelligence/`, or `interfaces/` directly.

This package delivers the deterministic conversational pipeline used by the
Telegram MVP: CommandParser, RuleBasedIntentRouter, symbol resolution,
asset-class eligibility, Tools (conversation.tools), TemplateResponseSummarizer,
ConversationService, and the build_default_conversation_service composition
root. The optional LLM-backed router/summarizer and speech adapters are not
built.
"""

from __future__ import annotations

from .access import AccessPolicy, AllowlistAccessPolicy
from .adapters import ConversationAdapter
from .asset_policy import AssetClassDecision, AssetClassPolicy, TickerPatternAssetClassPolicy
from .command_parser import CommandParser
from .errors import (
    ConversationError,
    LLMInterpretationError,
    SessionStoreError,
    SynthesisError,
    TranscriptionError,
)
from .factory import DEFAULT_NAME_TO_TICKER, build_default_conversation_service
from .intent_router import IntentRouter, RuleBasedIntentRouter
from .models import (
    AudioPayload,
    ChannelType,
    ConversationIdentity,
    ConversationMessage,
    ConversationResponse,
    ConversationSession,
    Intent,
    IntentType,
    UserIdentity,
)
from .service import ConversationService
from .session_store import InMemorySessionStore, SessionStore
from .speech import SpeechToTranscriber, TextToSpeechSynthesizer
from .summarizer import ResponseSummarizer, TemplateResponseSummarizer
from .symbol_resolver import StaticSymbolResolver, SymbolResolver

__all__ = [
    # models
    "ChannelType",
    "UserIdentity",
    "ConversationIdentity",
    "AudioPayload",
    "ConversationMessage",
    "ConversationResponse",
    "IntentType",
    "Intent",
    "ConversationSession",
    # ports and their default/optional adapters
    "AccessPolicy",
    "AllowlistAccessPolicy",
    "CommandParser",
    "IntentRouter",
    "RuleBasedIntentRouter",
    "SymbolResolver",
    "StaticSymbolResolver",
    "AssetClassDecision",
    "AssetClassPolicy",
    "TickerPatternAssetClassPolicy",
    "SessionStore",
    "InMemorySessionStore",
    "SpeechToTranscriber",
    "TextToSpeechSynthesizer",
    "ResponseSummarizer",
    "TemplateResponseSummarizer",
    "ConversationAdapter",
    # orchestration
    "ConversationService",
    "build_default_conversation_service",
    "DEFAULT_NAME_TO_TICKER",
    # errors
    "ConversationError",
    "TranscriptionError",
    "SynthesisError",
    "SessionStoreError",
    "LLMInterpretationError",
]
