"""Conversation domain model.

See docs/specs/spec-006-conversational-interface.md S10. Every dataclass
here except ConversationSession is frozen and self-validating, matching
this codebase's house style for anything representing a fact once true
(S6); ConversationSession is the sole deliberate exception (S20) - it is
ephemeral UI-layer state, not domain data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from investment_committee.domain import Asset
from investment_committee.memory import DecisionRecordId

from ._validation import require_timezone_aware


class ChannelType(str, Enum):
    """Which messaging surface a message/response travels over.

    New channels add one member here and one ConversationAdapter
    implementation (interfaces/<channel>/) - no other change (S8).
    """

    TELEGRAM = "TELEGRAM"
    # Future: DISCORD, SLACK, WHATSAPP, WEB, VOICE_ASSISTANT.


@dataclass(frozen=True)
class UserIdentity:
    """A real person, identified by a channel-stable, opaque id."""

    user_id: str
    display_name: str | None

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ValueError("UserIdentity.user_id must not be blank")


@dataclass(frozen=True)
class ConversationIdentity:
    """One channel's notion of 'where this conversation happens' - a chat id,
    a channel id, a thread id. Opaque and channel-defined; conversation/
    never interprets its structure."""

    channel: ChannelType
    channel_conversation_id: str

    def __post_init__(self) -> None:
        if not self.channel_conversation_id.strip():
            raise ValueError("ConversationIdentity.channel_conversation_id must not be blank")


@dataclass(frozen=True)
class AudioPayload:
    """Raw audio bytes plus enough metadata to transcribe or play them back."""

    data: bytes
    mime_type: str
    duration_seconds: Decimal | None

    def __post_init__(self) -> None:
        if not self.data:
            raise ValueError("AudioPayload.data must not be empty")
        if not self.mime_type.strip():
            raise ValueError("AudioPayload.mime_type must not be blank")


@dataclass(frozen=True)
class ConversationMessage:
    """One inbound message, from any channel, in either text or voice form.

    Exactly one of text/audio is set - mirrors EvidenceItem's restricted
    Decimal | str union (spec-002): a message is either already text or is
    audio awaiting transcription (S18), never both, never neither.
    """

    conversation_identity: ConversationIdentity
    user_identity: UserIdentity
    text: str | None
    audio: AudioPayload | None
    received_at: datetime

    def __post_init__(self) -> None:
        if (self.text is None) == (self.audio is None):
            raise ValueError("ConversationMessage requires exactly one of text or audio")
        if self.text is not None and not self.text.strip():
            raise ValueError("ConversationMessage.text must not be blank")
        require_timezone_aware(self.received_at, "received_at")


@dataclass(frozen=True)
class ConversationResponse:
    """One outbound response. audio is an optional TTS companion to text -
    additive, not an alternative; text is always present."""

    text: str
    audio: AudioPayload | None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("ConversationResponse.text must not be blank")


class IntentType(str, Enum):
    ANALYZE_ASSET = "ANALYZE_ASSET"
    EXPLAIN_DECISION = "EXPLAIN_DECISION"
    DECISION_HISTORY = "DECISION_HISTORY"
    COMPARE_ASSETS = "COMPARE_ASSETS"
    PORTFOLIO_SUMMARY = "PORTFOLIO_SUMMARY"
    PERFORMANCE_SUMMARY = "PERFORMANCE_SUMMARY"
    HELP = "HELP"
    UNSUPPORTED_TRADE_REQUEST = "UNSUPPORTED_TRADE_REQUEST"
    UNKNOWN = "UNKNOWN"
    # Simulated trading and lessons - never a real order (CLAUDE.md).
    TRADE_SIGNAL = "TRADE_SIGNAL"
    EXPLAIN_TRADE = "EXPLAIN_TRADE"
    BACKTEST = "BACKTEST"
    SIMULATED_TRADES = "SIMULATED_TRADES"
    LEARN_CONCEPT = "LEARN_CONCEPT"
    AI_COMMITTEE = "AI_COMMITTEE"  # LLM-backed committee; /analyze stays deterministic
    OPPORTUNITIES = "OPPORTUNITIES"  # deterministic scan of the configured universe; opens nothing
    REPLAY_MONTH = "REPLAY_MONTH"  # historical replay of the scanner, policies A/B/C
    OPPORTUNITY_TRACKER = "OPPORTUNITY_TRACKER"  # observed candidates, hypothetical results
    PORTFOLIO_GUARDIAN = "PORTFOLIO_GUARDIAN"  # monitored positions; never orders
    PORTFOLIO_ALERTS = "PORTFOLIO_ALERTS"  # manual alert check


@dataclass(frozen=True)
class Intent:
    """A structured request one message resolved to.

    `parameters` is intentionally a flat Mapping[str, str], matching this
    codebase's preference for plain data over rich structure (EvidenceItem's
    key/value/interpretation is the same idea) - e.g. {"symbol": "AAPL"},
    {"symbols": "MSFT,AAPL"} for CompareAssets, {"limit": "5"} for
    DecisionHistory. Each Tool (S14) is responsible for parsing its own
    expected keys; Intent itself validates nothing about their meaning.
    """

    type: IntentType
    parameters: Mapping[str, str]
    raw_text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))
        if not self.raw_text.strip():
            raise ValueError("Intent.raw_text must not be blank")


@dataclass
class ConversationSession:
    """Small, mutable, ephemeral per-conversation state (S20).

    The one deliberately non-frozen dataclass in this spec: it represents
    UI continuity, not a fact about the world. Never durable beyond
    SessionStore's own guarantees (S20); never a substitute for the
    Decision Ledger.
    """

    conversation_identity: ConversationIdentity
    last_asset: Asset | None = None
    last_record_id: DecisionRecordId | None = None
    last_trade_signal_id: str | None = None
