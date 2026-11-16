# Spec 006 - Conversational Interface

## 1. Status

Draft. Spec-only — no production code has been written or modified for this
Epic (spec → architecture review → implementation → code review → tests →
commit → merge, matching the process spec-005 followed). This document
defines the architecture for a new package; it does not implement it.

## 2. Context

Epics 1–7 are complete and implemented: `domain/`, `market_intelligence/`,
`indicators/`, `committee/` (deterministic baseline, ADR-001),
`application/` (`ApplicationService.analyze_asset`,
`RecordDecisionUseCase.record` / `.analyze_and_record`), and `memory/` (the
Decision Ledger — `DecisionRecord`, `DecisionLedger`,
`JsonLinesDecisionLedger`, `InMemoryDecisionLedger`). The only way to use
any of this today is the dev CLI (`python -m investment_committee analyze
<SYMBOL>`, `src/investment_committee/cli.py`) — a single command, a single
ticker, no history, no explanation, no natural language.

`docs/roadmap.md` originally scheduled a separate "Epic 10 — Telegram
Interface" as "the first real user-facing interface." **This iteration of
the spec supersedes that sequencing: Epic 8, defined by this document, is
the epic that delivers a working, local-first Telegram conversational
interface** — explicit commands, free-form natural-language text, and voice
messages, all grounded in the existing Application Services and Decision
Ledger. Telegram is not a future adapter sketched for later implementation;
it is this Epic's concrete, tested deliverable (§21). The
`ConversationAdapter` port is still defined as a small, channel-shaped
abstraction rather than a Telegram-specific class — purely so the SDK
isolation `CLAUDE.md` already requires for external providers ("external
providers... sit behind an interface/adapter") holds for the Telegram Bot
API too — but Epic 8 builds, tests, and ships exactly one adapter. No other
channel is an Epic 8 goal (§5, §7, §8).

This revision also introduces, for the first time in this codebase, an
optional LLM provider (§12, §17) — a deliberate, explicit product decision
for Epic 8 specifically, superseding `CLAUDE.md`'s earlier default caution
about paid LLM APIs for this one, scoped use: natural-language intent
interpretation, clarification, and grounded response summarization. The
deterministic committee (`committee/`) and every numeric calculation remain
entirely inside the existing, unmodified Application Services — the LLM is
never given the means to compute or store anything itself (§8, §12, §14,
§17).

## 3. Problem

Nothing in this codebase today understands "Should I buy Nvidia?" or
"What have we recommended for Tesla?" as anything other than a `ValueError`
from `ApplicationService.analyze_asset("Should I buy Nvidia?")` (an invalid
ticker). Every existing entry point requires the caller to already know the
system's vocabulary — a bare ticker string, nothing else. A conversational
assistant needs to: turn free-form natural language into a structured
request, decide which already-existing Application Service capability
answers it, execute that capability (never re-deriving or approximating
what it already computes), and turn the structured result back into
readable text — all without ever letting free-form generation stand in for
a financial calculation or a stored fact.

## 4. Goals

- Deliver a working Telegram conversational interface (§21): explicit
  `/commands`, free-form natural-language text, and voice messages, all
  grounded in the existing Application Services and Decision Ledger. The
  channel-shaped `ConversationAdapter` port (§8, §21) keeps a future
  channel from requiring a redesign, but Telegram is this Epic's only
  built and tested adapter.
- Support the eight intents named in the Epic prompt — `AnalyzeAsset`,
  `ExplainDecision`, `DecisionHistory`, `CompareAssets`, `PortfolioSummary`,
  `PerformanceSummary`, `Help`, plus a required ninth
  (`UnsupportedTradeRequest`, §15.8) — reachable through both deterministic
  `/commands` and free-form natural language, without the user needing to
  memorize either (§12, §15).
- Make tool routing explicit and auditable: an LLM, when configured, is
  used only to interpret free-form text into a structured `Intent`, ask a
  clarifying question, or summarize an already-computed result — it never
  computes, and it never answers from anything other than structured data
  an Application Service call already produced. Every intent the LLM can
  select is drawn from a closed, constructor-validated allowlist (§14); a
  fully deterministic router and summarizer are always available and used
  automatically if the LLM is disabled, unavailable, or returns invalid
  output (§12, §17).
- Keep explicit Telegram commands (`/help`, `/analyze AAPL`, `/history
  AAPL`) on a wholly deterministic parsing path that never invokes the LLM,
  converging on the same `Intent`/`ToolRouter`/`Tool` pipeline free-form
  text uses (§9, §12).
- Treat voice as a required Epic 8 capability, not a future extension: a
  Telegram voice message is downloaded by the Telegram adapter, transcribed
  by a configured Speech-to-Text adapter, and re-enters exactly the same
  intent-routing and orchestration pipeline typed text uses, ending in a
  text reply. Text-to-Speech remains optional (§18).
- Reject unsupported asset classes — crypto, commodities (including
  XAU/USD), options, and futures — before any Application Service call.
  Epic 8 supports only equities, ETFs, and Forex currency pairs (§13).
- Keep conversational memory intentionally small: a handful of named
  context slots for follow-up questions, not a transcript log — the system
  of record for "what did we decide" remains the Decision Ledger (§20).
- Extend `application/` with exactly the one read capability it is
  currently missing — querying recorded decisions — because the
  conversational layer is not permitted to reach `memory/` directly (§16).

## 5. Non-goals

Per the Epic prompt and `CLAUDE.md`:

- No broker execution, no autonomous trading, no agent planning, no
  long-term memory, no multi-agent systems, no workflow automation.
- No generic, reusable confirmation-flow mechanism. Epic 8 has no
  execution-capable tool to gate, and designing one now would be premature
  architecture for a future broker-execution epic — removed from this
  spec's normative scope (§19).
- **LLM use is scoped, not general-purpose.** `CLAUDE.md`'s earlier default
  caution about paid LLM APIs is explicitly superseded for this one Epic
  and this one use: intent interpretation, clarification, and grounded
  response summarization (§12, §17). The LLM must not invent market data,
  produce the authoritative investment recommendation, bypass Application
  Services, access infrastructure adapters directly, write to the Decision
  Ledger, execute arbitrary tools, or create tool/intent names dynamically
  — every one of these is prevented structurally, not just by instruction
  (§12, §14, §17). A fully deterministic path (`RuleBasedIntentRouter`,
  `CommandParser`, `TemplateResponseSummarizer`) remains shipped and is
  always what Epic 8 falls back to when the LLM is disabled, unavailable,
  or returns output that fails validation.
- No `BuyAsset`/`SellAsset`/any execution intent. Trade-shaped language is
  recognized only well enough to decline it safely (§15.8) — this is a
  *refusal* path, not a first broker feature.
- No comparative-scoring domain logic for `CompareAssets` — it presents two
  independently-produced decisions side by side; it does not compute a new
  "which is better" verdict. That is a different, unrelated concept from
  Epic 13's Strategy Comparison — see §7's terminology table.
- **No support for crypto, commodities (including XAU/USD), options, or
  futures**, at any layer. `AnalyzeAssetTool`/`CompareAssetsTool` reject
  these ticker shapes before any Application Service call (§13, §15.1,
  §15.4) — they are never analyzed, never fetched, and never written to
  the Decision Ledger.
- No text-to-speech adapter is required for Epic 8's acceptance — the
  `TextToSpeechSynthesizer` port exists and may be wired, but voice replies
  remain optional (§18). A Speech-to-Text adapter, by contrast, **is**
  required — see §18.
- No additional channel beyond Telegram. `ConversationAdapter`,
  `ChannelType`, and the rest of `conversation/`'s core stay channel-
  shaped rather than Telegram-specific, but no Discord/Slack/WhatsApp/Web
  adapter is designed, built, or tested in this Epic (§7, §8).
- No `education/` implementation — still an empty placeholder package
  today. `ResponseSummarizer`'s default is template-based; delegating to
  `education/` for beginner-friendly explanations is a flagged future seam
  (§17, §30), not built here.
- No FastAPI, web server, or Web UI channel implementation — `CLAUDE.md`'s
  "No FastAPI... yet" stands.
- No portfolio or performance data — `portfolio/`, `simulation/`,
  `evaluation/` remain empty placeholders. `PortfolioSummary` and
  `PerformanceSummary` are designed as intents and routed to a tool that
  returns a structured "not yet available" outcome, not a fabricated
  answer (§15.5, §15.6).
- No modification of any existing file, and no new `domain.AssetType`
  member. `application/factory.py` and `application/__init__.py` gain
  proposed additions (§16, §24), shown as diffs, matching spec-005's
  precedent for touching an existing composition root without this
  document actually editing it; §13 explains why the asset-class
  restriction is implemented without touching `domain/assets.py`.

## 6. Existing architecture this spec relies on

- **Port + adapter, twice-precedented, now shipped a third time**:
  `MarketDataProvider` (`market_intelligence/providers/base.py`) →
  `YFinanceProvider`; `DecisionLedger` (`memory/ledgers/base.py`) →
  `JsonLinesDecisionLedger`/`InMemoryDecisionLedger`. Every new
  interpretation/delivery point this spec adds (`IntentRouter`,
  `ResponseSummarizer`, `SpeechToTranscriber`,
  `TextToSpeechSynthesizer`, `AccessPolicy`, `SessionStore`,
  `ConversationAdapter`) follows the identical shape: an `ABC` port
  defined in `conversation/`, a deterministic default adapter shipped in
  this Epic, and any external-service-backed adapter living behind
  `integrations/` and wired only from a composition root (§25).
- **Composition roots stay separate from orchestration.**
  `application/factory.py` is the only file allowed to import concrete
  production implementations for `application/`'s wiring;
  `conversation/factory.py` is this spec's equivalent for `conversation/`
  (§25).
- **Deterministic floor, LLM optional layer on top — ADR-001's pattern,
  reapplied one layer up.** ADR-001 made the committee deterministic
  before any LLM analyst existed, specifically so behavior would be
  reproducible, auditable, and testable from day one. This spec applies
  the same reasoning to natural-language understanding, adapted for Epic
  8's explicit LLM approval: `RuleBasedIntentRouter`,
  `TemplateResponseSummarizer`, and `CommandParser` are shipped,
  always-available, and never depend on network access; `LLMIntentRouter`
  and `LLMResponseSummarizer` are optional, additive layers wrapped by a
  `Fallback*` adapter that transparently drops back to the deterministic
  implementation on any failure (§12, §17). The committee's own
  determinism (ADR-001) is untouched either way — the LLM never reaches
  `committee/`.
- **Typed exception hierarchies, chained with `raise ... from ...`, at
  every layer boundary** — `ApplicationServiceError` already wraps
  `MarketDataError`/`IndicatorCalculationError`/`CommitteeEngineError`;
  `DecisionRecordingError` already wraps `LedgerError`. This spec adds one
  more translation point (`ConversationService` catching
  `ApplicationServiceError`, §22) and is the first layer in this codebase
  whose outermost boundary does not re-raise a typed exception at all —
  see §22 for why that is a deliberate, narrow departure.
- **Frozen, self-validating dataclasses are the house style** for anything
  that represents a fact once true. This spec keeps that for every type
  that represents "one message," "one response," "one intent" — with
  exactly one deliberate exception, `ConversationSession`, which is
  ephemeral UI-layer state, not domain data (§20 explains why the
  exception is drawn there and nowhere else).
- **`AnalysisResult`/`DecisionRecord`'s existing shape already carries
  everything `ExplainDecision` needs.** Spec-005's "full embedding, not
  normalization" decision (spec-005 §9) means a `DecisionRecord` already
  contains the complete `IndicatorSnapshot` and every `AgentReport`'s full
  `EvidenceItem` list — "what indicators caused that decision" is
  answerable from a `DecisionRecord` alone, no new data collection
  required.

## 7. Terminology

| Term | Meaning in this document |
|---|---|
| **Channel** | A concrete messaging surface. `ChannelType` (§10) is defined as an open enum for architectural extensibility, but **Telegram is Epic 8's only member with a real adapter** — no other channel is an Epic 8 goal (§5). |
| **`ConversationAdapter`** | The channel-specific boundary class: turns a channel's native event into a `ConversationMessage`, and a `ConversationResponse` back into a channel-native reply. Epic 8 implements exactly one: `TelegramConversationAdapter` (§21). |
| **Intent** | The structured request a message resolves to — one of the `IntentType` members (§10, §12) — reached either through `CommandParser` (explicit `/commands`) or `IntentRouter` (free-form text, deterministic or LLM-backed). Both paths converge on the same `Intent` type (§9, §12). |
| **Tool** | The one-per-intent adapter over the Application Layer that actually satisfies an `Intent` (§14). Not the same concept as an LLM "tool call" in the function-calling sense, though the shape rhymes deliberately — see §14's naming note. |
| **`ToolOutcome`** | The structured, already-computed data a `Tool` returns — always a type this codebase already defines (`DecisionRecord`, `Sequence[DecisionRecord]`, etc.), never a new generic payload (§14). |
| **Asset comparison (this Epic)** | `CompareAssets` presents two assets' independently-produced `DecisionRecord`s side by side. **Not** Strategy Comparison (Epic 13, `evaluation/`), which compares committee *strategies* against each other over identical historical data. The two concepts share the English word "compare" and nothing else — flagged because the naming collision is a genuine risk for a future reader. |
| **Asset-class eligibility** | Whether a resolved ticker is one Epic 8 supports (equity, ETF, Forex) or must be rejected (crypto, commodity, option, future) — decided by `AssetClassPolicy` (§13) before any Application Service call. **Not** related to `domain.AssetType`, which this spec deliberately does not extend (§13). |
| **Conversation session** | Small, ephemeral, non-durable per-conversation state (`ConversationSession`, §20) — explicitly *not* the Decision Ledger, and not a transcript. |

## 8. Architecture overview

### Package choice: `conversation/`, not `assistant/`

**Chosen: `conversation/`.** Every type this Epic's own prompt asks for is
already named with a `Conversation` prefix (`ConversationAdapter`,
`ConversationMessage`, `ConversationResponse`) — `conversation/` names the
package after the abstraction it actually centers on, exactly like
`committee/` is named after `CommitteeRole`/`InvestmentDecision`, not after
"analysis," and `memory/` centers on the Decision Ledger, not on a vaguer
"storage" idea. `assistant/` was considered and rejected: it describes the
*product* ("an AI assistant"), not the *abstraction* this package defines,
and it invites confusion with a future LLM-backed "assistant" persona
concept that does not exist in this codebase and is explicitly out of scope
(no agent planning, §5).

### A new layer between Application Service and User Experience

`docs/architecture.md`'s current layer diagram has `interfaces/` (layer 7)
depending directly on `application/` (layer 6). This spec inserts
`conversation/` between them:

```
┌─────────────────────────────────────────────────────────────┐
│ 8. User Experience        (interfaces/)                       │
│    Telegram adapter (interfaces/telegram/) - Epic 8's only    │
│    built adapter; the port shape leaves room for a later one  │
└───────────────▲─────────────────────────────────────────────┘
                │ depends on
┌───────────────┴─────────────────────────────────────────────┐
│ 7. Conversational Interface   (conversation/)      <- NEW     │
│    ConversationService, CommandParser, IntentRouter,           │
│    ToolRouter, ResponseSummarizer, session/access/asset policy │
└───────────────▲─────────────────────────────────────────────┘
                │ depends on
┌───────────────┴─────────────────────────────────────────────┐
│ 6. Application Service     (application/)                     │
│    analyze_asset · RecordDecisionUseCase · DecisionQueryService│
│    (DecisionQueryService is this Epic's one addition — §16)    │
└─────────────────────────────────────────────────────────────┘
```

**`docs/architecture.md`'s layer diagram and "Dependency direction,
summarized" section need a corresponding edit** (add `interfaces →
conversation`, `conversation → application`, and renumber "User
Experience" to layer 8) **before this Epic's implementation PR merges** —
mirroring spec-005 §6's identical requirement for `memory → committee,
indicators`. Not performed by this spec-only pass (§5).

### Dependency direction

- `interfaces/*` (channel adapters, e.g. `interfaces/telegram/`) →
  `conversation/`. Nothing else — a channel adapter never imports
  `application/`, `memory/`, or any lower layer directly (this is the
  literal meaning of "no business logic may depend on Telegram," inverted:
  no channel adapter may contain business logic either).
- `conversation/` → `application/` for every *behavioral* call (calling a
  method, invoking a use case). `conversation/` also references types
  defined in `domain/`, `committee/`, `memory/`, and `indicators/` in
  signatures — e.g. a `Tool` returns a `DecisionRecord` (`memory/`) or
  reads `Asset` (`domain/`) off a session — exactly as `application/models.py`
  already imports `committee.InvestmentDecision`, `domain.Asset`, and
  `indicators.IndicatorSnapshot` for `AnalysisResult`'s field types
  (spec-004 §"Public API"). **Type-reference imports are allowed; calling
  a `memory/`, `committee/`, `indicators/`, or `market_intelligence/`
  function or method directly from `conversation/` is not.** This is the
  concrete meaning of "the conversational layer must never bypass the
  Application Layer."
- `conversation/factory.py` (the composition root, §25) is the one file in
  `conversation/` permitted to import concrete `integrations/` adapters —
  the LLM client behind `LLMIntentRouter`/`LLMResponseSummarizer` (§12,
  §17) and the Speech-to-Text adapter behind `SpeechToTranscriber` (§18) —
  for production wiring, mirroring `application/factory.py`'s existing
  exception for itself.
- Nothing in `application/`, `memory/`, `committee/`, `indicators/`,
  `market_intelligence/`, or `domain/` gains a dependency on
  `conversation/` or `interfaces/`.

### Extensibility is a property of the port shape, not an Epic 8 goal

`ConversationAdapter`, `ChannelType`, and the rest of `conversation/`'s
core are still defined generically enough that a future channel (Discord,
Slack, WhatsApp, a Web UI) would need only one new `ConversationAdapter`
implementation and one new `ChannelType` member — no change to
`IntentRouter`, `ToolRouter`, `ResponseSummarizer`, or `ConversationService`
— the same "swap the adapter, not the port" guarantee `DecisionLedger`
already gives `RecordDecisionUseCase` (spec-005 §15). **This is a
consequence of keeping the port shape honest, not a stated Epic 8
objective**: no other channel is designed, built, or tested here (§5).

## 9. Message flow

```
Telegram Update (text or voice)
     │
     ▼
TelegramConversationAdapter                      <- interfaces/telegram/ (§21)
     │  voice: downloads the audio file via the Bot API first
     │  builds ConversationMessage(text | audio, user_identity,
     │                              conversation_identity, received_at)
     ▼
ConversationService.handle_message(message)      <- channel-agnostic (conversation/service.py)
     │
     ├─ 1. If message.audio is set: SpeechToTranscriber.transcribe() -> text
     │      (required collaborator, §18; TranscriptionError -> friendly reply, stop)
     │
     ├─ 2. AccessPolicy.is_authorized(user_identity) — reject and stop if not
     │
     ├─ 3. SessionStore.get(conversation_identity) -> ConversationSession
     │
     ├─ 4. CommandParser.parse(text) -> Intent | None                  (§12)
     │      — if not None (an explicit "/command"), use it directly and
     │        skip step 5 entirely: the LLM is never invoked for a command
     │
     ├─ 5. Else: IntentRouter.route(text, session) -> Intent            (§12)
     │      — deterministic (RuleBasedIntentRouter) or, if configured,
     │        LLM-backed with a deterministic fallback (FallbackIntentRouter)
     │
     ├─ 6. ToolRouter.tool_for(intent.type) -> Tool                    (§14)
     │
     ├─ 7. Tool.execute(intent, session) -> ToolOutcome                (§13, §14, §15)
     │      — the Tool's only collaborator is application/; asset-class
     │        eligibility (§13) is checked here, before any application/
     │        call, for ANALYZE_ASSET/COMPARE_ASSETS
     │
     ├─ 8. Update session's context slots from the outcome (§20)
     │
     ├─ 9. ResponseSummarizer.summarize(outcome, intent) -> str         (§17)
     │      — template-based, or LLM-rephrased-and-grounding-checked with
     │        a template fallback (FallbackResponseSummarizer)
     │
     ├─ 10. (optional) TextToSpeechSynthesizer.synthesize(text) -> AudioPayload
     │
     ├─ 11. SessionStore.save(session)
     │
     ▼
ConversationResponse(text, audio)
     │
     ▼
TelegramConversationAdapter.send(response, conversation_identity)   <- Bot API delivery
```

`ConversationService.handle_message` never raises (§22) — every step above
that can fail is caught internally and converted into a
`ConversationResponse` carrying a friendly explanation, so no
`ConversationAdapter` implementation ever needs its own error-handling
logic beyond delivering whatever `ConversationResponse` it receives.

## 10. Domain model

### `conversation/models.py`

```python
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
```

### Field notes

- `ConversationMessage`/`ConversationResponse`/`Intent`
  are frozen and self-validating, matching every other value object in this
  codebase (§6). `ConversationSession` is the sole exception (§20).
- **No `PendingConfirmation` type.** Revision 1 of this spec designed a
  generic confirmation-flow mechanism for a hypothetical future
  execution-capable `Tool`. It is removed here: Epic 8 has no such `Tool`,
  and the mechanism was premature architecture for a later, unscheduled
  broker-execution epic — see §19.
- `Intent.parameters` normalizes to a `MappingProxyType`, the `Mapping`
  analogue of every other sequence field's `tuple` normalization
  (`AgentReport.evidence`, `InvestmentDecision.agent_reports`, etc.,
  spec-002) — the caller's original `dict` cannot be mutated to affect an
  already-constructed `Intent`.
- No `confidence` field on `Intent`. A rule-based classifier's "confidence"
  would be a fabricated number with no calibration behind it — exactly the
  kind of false precision ADR-001 already rejected for the deterministic
  committee. `IntentRouter` either resolves a message to a specific
  `IntentType`, or it resolves to `IntentType.UNKNOWN`; there is no
  in-between score to report.

## 11. Access control and identity

```python
# conversation/access.py
from __future__ import annotations

from abc import ABC, abstractmethod

from .models import UserIdentity


class AccessPolicy(ABC):
    """Decides whether a UserIdentity may use the assistant at all."""

    @abstractmethod
    def is_authorized(self, user: UserIdentity) -> bool:
        """Never raises - an unrecognized or malformed identity is simply unauthorized."""
        raise NotImplementedError


class AllowlistAccessPolicy(AccessPolicy):
    """The only adapter this Epic ships: a fixed set of allowed user ids.

    Matches the project's stated scope (CLAUDE.md: "personal, local"
    system) - there is exactly one owner, not a multi-tenant user base.
    Where the allowed id(s) are read from (an environment variable, a local
    config file - never a committed secret) is a composition-root detail,
    deferred to implementation (S30), not decided by this spec.
    """

    def __init__(self, allowed_user_ids: frozenset[str]) -> None:
        self._allowed_user_ids = allowed_user_ids

    def is_authorized(self, user: UserIdentity) -> bool:
        return user.user_id in self._allowed_user_ids
```

`AccessPolicy.is_authorized` is checked **before** `SessionStore.get`,
`IntentRouter.route`, or any `Tool` call (§9, step 2) — an unauthorized
message triggers zero session mutation, zero Application Service calls, and
zero LLM calls (if one is ever wired in), mirroring
`ApplicationService.analyze_asset`'s existing fail-fast pattern for blank
tickers (spec-004: "no collaborator is called yet"). `ConversationService`
returns a fixed, generic decline message for an unauthorized user — it
never reveals *why* access was denied (no "you are not user 12345"), to
avoid leaking any information to an unrecognized sender.

## 12. Intent recognition — `CommandParser` and `IntentRouter`

Two independent, converging paths turn text into an `Intent` (§9, steps 4–5):
a deterministic `CommandParser` for explicit `/commands`, checked first and
exclusively, and an `IntentRouter` for everything else. **The LLM, when
configured, is only ever reachable through `IntentRouter` — `CommandParser`
never invokes it, under any circumstance.**

### `conversation/command_parser.py`

```python
from __future__ import annotations

from .models import Intent, IntentType

_COMMAND_INTENTS: dict[str, IntentType] = {
    "help": IntentType.HELP,
    "analyze": IntentType.ANALYZE_ASSET,
    "history": IntentType.DECISION_HISTORY,
    "explain": IntentType.EXPLAIN_DECISION,
    "compare": IntentType.COMPARE_ASSETS,
    "portfolio": IntentType.PORTFOLIO_SUMMARY,
    "performance": IntentType.PERFORMANCE_SUMMARY,
}


class CommandParser:
    """Deterministic parser for explicit Telegram bot commands (/help,
    /analyze AAPL, /history AAPL, ...). Standard library only - never
    invokes an LLM, never calls IntentRouter. Symbol resolution for a
    command's argument is literal (the argument is used as a ticker
    directly, uppercased) - SymbolResolver's name lookup (S13) is an
    IntentRouter-only convenience for free-form text, not extended to
    commands, since a command's argument is expected to already be a
    ticker."""

    def parse(self, text: str) -> Intent | None:
        """Returns None if `text` does not start with "/" - the caller
        (ConversationService, S9) falls through to IntentRouter only in
        that case. Text that *does* start with "/" but names an
        unrecognized command still returns an Intent (UNKNOWN) - it is
        never silently handed to IntentRouter or the LLM."""
        if not text.startswith("/"):
            return None
        parts = text[1:].split(maxsplit=1)
        command = parts[0].lower() if parts else ""
        argument = parts[1].strip() if len(parts) > 1 else ""
        intent_type = _COMMAND_INTENTS.get(command)
        if intent_type is None:
            return Intent(type=IntentType.UNKNOWN, parameters={}, raw_text=text)
        parameters = {"symbol": argument.upper()} if argument else {}
        return Intent(type=intent_type, parameters=parameters, raw_text=text)
```

`CompareAssetsTool` expects `parameters["symbols"]` (comma-joined, §15.4),
not `parameters["symbol"]` — a `/compare` command's argument (e.g.
`"MSFT AAPL"` or `"MSFT,AAPL"`) is normalized to the comma-joined form
`CommandParser` produces before constructing the `Intent`; this is a small,
literal string transform, not a second symbol-resolution mechanism.

### `conversation/intent_router.py`

```python
from __future__ import annotations

from abc import ABC, abstractmethod

from .models import ConversationSession, Intent


class IntentRouter(ABC):
    """Turns free-form text (never an explicit "/command" - CommandParser
    already handled that, S9) into a structured Intent. Never raises - an
    unrecognized message resolves to Intent(type=UNKNOWN, ...), not an
    exception. `session` is read-only context (e.g. session.last_asset for
    a follow-up like "explain that") - IntentRouter never mutates it."""

    @abstractmethod
    def route(self, text: str, session: ConversationSession) -> Intent:
        raise NotImplementedError
```

### Baseline: `RuleBasedIntentRouter` — deterministic, standard library only, always available

The shipped default, following §6's "deterministic first" precedent.
Standard-library `re` and plain string matching only, no network access, no
LLM. Three cooperating steps:

1. **Keyword/pattern matching per intent** — a small, fixed table of
   phrase patterns per `IntentType` (e.g. `"compare"` + two ticker-like
   tokens → `COMPARE_ASSETS`; `"why"`/`"explain"` → `EXPLAIN_DECISION`;
   `"history"`/`"recommended"`/`"last"` + a count → `DECISION_HISTORY`;
   `"help"`/`"what can you do"` → `HELP`; `"portfolio"` → `PORTFOLIO_SUMMARY`;
   `"performance"`/`"how did we do"` → `PERFORMANCE_SUMMARY`). Anything
   matching none of these, but containing a resolvable ticker/company name,
   defaults to `ANALYZE_ASSET` — this is deliberately the most permissive
   fallback, since "analyze this thing" is the single most common
   conversational shape ("Analyze Nvidia," "Should I buy Google?," "What
   does the committee think about TSLA?").
2. **Imperative-vs-interrogative disambiguation for trade language** — the
   one place this baseline needs a real, explicit rule rather than a
   keyword bag, because the Epic prompt gives two examples that must
   resolve *differently* despite both containing "buy":
   - **"Should I buy Nvidia?"** — hedged/interrogative phrasing (`"should
     I"`, `"would"`, `"do you think"`, a trailing `"?"`) → `ANALYZE_ASSET`.
   - **"Buy Bitcoin."** — a bare imperative opening with an execution verb
     (`"buy"`, `"sell"`, `"purchase"`, `"place an order"`) and no hedging
     language → `UNSUPPORTED_TRADE_REQUEST` (§15.8).
   This heuristic is accepted as imperfect (§29) — but the failure mode is
   safely asymmetric: misclassifying a real analysis question as a trade
   request produces an unhelpful decline; misclassifying a trade command as
   an analysis question is impossible by construction, since no intent in
   this spec executes anything (§5). There is no failure mode here that
   causes an unintended action.
3. **Symbol resolution** (§13) extracts `parameters["symbol"]` (or
   `parameters["symbols"]` for `CompareAssets`) from whatever ticker- or
   company-name-shaped tokens the matched pattern identified.

`RuleBasedIntentRouter` never raises; a message matching no pattern and
containing no resolvable symbol resolves to `Intent(type=UNKNOWN, ...)`,
which `ToolRouter` maps to `HelpTool` (§14) — an unrecognized message always
produces a helpful nudge, never silence or an error.

### `LLMIntentRouter` — optional, Epic 8 in-scope, structurally bounded

Unlike revision 1 of this spec, an LLM-backed `IntentRouter` **is** an Epic
8 adapter — the natural-language examples in the Epic prompt ("Should I buy
Nvidia?", "How many BUY decisions have we made this month?") are exactly
the free-form cases `RuleBasedIntentRouter`'s keyword matching handles only
approximately. It lives behind `integrations/llm/` (mirroring
`integrations/tradingagents/`'s existing isolation of an external
dependency), is injected into `conversation/` only via `IntentRouter`'s
existing ABC, and its provider-specific client type never appears outside
`integrations/llm/` and `conversation/factory.py` (§8).

```python
# conversation/intent_router.py (addition)

class LLMIntentRouter(IntentRouter):
    """LLM-backed interpretation of free-form text. Never reached for a
    "/command" (CommandParser runs first, S9) and never calls application/
    itself (S8) - it only ever produces an Intent.

    Structured output validation: the model's raw response is parsed
    against a fixed schema (an intent name and a flat string/string
    parameter map). The intent name is looked up against IntentType by
    value; anything that does not match an existing member - a
    hallucinated name, a malformed response, missing/extra structure -
    resolves to IntentType.UNKNOWN, never to a dynamically-constructed
    intent or tool. This is what makes "execute arbitrary tools" and
    "create unsupported tool names dynamically" structurally impossible,
    not just discouraged: ToolRouter (S14) only ever holds Tools for the
    fixed IntentType enum, and nothing upstream of it can hand it a type
    outside that enum.

    If the model determines the request is genuinely ambiguous (e.g. no
    resolvable asset, unclear which of two tickers is meant), it returns
    IntentType.UNKNOWN with parameters={"clarification": "<question>"} -
    TemplateResponseSummarizer's UNKNOWN branch (S17) surfaces this
    verbatim instead of the generic help text.

    Raises LLMInterpretationError (conversation/errors.py) if the provider
    call itself fails - timeout, network error, malformed response the
    schema check rejects outright, or the provider is not configured. Never
    falls back on its own; FallbackIntentRouter (below) owns that
    decision, so LLMIntentRouter's own contract stays simple: succeed with
    a valid Intent, or raise.
    """

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm_client = llm_client

    def route(self, text: str, session: ConversationSession) -> Intent:
        raise NotImplementedError  # implementation Epic
```

### `FallbackIntentRouter` — the deterministic-fallback requirement, made concrete

```python
class FallbackIntentRouter(IntentRouter):
    """Tries `primary` first; falls back to `fallback` if `primary` raises
    LLMInterpretationError. This is the deterministic-fallback-when-the-
    provider-is-disabled-or-unavailable mechanism: never raises itself,
    since `fallback` (RuleBasedIntentRouter) never raises. Composing this
    wrapper is the *only* way an LLM is ever used in production (S25) -
    IntentRouter alone, with no wrapper, is never wired to the raw
    LLMIntentRouter."""

    def __init__(self, primary: IntentRouter, fallback: IntentRouter) -> None:
        self._primary = primary
        self._fallback = fallback

    def route(self, text: str, session: ConversationSession) -> Intent:
        try:
            return self._primary.route(text, session)
        except LLMInterpretationError:
            return self._fallback.route(text, session)
```

`conversation/errors.py` gains `LLMInterpretationError(ConversationError)` —
one exception, shared by `LLMIntentRouter` and `LLMResponseSummarizer`
(§17), matching this codebase's convention of a small, named exception
vocabulary per boundary rather than one per failure mode.

**What the LLM is structurally prevented from doing**, cross-referenced
against the Epic prompt's list:

| Constraint | How this design enforces it |
|---|---|
| Must not invent market data | The LLM never receives, and `Intent` never carries, market data — `Intent` only carries an `IntentType` and string parameters (§10); all numbers come from `Tool.execute` → `application/` (§14, §15) |
| Must not produce the authoritative recommendation | `AnalyzeAssetTool`/`CompareAssetsTool` call `RecordDecisionUseCase.analyze_and_record`, which runs the deterministic committee (ADR-001) — the LLM never sees an `IndicatorSnapshot` and has no path to influence `InvestmentDecision.action` |
| Must not bypass Application Services | `IntentRouter` has zero `application/` dependency (§8); only `Tool.execute` calls `application/`, and every `Tool` is deterministic Python (§14, §15) |
| Must not access infrastructure adapters directly | `LLMIntentRouter`'s only dependency is `LLMClient` (`integrations/llm/`); it has no reference to `market_intelligence`, `memory`, or any other adapter |
| Must not write to the Decision Ledger | Only `RecordDecisionUseCase`/`DecisionQueryService` (both `application/`) touch `memory/`; `conversation/` cannot reach `memory/` directly (§8), and the LLM cannot reach `application/` at all |
| Must not execute arbitrary tools / create unsupported tool names dynamically | `Intent.type` is always a member of the closed `IntentType` enum (any non-matching model output resolves to `UNKNOWN`, never a new type); `ToolRouter.__init__` requires a `Tool` for every member and holds no others (§14) |

## 13. Symbol resolution

`"Analyze Apple"` names a company, not a ticker — nothing in this codebase
today maps `"Apple"` → `"AAPL"` (`Asset.name` is optional and unset by
`YFinanceProvider`, per spec-004). This is a real, small gap this spec must
close for the Epic prompt's own examples to work.

```python
# conversation/symbol_resolver.py
from __future__ import annotations

from abc import ABC, abstractmethod


class SymbolResolver(ABC):
    """Best-effort resolution of a free-form name to a ticker symbol."""

    @abstractmethod
    def resolve(self, text: str) -> str | None:
        """Return a ticker symbol, or None if `text` cannot be resolved.

        Never raises. A None result is not an error - the caller (an
        IntentRouter step) falls back to treating `text` itself as a
        literal ticker attempt, which then either works (the user already
        typed a real ticker) or fails downstream at MarketDataUnavailableError
        with a friendly translated response (S22) - never silently guessed.
        """
        raise NotImplementedError


class StaticSymbolResolver(SymbolResolver):
    """A small, hand-maintained name -> ticker table, restricted to
    supported asset classes (equities, ETFs, Forex - see "Asset-class
    eligibility" below): e.g. "apple" -> "AAPL", "nvidia" -> "NVDA",
    "tesla" -> "TSLA", "microsoft" -> "MSFT", "google"/"alphabet" ->
    "GOOGL", "euro dollar"/"eur usd" -> "EURUSD=X". Deliberately not
    exhaustive - this is a convenience for the common case, not a full
    company-name search service (which would need a real data source, out
    of scope). Case-insensitive, whitespace-trimmed lookup only; no fuzzy
    matching. Deliberately excludes crypto/commodity names (no "bitcoin" ->
    "BTC-USD" entry) - resolving a name only to have AssetClassPolicy
    reject the resulting ticker (below) would be a confusing, avoidable
    round trip; excluded-class names simply fail to resolve, and the
    assistant asks for clarification like any other unrecognized name."""

    def __init__(self, name_to_ticker: dict[str, str]) -> None:
        self._name_to_ticker = {k.lower(): v for k, v in name_to_ticker.items()}

    def resolve(self, text: str) -> str | None:
        return self._name_to_ticker.get(text.strip().lower())
```

If neither the static table nor "the token already looks like a ticker"
resolves a name, the assistant asks for clarification rather than guessing
(e.g. *"I don't recognize 'Foo Corp' — try its ticker symbol, like
AAPL."*) — consistent with ADR-001's "no false precision" rationale applied
to symbol matching, not just numeric confidence.

### Asset-class eligibility

Epic 8 supports only equities, ETFs, and Forex currency pairs, and must
reject crypto, commodities (including XAU/USD), options, and futures
**before reaching the Application layer** — before
`RecordDecisionUseCase.analyze_and_record` is ever called, since
`market_intelligence`/`yfinance` will happily fetch (and, if not stopped,
the ledger would happily record) tickers like `BTC-USD`, `GC=F`, or
`XAUUSD=X`. Nothing in the codebase performs this check today.

**Why not add `domain.AssetType.FOREX`, given Forex is now a first-class
supported asset class?** Considered and rejected, for two independent
reasons, not a style preference:

1. **The rejection has to happen before an `Asset` exists.** An `Asset`,
   and therefore any `AssetType`, is only constructed inside
   `YFinanceProvider.get_daily_history` — one layer past the point where
   this check must already have stopped an unsupported request (`domain/`
   and `market_intelligence/` are both below `application/`, which this
   check must never even reach). A domain enum member cannot gate a
   decision that has to be made from a raw ticker *string*, pre-fetch.
2. **`AssetType` is not populated reliably today.** Per spec-004,
   `YFinanceProvider` always constructs `Asset` with `asset_type=AssetType.STOCK`,
   regardless of whether the ticker is actually a stock or an ETF. Adding
   `FOREX` without also changing `YFinanceProvider` to classify correctly
   would add a third enum value nothing ever assigns — the same "value
   that looks authoritative but is arbitrary" false precision ADR-001
   already rejected once for confidence scores. Fixing that classification
   at the source is a `market_intelligence`/`domain` change, out of scope
   for a conversational-interface spec and for a pass that must not modify
   existing files (§5).

Instead, eligibility is decided from the **ticker string itself**, in
`conversation/`, using the same `yfinance` ticker conventions
`market_intelligence` already relies on (spec-004: "Symbols such as
`BRK.B`, `BTC-USD`, `^GSPC`, or `EURUSD=X` are all passed through
unchanged"):

```python
# conversation/asset_policy.py
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from enum import Enum


class AssetClassDecision(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED_CRYPTO = "UNSUPPORTED_CRYPTO"
    UNSUPPORTED_COMMODITY = "UNSUPPORTED_COMMODITY"
    UNSUPPORTED_DERIVATIVE = "UNSUPPORTED_DERIVATIVE"
    UNSUPPORTED_UNRECOGNIZED = "UNSUPPORTED_UNRECOGNIZED"


class AssetClassPolicy(ABC):
    @abstractmethod
    def classify(self, ticker: str) -> AssetClassDecision:
        """Never raises. Deny-by-default: any ticker shape not positively
        recognized as an equity/ETF or a genuine Forex pair is
        UNSUPPORTED_*, never SUPPORTED - the safer polarity for a hard
        product exclusion list (contrast with SymbolResolver, S13, whose
        failure mode is a clarification question, not a rejection)."""
        raise NotImplementedError


_METAL_CODES = frozenset({"XAU", "XAG", "XPT", "XPD"})
_CRYPTO_SUFFIXES = ("-USD", "-USDT", "-EUR", "-GBP", "-BTC", "-ETH")
_FX_PATTERN = re.compile(r"^([A-Z]{3})([A-Z]{3})=X$")
_EQUITY_OR_ETF_PATTERN = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


class TickerPatternAssetClassPolicy(AssetClassPolicy):
    """Deterministic, standard-library ticker-shape classifier - no
    dependency on domain.AssetType (see "Why not AssetType.FOREX" above)."""

    def classify(self, ticker: str) -> AssetClassDecision:
        symbol = ticker.strip().upper()
        if any(symbol.endswith(suffix) for suffix in _CRYPTO_SUFFIXES):
            return AssetClassDecision.UNSUPPORTED_CRYPTO
        if symbol.endswith("=F"):
            return AssetClassDecision.UNSUPPORTED_COMMODITY
        fx_match = _FX_PATTERN.match(symbol)
        if fx_match:
            base, quote = fx_match.group(1), fx_match.group(2)
            if base in _METAL_CODES or quote in _METAL_CODES:
                # XAUUSD=X, XAGUSD=X etc. - metal spot codes disguised as
                # an FX pair. XAU/XAG/XPT/XPD are ISO 4217 codes for
                # precious metals, not currencies - must not be classified
                # as Forex just because the ticker shape matches.
                return AssetClassDecision.UNSUPPORTED_COMMODITY
            return AssetClassDecision.SUPPORTED
        if _EQUITY_OR_ETF_PATTERN.match(symbol):
            return AssetClassDecision.SUPPORTED
        return AssetClassDecision.UNSUPPORTED_UNRECOGNIZED
```

- `XAUUSD=X` is caught explicitly: it matches the Forex ticker shape, but
  `XAU` is a recognized metal code, so it classifies as
  `UNSUPPORTED_COMMODITY`, not `SUPPORTED` — the one case the Epic prompt
  calls out by name.
- `EURUSD=X`, `GBPJPY=X` — genuine currency pairs — classify as `SUPPORTED`.
- `BTC-USD`, `ETH-USD` → `UNSUPPORTED_CRYPTO`. `GC=F` (gold futures) →
  `UNSUPPORTED_COMMODITY`. An options-style or otherwise unrecognized
  ticker shape → `UNSUPPORTED_UNRECOGNIZED`, not silently allowed through.
- **Called by `AnalyzeAssetTool`/`CompareAssetsTool` (§15.1, §15.4)
  before** `RecordDecisionUseCase.analyze_and_record` — never by
  `ExplainDecisionTool`/`DecisionHistoryTool`, which only read
  already-recorded history; if the gate holds at analyze-time, no
  unsupported-asset record can ever exist in the ledger to read back.
- This is a pragmatic, ticker-*shape* classifier, not a claim of a clean
  domain-modeled asset taxonomy — flagged in §30 as a place coverage may
  need to grow (e.g. more metal codes, more crypto quote currencies) as
  real usage surfaces gaps.

## 14. Tool routing — `Tool`, `ToolOutcome`, `ToolRouter`

### Naming note

"Tool" here means the same thing an LLM function-calling framework usually
means by it — a discrete, named capability the interpretation layer can
invoke — but nothing in this spec requires an actual LLM to exist for the
name to make sense: `ToolRouter` dispatches on `IntentType`, a plain enum,
whether or not `IntentRouter` (§12) is LLM-backed.

### `conversation/tools/base.py`

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Sequence

from investment_committee.memory import DecisionRecord

from ..models import ConversationSession, Intent


@dataclass(frozen=True)
class HelpContent:
    """What HelpTool returns - a static description of available intents."""

    available_intents: Sequence[str]
    description: str


@dataclass(frozen=True)
class NotYetAvailable:
    """What PortfolioSummaryTool/PerformanceSummaryTool return today -
    an honest "not built yet," never a fabricated number."""

    feature_name: str
    message: str


@dataclass(frozen=True)
class PolicyDeclined:
    """What UnsupportedTradeRequestTool (and any future permanently-out-of-
    scope request) returns - distinct from NotYetAvailable: this isn't
    coming later, it's a deliberate CLAUDE.md constraint."""

    reason: str
    message: str


ToolOutcome = (
    DecisionRecord
    | Sequence[DecisionRecord]
    | tuple[DecisionRecord, DecisionRecord]
    | HelpContent
    | NotYetAvailable
    | PolicyDeclined
)
"""Every Tool returns one of these - all pre-existing domain/memory types
plus three small new presentation types, never a generic Any payload. This
mirrors EvidenceItem.value's restricted Decimal | str union (spec-002):
a closed set, not an open one, so ResponseSummarizer (S17) can pattern-match
exhaustively with `match outcome:` (Python 3.11, CLAUDE.md)."""


class Tool(ABC):
    """One intent's adapter over the Application Layer. A Tool's only
    permitted collaborator is something from investment_committee.application
    (S8) - never memory/, committee/, indicators/, or market_intelligence/
    directly. No confirmation-gating mechanism exists on this ABC - Epic 8
    has no execution-capable Tool to gate (S19)."""

    @abstractmethod
    def execute(self, intent: Intent, session: ConversationSession) -> ToolOutcome:
        """Execute this tool for `intent`. May raise ApplicationServiceError
        subclasses (S22) - a Tool does not catch its own application/
        failures; ConversationService does, once, in one place."""
        raise NotImplementedError
```

### `conversation/tools/router.py`

```python
from __future__ import annotations

from typing import Mapping

from ..models import IntentType
from .base import Tool


class ToolRouter:
    """Deterministic IntentType -> Tool dispatch table. Not an ABC - unlike
    IntentRouter (S12), there is exactly one sane way to map a fixed enum to
    a fixed set of tools; no swappable strategy is needed here."""

    def __init__(self, tools_by_intent: Mapping[IntentType, Tool]) -> None:
        missing = set(IntentType) - set(tools_by_intent)
        if missing:
            raise ValueError(f"ToolRouter is missing a tool for: {sorted(missing)}")
        self._tools_by_intent = dict(tools_by_intent)

    def tool_for(self, intent_type: IntentType) -> Tool:
        return self._tools_by_intent[intent_type]
```

`ToolRouter.__init__` requires **every** `IntentType` member to have a
registered `Tool` — including `UNKNOWN` (routed to `HelpTool`, §15.7) and
`UNSUPPORTED_TRADE_REQUEST` (§15.8). This is a constructor-time invariant,
not a runtime `KeyError` risk: a `ToolRouter` that is missing a tool for
any intent fails to construct at all, the same way
`DeterministicCommitteeEngine` validates its analysts' roles at
construction (spec-003).

## 15. Per-intent tool design

| # | Intent | Tool | Application-layer call | Outcome |
|---|---|---|---|---|
| 1 | `ANALYZE_ASSET` | `AnalyzeAssetTool` | `AssetClassPolicy.classify` (§13) gate, then `RecordDecisionUseCase.analyze_and_record(symbol)` | `DecisionRecord` or `PolicyDeclined` |
| 2 | `EXPLAIN_DECISION` | `ExplainDecisionTool` | `DecisionQueryService.get`/`.list_by_asset` (§16) | `DecisionRecord` |
| 3 | `DECISION_HISTORY` | `DecisionHistoryTool` | `DecisionQueryService.list_by_asset`/`.list_recent` (§16) | `Sequence[DecisionRecord]` |
| 4 | `COMPARE_ASSETS` | `CompareAssetsTool` | `AssetClassPolicy.classify` (§13) gate on both symbols, then `RecordDecisionUseCase.analyze_and_record(symbol)` × 2 | `tuple[DecisionRecord, DecisionRecord]` or `PolicyDeclined` |
| 5 | `PORTFOLIO_SUMMARY` | `PortfolioSummaryTool` | none — `portfolio/` does not exist yet | `NotYetAvailable` |
| 6 | `PERFORMANCE_SUMMARY` | `PerformanceSummaryTool` | none — `evaluation/` does not exist yet | `NotYetAvailable` |
| 7 | `HELP` / `UNKNOWN` | `HelpTool` | none | `HelpContent` |
| 8 | `UNSUPPORTED_TRADE_REQUEST` | `UnsupportedTradeRequestTool` | none | `PolicyDeclined` |

`PolicyDeclined` (§14) now has two distinct producers: `UnsupportedTradeRequestTool`
(a permanently-out-of-scope *action*, §15.8) and `AnalyzeAssetTool`/
`CompareAssetsTool` (a permanently-out-of-scope *asset class*, §13). No new
`ToolOutcome` member was needed — both are "this request is declined, not
merely unavailable yet," which is exactly `PolicyDeclined`'s existing
meaning.

### 15.1 `AnalyzeAssetTool`

```python
class AnalyzeAssetTool(Tool):
    def __init__(
        self,
        record_decision_use_case: RecordDecisionUseCase,
        asset_class_policy: AssetClassPolicy,
    ) -> None:
        self._record_decision_use_case = record_decision_use_case
        self._asset_class_policy = asset_class_policy

    def execute(self, intent: Intent, session: ConversationSession) -> DecisionRecord | PolicyDeclined:
        symbol = intent.parameters["symbol"]
        decision = self._asset_class_policy.classify(symbol)
        if decision is not AssetClassDecision.SUPPORTED:
            return PolicyDeclined(
                reason=decision.value.lower(),
                message=f"{symbol} isn't a supported asset class - this assistant "
                "covers equities, ETFs, and Forex currency pairs only "
                "(no crypto, commodities, options, or futures).",
            )
        result = self._record_decision_use_case.analyze_and_record(symbol)
        return result.record
```

The eligibility check runs **before** `analyze_and_record` — an
unsupported symbol never reaches `RecordDecisionUseCase`, `application/`,
or the Decision Ledger (§13).

**Design decision: conversational `AnalyzeAsset` always records.** Unlike
the dev CLI (spec-004), which calls `analyze_asset` without recording
(it's a debugging tool, not a user-facing recommendation), a conversational
answer to "Should I buy Nvidia?" *is* a recommendation shown to a human —
`CLAUDE.md`'s auditability goal is best served by every such answer being
ledger-recorded by default, with no opt-out in this Epic. Flagged in §29 as
a judgment call, since an argument exists for an unrecorded "just curious"
mode.

### 15.2 `ExplainDecisionTool`

Resolution order for "which decision":

1. `intent.parameters["record_id"]` if the router extracted one explicitly
   (uncommon in natural language, but supported for a future "explain
   record ...");
2. else `intent.parameters["symbol"]` if given — look up
   `DecisionQueryService.list_by_asset(symbol)` and take the most recently
   `recorded_at` entry;
3. else `session.last_record_id` (e.g. *"Why was that a BUY?"* right after
   an `AnalyzeAsset` reply) — this is the concrete case
   `ConversationSession`'s limited context slots exist for (§20);
4. else no target is resolvable — return a clarification request (via
   `HelpContent`, reusing the same "ask, don't guess" pattern as §13)
   rather than raising or fabricating.

If step 2 finds no records for the asset, the tool returns a `NotYetAvailable`-shaped
message suggesting the user analyze the asset first — not an error, since
"no history yet" is an entirely ordinary state for a newly-analyzed ticker.

### 15.3 `DecisionHistoryTool`

`intent.parameters` may carry `symbol` ("What have we recommended for
Tesla?"), `limit` ("Show my last five analyses"), or neither ("How many BUY
decisions have we made this month?" — routed here with no `symbol`, an
implicit `since`/date-range filter applied client-side over
`DecisionQueryService.list_recent`, per §16's note that the ledger port
itself has no date-range query). Aggregation ("how many were BUY") is
computed by `ResponseSummarizer` (§17) from the returned
`Sequence[DecisionRecord]`, not by the `Tool` or the ledger — the `Tool`'s
job stays "fetch the records," never "compute a derived statistic," keeping
that logic in exactly one place per §17.

### 15.4 `CompareAssetsTool`

```python
class CompareAssetsTool(Tool):
    def __init__(
        self,
        record_decision_use_case: RecordDecisionUseCase,
        asset_class_policy: AssetClassPolicy,
    ) -> None:
        self._record_decision_use_case = record_decision_use_case
        self._asset_class_policy = asset_class_policy

    def execute(
        self, intent: Intent, session: ConversationSession
    ) -> tuple[DecisionRecord, DecisionRecord] | PolicyDeclined:
        symbol_a, symbol_b = intent.parameters["symbols"].split(",")
        for symbol in (symbol_a, symbol_b):
            decision = self._asset_class_policy.classify(symbol)
            if decision is not AssetClassDecision.SUPPORTED:
                return PolicyDeclined(
                    reason=decision.value.lower(),
                    message=f"{symbol} isn't a supported asset class - this assistant "
                    "covers equities, ETFs, and Forex currency pairs only "
                    "(no crypto, commodities, options, or futures).",
                )
        result_a = self._record_decision_use_case.analyze_and_record(symbol_a)
        result_b = self._record_decision_use_case.analyze_and_record(symbol_b)
        return (result_a.record, result_b.record)
```

Both symbols are checked **before either** `analyze_and_record` call — if
either is unsupported, neither asset is analyzed or recorded (no partial
comparison against one legitimate and one rejected asset). Two independent
`analyze_and_record` calls, two independent `DecisionRecord`s — no new
comparison computation (§5, §7). If the two tickers cannot both be
resolved, `Intent` construction/parameter extraction itself already failed
upstream (§12), so `CompareAssetsTool.execute` can assume exactly two
resolved symbols are present.

### 15.5 / 15.6 `PortfolioSummaryTool` / `PerformanceSummaryTool`

```python
class PortfolioSummaryTool(Tool):
    def execute(self, intent: Intent, session: ConversationSession) -> NotYetAvailable:
        return NotYetAvailable(
            feature_name="Portfolio",
            message="Paper portfolio tracking isn't built yet - it's planned for a later stage of the project.",
        )
```

`PerformanceSummaryTool` is identical in shape, `feature_name="Performance
evaluation"`. Both intents, and both tools, exist so the conversational
surface (and its intent vocabulary) is complete and forward-compatible now
— per the Epic prompt's explicit instruction — while returning an honest
placeholder instead of inventing portfolio or performance numbers that do
not exist. When `portfolio/`/`simulation/` (Epic 8) and `evaluation/`
(Epic 9) are eventually implemented, these two tools gain a real
`application/`-layer collaborator each; `ToolRouter`'s wiring and every
other tool are unaffected.

### 15.7 `HelpTool`

```python
class HelpTool(Tool):
    def execute(self, intent: Intent, session: ConversationSession) -> HelpContent:
        return HelpContent(
            available_intents=(
                "Analyze an asset - \"Analyze Apple\", \"Should I buy Nvidia?\"",
                "Explain a decision - \"Why was Apple a BUY?\"",
                "Decision history - \"What have we recommended for Tesla?\"",
                "Compare assets - \"Compare Microsoft and Apple\"",
                "Portfolio summary (coming soon)",
                "Performance summary (coming soon)",
            ),
            description="I'm the Investment Committee assistant. I analyze stocks and ETFs "
            "using a deterministic committee of analysts, explain past decisions, and "
            "show recommendation history. I never place trades.",
        )
```

`HelpTool` is registered for **both** `IntentType.HELP` and
`IntentType.UNKNOWN` in `ToolRouter`'s table (§14) — an unrecognized
message and an explicit "help" request produce the same graceful response.
`HelpTool` calls no `application/` collaborator at all; it is the one tool
whose `__init__` takes no arguments.

### 15.8 `UnsupportedTradeRequestTool`

```python
class UnsupportedTradeRequestTool(Tool):
    def execute(self, intent: Intent, session: ConversationSession) -> PolicyDeclined:
        return PolicyDeclined(
            reason="no_broker_integration",
            message="I don't execute trades - this system only analyzes and records "
            "recommendations, never real-money or broker actions. "
            f"I can analyze {intent.parameters.get('symbol', 'that')} for you instead, "
            "if you'd like.",
        )
```

This is the concrete, present-day answer to the Epic prompt's "Buy Bitcoin"
example: the request is recognized, never silently dropped, and declined
with a message that (a) states the real constraint (`CLAUDE.md`, no broker
integration, no real-money execution — "ever, unless the user explicitly
changes this constraint") and (b) offers the one thing the assistant can
actually do instead. There is no confirmation step here — the request is
refused outright, not paused pending a "yes" (§19 explains why no
confirmation mechanism exists in this Epic at all).

## 16. Application Layer addition: `DecisionQueryService`

`conversation/`'s "never bypass the Application Layer" rule (§8) means
`ExplainDecisionTool`/`DecisionHistoryTool` cannot call
`memory.DecisionLedger` directly — but no existing `application/`
component exposes a read path over the ledger today (`RecordDecisionUseCase`
only writes, per spec-005 §17). This spec adds the one missing read facade.

### `application/decision_query.py` (new file)

```python
from __future__ import annotations

from typing import Sequence

from investment_committee.domain import Asset, AssetType
from investment_committee.memory import (
    DecisionLedger,
    DecisionNotFoundError,
    DecisionRecord,
    DecisionRecordId,
    LedgerReadError,
)

from .errors import ApplicationServiceError


class DecisionNotFoundInApplicationError(ApplicationServiceError):
    """Wraps memory.DecisionNotFoundError from DecisionLedger.get."""


class DecisionQueryFailedError(ApplicationServiceError):
    """Wraps memory.LedgerReadError from any DecisionLedger read operation."""


class DecisionQueryService:
    """Read-only facade over the Decision Ledger, for callers (conversation/)
    that must not depend on memory/ directly (S8)."""

    def __init__(self, ledger: DecisionLedger) -> None:
        self._ledger = ledger

    def get(self, record_id: DecisionRecordId) -> DecisionRecord:
        try:
            return self._ledger.get(record_id)
        except DecisionNotFoundError as exc:
            raise DecisionNotFoundInApplicationError(str(exc)) from exc
        except LedgerReadError as exc:
            raise DecisionQueryFailedError(str(exc)) from exc

    def list_by_asset(self, symbol: str) -> Sequence[DecisionRecord]:
        ticker = symbol.strip().upper()
        if not ticker:
            raise DecisionQueryFailedError("symbol must not be blank")
        asset = Asset(ticker=ticker, name=None, asset_type=AssetType.STOCK, exchange=None, currency=None)
        try:
            return self._ledger.find_by_asset(asset)
        except LedgerReadError as exc:
            raise DecisionQueryFailedError(str(exc)) from exc

    def list_recent(self, limit: int) -> Sequence[DecisionRecord]:
        try:
            records = self._ledger.list_all()
        except LedgerReadError as exc:
            raise DecisionQueryFailedError(str(exc)) from exc
        return tuple(sorted(records, key=lambda r: r.recorded_at, reverse=True))[:limit]
```

- **`list_by_asset` normalizes the ticker itself** (`strip().upper()`) so
  it can construct a matching `Asset` for `DecisionLedger.find_by_asset`
  (spec-005 §14's contract, matched by `Asset` equality — `AssetType.STOCK`
  is the same default `YFinanceProvider` already assumes when no richer
  metadata exists, spec-004). This is now a **fifth** place ticker
  normalization logic exists (`Asset.__post_init__`,
  `YFinanceProvider.get_daily_history`, `ApplicationService.analyze_asset`,
  and — new in this Epic — here and in `conversation/`'s own symbol
  extraction). Spec-004's Unresolved Question 5 already flagged this
  duplication as a candidate future refactor; this spec adds to the count
  rather than resolving it, consistent with "don't touch existing files"
  (§5) and because `Asset.__post_init__` already re-normalizes regardless,
  making every one of these call sites correct even if not deduplicated.
- **`list_recent` reads the entire ledger and sorts client-side.** Fine at
  this project's personal, local scale — mirrors spec-005 §14's own
  reasoning for why `list_all()` has no pagination yet. `limit` is applied
  after sorting, not before, so "most recent" is always correct regardless
  of append order.
- **No `list_all()` passthrough is added.** `DecisionQueryService.list_recent`
  is what "how many BUY decisions this month" and "show my last five
  analyses" both actually need; a bare unbounded `list_all()` re-export
  would be the same kind of speculative surface spec-005 §14 already
  rejected for `exists()` — add it only if a concrete caller needs it.

### `application/factory.py` (proposed addition, not applied — §5)

```python
from investment_committee.memory import DecisionLedger, JsonLinesDecisionLedger

from .decision_query import DecisionQueryService


def build_default_decision_query_service(
    ledger: DecisionLedger | None = None,
) -> DecisionQueryService:
    """Assemble the production DecisionQueryService.

    Reuses the same default JsonLinesDecisionLedger()/DEFAULT_LEDGER_PATH
    build_default_record_decision_use_case() already uses, so a query
    service and a recording use case constructed with no arguments read
    and write the same ledger file - not two accidentally-different ones.
    """
    return DecisionQueryService(ledger=ledger if ledger is not None else JsonLinesDecisionLedger())
```

`DecisionQueryService`, `DecisionNotFoundInApplicationError`, and
`DecisionQueryFailedError` are proposed additions to `application/__init__.py`'s
`__all__`, alongside `build_default_decision_query_service` — the same
"proposed, not applied" treatment spec-005 gave its own `application/`
additions.

## 17. Response summarization

### `conversation/summarizer.py`

```python
from __future__ import annotations

from abc import ABC, abstractmethod

from .models import Intent
from .tools.base import ToolOutcome


class ResponseSummarizer(ABC):
    @abstractmethod
    def summarize(self, outcome: ToolOutcome, intent: Intent) -> str:
        """Turn an already-computed ToolOutcome into human-readable text.

        Never computes a new number, never fetches anything, never
        introduces a claim not already present on `outcome` - see "Tool
        Routing" constraint (Epic prompt) and the LLM-backed variant's
        contract below.
        """
        raise NotImplementedError
```

### Baseline: `TemplateResponseSummarizer`

Deterministic, `match`-statement dispatch on `outcome`'s type (Python
3.11's structural pattern matching), one branch per `ToolOutcome` member:

- `DecisionRecord` → `f"{ticker}: {action} (confidence {confidence})\n{summary}"`
  — literally `cli.py`'s existing `_format_result` (spec-004), reused for
  `AnalyzeAsset`; for `ExplainDecision`, additionally walks
  `decision.agent_reports[*].evidence` to list which indicator values and
  rules drove the outcome (this is exactly what §6 said `DecisionRecord`'s
  full embedding already makes possible with no new data).
- `Sequence[DecisionRecord]` → one line per record (ticker, action, date),
  and, when `intent.type == DECISION_HISTORY` and the raw text implied a
  count question ("how many BUY..."), a computed tally over the returned
  records — this is where that aggregation happens (§15.3).
- `tuple[DecisionRecord, DecisionRecord]` → two `DecisionRecord` blocks
  printed side by side, explicitly *not* a synthesized "X is better than Y"
  line (§5, §7).
- `HelpContent` → if `intent.type == UNKNOWN` and
  `intent.parameters.get("clarification")` is set (an `LLMIntentRouter`
  asking a follow-up question, §12), that clarification text is surfaced
  verbatim instead of the generic capability list; otherwise `description`
  followed by the bulleted `available_intents`.
- `NotYetAvailable` → `f"{feature_name} isn't available yet. {message}"`.
- `PolicyDeclined` → `message`, unchanged (§13, §15.8 already wrote the
  full user-facing text — for an asset-class decline as much as a trade
  decline).

**Forward seam to `education/`, not built now.** `education/`'s stated
purpose (`docs/architecture.md`, layer "Supporting packages") is exactly
"turn a decision and its supporting reports into a beginner-friendly
explanation" — `TemplateResponseSummarizer`'s `DecisionRecord` branch is
the natural place to delegate to it once it exists. Not performed here
(§5) — `education/` is still an empty placeholder — but flagged so a
future implementer does not have to rediscover the seam (§30).

### `LLMResponseSummarizer` — optional, Epic 8 in-scope, grounding-checked

An LLM-backed summarizer **is** an Epic 8 adapter, for the same reason
`LLMIntentRouter` is (§12) — but its contract is deliberately stricter, and
that stricter contract is enforced in code, not only documented:

```python
# conversation/summarizer.py (addition)

class LLMResponseSummarizer(ResponseSummarizer):
    """Rephrases TemplateResponseSummarizer's deterministic output in a
    friendlier tone. Never given raw ToolOutcome fields directly - only the
    already-rendered, already-correct template text - so it has nothing to
    compute from and nothing ungrounded to draw on. This is the literal
    implementation of the Epic prompt's Tool Routing requirement: "The LLM
    should never generate financial answers directly... summarize the
    returned structured data."

    Structured output validation (the "grounding check"): every distinct
    numeric token in the LLM's rephrased text must already appear in the
    template text it was given as input. If the rephrased text introduces
    any number the template did not contain, the check fails and
    LLMInterpretationError (S12) is raised - never silently accepted.
    """

    def __init__(self, llm_client: LLMClient, template: TemplateResponseSummarizer) -> None:
        self._llm_client = llm_client
        self._template = template

    def summarize(self, outcome: ToolOutcome, intent: Intent) -> str:
        raise NotImplementedError  # implementation Epic


class FallbackResponseSummarizer(ResponseSummarizer):
    """Same fallback shape as FallbackIntentRouter (S12): tries `primary`
    (LLMResponseSummarizer), falls back to `fallback`
    (TemplateResponseSummarizer) on LLMInterpretationError - including a
    failed grounding check, a provider timeout, or the provider being
    disabled. Composing this wrapper is the only way an LLM summarizer is
    ever used in production (S25)."""

    def __init__(self, primary: ResponseSummarizer, fallback: ResponseSummarizer) -> None:
        self._primary = primary
        self._fallback = fallback

    def summarize(self, outcome: ToolOutcome, intent: Intent) -> str:
        try:
            return self._primary.summarize(outcome, intent)
        except LLMInterpretationError:
            return self._fallback.summarize(outcome, intent)
```

The grounding check is deliberately simple (numeric-token containment, not
full semantic verification) — it catches the concrete, most damaging
failure mode (a fabricated number in a financial response) cheaply, at the
cost of not catching every possible ungrounded claim a model could phrase
without using a new digit. A stronger check is flagged as a future
improvement, not solved here (§30), consistent with spec-005 §28's
precedent of flagging a real but only-partially-solved verification
question rather than asserting a guarantee this spec cannot fully back up.

## 18. Voice support

```python
# conversation/speech.py
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
```

- **Both ports live in `conversation/`. A concrete Speech-to-Text adapter
  is required for Epic 8's acceptance — voice is not a future extension
  (§4).** `ConversationService.__init__` takes `transcriber:
  SpeechToTranscriber` as a **required** constructor parameter (no `None`
  default) — the type signature itself encodes "a working service must
  have a transcriber," rather than leaving voice silently unsupported by
  default. `TextToSpeechSynthesizer` remains optional
  (`synthesizer: TextToSpeechSynthesizer | None = None`, §9's pipeline) —
  Text-to-Speech output is explicitly not required.
- **The full Epic 8 voice path**: a Telegram voice message arrives as an
  Opus/OGG file; `TelegramConversationAdapter` downloads it via the Bot
  API's file-download endpoint and wraps the raw bytes in an
  `AudioPayload(data=..., mime_type="audio/ogg", duration_seconds=...)`
  before constructing `ConversationMessage` (§21) — from that point on,
  `ConversationService.handle_message` (§9) processes it through **exactly
  the same pipeline** typed text uses: `SpeechToTranscriber.transcribe`
  produces a plain `str`, which then flows through `CommandParser`/
  `IntentRouter` → `ToolRouter` → `Tool` → `ResponseSummarizer` identically
  to a typed message. No branch in that pipeline, past transcription,
  knows or cares whether the original message was spoken or typed.
- If the configured `transcriber` raises `TranscriptionError` (the
  provider is unreachable, rejects the audio, or times out),
  `ConversationService` catches it and replies with a friendly "couldn't
  understand that voice message — try typing your question instead"
  message (§22) — this is the *unavailable* case; it is no longer possible
  to construct a `ConversationService` with no transcriber at all.
- **The concrete Speech-to-Text adapter itself lives behind
  `integrations/speech/`** (§8), matching `CLAUDE.md`'s "external
  providers... sit behind an interface/adapter" rule exactly the way
  `YFinanceProvider` isolates `yfinance`. Which specific provider/library
  Epic 8 ships is flagged as an open decision (§30), not resolved by this
  spec.
- Neither port is Telegram-specific in any way — this is the literal
  fulfillment of "Do NOT couple speech recognition to Telegram":
  `AudioPayload` and both ports are defined in `conversation/`, reachable
  by any `ConversationAdapter`, not just `TelegramConversationAdapter`.
- `TextToSpeechSynthesizer` is invoked only when the inbound message itself
  arrived as voice (`ConversationResponse.audio` mirrors
  `ConversationMessage.audio`'s presence) — a text question never
  provokes an unsolicited voice reply. If no `synthesizer` is configured,
  or `SynthesisError` is raised, the response is delivered as text only
  (§22) — this remains fully optional, per the frozen scope.

## 19. Confirmation flows — removed from this Epic's scope

Revision 1 of this spec designed a generic `Tool.requires_confirmation` /
`PendingConfirmation` mechanism, on the reasoning that a future
execution-capable action ("Buy Bitcoin") should eventually require
confirmation. On review, this is premature design for Epic 8: no `Tool`
defined here executes anything (`AnalyzeAssetTool`/`CompareAssetsTool`'s
ledger writes are append-only and unconditional, mirroring why
`RecordDecisionUseCase.record` itself needs no confirmation step,
spec-005; `UnsupportedTradeRequestTool`, §15.8, refuses outright rather
than pausing for a "yes," since there is no execution path to confirm into
at all), and it is not this Epic's responsibility to design the
confirmation UX for a broker-execution epic that is not scheduled
(`CLAUDE.md`'s hard constraint: no broker integration, no real-money
execution, ever, unless the user explicitly changes this constraint).

**Consequence for this spec:** `Tool` has no `requires_confirmation`
attribute (§14), `Intent`/`ConversationSession` have no
`PendingConfirmation`-related field (§10), `conversation/confirmation.py`
does not exist, and §9's message flow has no confirmation step. When a
future epic introduces an execution-capable `Tool`, designing how it is
confirmed is that epic's own architecture work, informed by — but not
pre-committed by — this document.

## 20. Conversation session and context management

**Deliberately small.** The Epic prompt is explicit: rely on the Decision
Ledger and Application Services, not long conversational memory.
`ConversationSession` (§10) carries exactly two pieces of state beyond its
own identity:

| Field | Purpose | Set by |
|---|---|---|
| `last_asset` | Resolves an elliptical follow-up ("what about its risk?") to the most recently discussed `Asset` — **not used by any Tool in this spec** (no tool currently needs it), included now because `IntentRouter`'s symbol-resolution step (§13) is the natural, safe place a future intent would read it from | `ConversationService`, after any outcome that carries a `DecisionRecord` |
| `last_record_id` | Resolves "Why was that a BUY?" / "Explain yesterday's recommendation" to a specific record (§15.2) | Same as above |

This is **not** a transcript, not a rolling window of prior messages, and
not a place raw LLM context accumulates — an `LLMIntentRouter`/
`LLMResponseSummarizer` call (§12, §17) never receives prior messages, only
the current one plus these two narrow slots. A restarted process or an
expired `SessionStore` entry simply forgets `last_asset`/`last_record_id` —
acceptable, since the durable record of "what was decided" is always the
Decision Ledger, reachable by `symbol` or `record_id` regardless of session
state (§16).

### `conversation/session_store.py`

```python
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
    deferred: this state is meant to be disposable."""

    def __init__(self) -> None:
        self._sessions: dict[ConversationIdentity, ConversationSession] = {}

    def get(self, conversation_identity: ConversationIdentity) -> ConversationSession:
        return self._sessions.get(
            conversation_identity, ConversationSession(conversation_identity=conversation_identity)
        )

    def save(self, session: ConversationSession) -> None:
        self._sessions[session.conversation_identity] = session
```

## 21. Channel adapters — `ConversationAdapter`

### `conversation/adapters/base.py`

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from ..models import ChannelType, ConversationIdentity, ConversationResponse
from ..service import ConversationService


class ConversationAdapter(ABC):
    """One channel's boundary. Owns no business logic - every inbound event
    becomes exactly one ConversationService.handle_message() call, and every
    outbound reply is exactly what that call returned, unmodified."""

    channel: ClassVar[ChannelType]

    def __init__(self, conversation_service: ConversationService) -> None:
        self._conversation_service = conversation_service

    @abstractmethod
    def send(self, response: ConversationResponse, conversation_identity: ConversationIdentity) -> None:
        """Deliver `response` back to the channel, in whatever native shape
        the channel needs (text message, voice note, etc.)."""
        raise NotImplementedError
```

- `ConversationAdapter.__init__` takes a `ConversationService` by
  constructor injection — the same dependency-injection shape
  `ApplicationService` and `RecordDecisionUseCase` already use (spec-004,
  spec-005) — never a global/singleton lookup.
- **Sync core, adapter owns any async boundary.** `ConversationService.handle_message`
  is a plain synchronous method, matching every other orchestration method
  in this codebase (`analyze_asset`, `record`, `analyze_and_record` — none
  are `async`). `python-telegram-bot` (Epic 8's own new dependency) is
  async-first; a concrete `TelegramConversationAdapter`'s own event-handling
  method is `async` and calls the synchronous `handle_message` from within
  its event loop. `conversation/`'s core gains no async machinery of its
  own — this keeps the "plain functions, no async framework" convention
  intact one layer further than it would otherwise reach.
- No `start()`/`run()`/`listen()` method is defined on the ABC itself —
  how a channel receives events (webhook, long polling, a WebSocket) is
  channel-specific enough that forcing one shared method signature onto it
  now would be speculative; each concrete adapter defines its own entry
  point. Only `send()` is shared, because every channel needs *some* way to
  deliver a `ConversationResponse`, and that is the one place `interfaces/`
  code calls back into a shape `conversation/` defines.

### `interfaces/telegram/adapter.py` — this Epic's real deliverable

`TelegramConversationAdapter` is not illustrative — it is the concrete
adapter Epic 8 implements, tests (against a fake Bot API client, never a
real Telegram server, §26), and ships:

```python
class TelegramConversationAdapter(ConversationAdapter):
    channel = ChannelType.TELEGRAM

    def __init__(self, conversation_service: ConversationService, bot_token: str) -> None:
        super().__init__(conversation_service)
        ...  # python-telegram-bot Application wiring

    async def _on_update(self, update, context) -> None:
        message = await self._to_conversation_message(update)  # Telegram Update -> ConversationMessage
        response = self._conversation_service.handle_message(message)
        self.send(response, self._conversation_identity_for(update))

    async def _to_conversation_message(self, update) -> ConversationMessage:
        if update.message.voice is not None:
            telegram_file = await update.message.voice.get_file()  # Bot API file download
            audio = AudioPayload(
                data=await telegram_file.download_as_bytearray(),
                mime_type="audio/ogg",
                duration_seconds=Decimal(update.message.voice.duration),
            )
            return ConversationMessage(..., text=None, audio=audio, ...)
        return ConversationMessage(..., text=update.message.text, audio=None, ...)

    def send(self, response: ConversationResponse, conversation_identity: ConversationIdentity) -> None:
        ...  # bot.send_message, and bot.send_voice if response.audio is set
```

This confirms the one property that matters architecturally: everything
Telegram-specific (`python-telegram-bot`, `Update`, `bot_token`, the voice
file download) is confined to `interfaces/telegram/`, which depends on
`conversation/` and nothing lower (§8). No other channel adapter is built
in this Epic (§5, §7) — the shape above would generalize to one if a
future epic needed it, but that is not a claim this Epic makes.

## 22. Error model

`ConversationService.handle_message` **never raises** — this is a
deliberate, narrow departure from every other layer boundary in this
codebase, justified because it is the final one: there is no further
caller to hand a typed exception to, only a human on the other end of a
chat channel who must always get *some* reply, the same reason `cli.py`'s
`main()` catches `ApplicationServiceError` and prints a friendly message
instead of letting a traceback reach the terminal (spec-004) — just
extended one layer further, since a channel has no traceback to show at
all.

| Failure | Origin | Handling |
|---|---|---|
| `TranscriptionError` | `SpeechToTranscriber.transcribe` | Caught; friendly "couldn't understand that voice message" response |
| Unauthorized user | `AccessPolicy.is_authorized` returns `False` | Fixed decline response; no further processing (§11) |
| `ApplicationServiceError` (any subclass, including `DecisionRecordingError`, `DecisionNotFoundInApplicationError`, `DecisionQueryFailedError`) | A `Tool.execute` call into `application/` | Caught once, in `ConversationService`, and translated to a friendly message (e.g. `MarketDataUnavailableError` → *"I couldn't get data for that symbol — check the ticker and try again."*); the original exception is logged, not shown to the user |
| `SynthesisError` | `TextToSpeechSynthesizer.synthesize` | Caught; response is delivered as text only, with no audio, rather than failing the whole reply |
| Any other exception | Anywhere in steps 4–11 of §9 | Caught by a single outer `except Exception` in `handle_message`, logged, and answered with a generic *"Something went wrong — please try again."* This is the **one place in this codebase** permitted to catch bare `Exception` (`application/service.py` explicitly does **not**, spec-004) — justified here, and only here, because this is a UI/channel boundary with a human on the other end, not a library boundary another piece of code depends on for typed failure signaling. |

`LLMInterpretationError` (§12, §17) is deliberately **not** a row in this
table: it is raised by `LLMIntentRouter`/`LLMResponseSummarizer` and caught
one level down, inside `FallbackIntentRouter`/`FallbackResponseSummarizer`
themselves — `ConversationService` never sees it, because by the time
`IntentRouter.route`/`ResponseSummarizer.summarize` return control to
`ConversationService`, the fallback has already resolved the failure
transparently.

`IntentRouter.route`, `CommandParser.parse`, and `Tool.execute`'s own
internal collaborators (e.g. `SymbolResolver.resolve`,
`AssetClassPolicy.classify`) are documented as never raising in their own
right (§12, §13) — the exceptions above are exhaustively the ones
`ConversationService` must anticipate from its direct collaborators.

## 23. Package/module structure

```
src/investment_committee/conversation/
    __init__.py                  # public exports (S24)
    _validation.py                 # duplicated require_timezone_aware (D4 precedent)
    models.py                        # ChannelType, UserIdentity, ConversationIdentity,
                                      # AudioPayload, ConversationMessage, ConversationResponse,
                                      # IntentType, Intent, ConversationSession
    errors.py                          # ConversationError, TranscriptionError, SynthesisError,
                                        # SessionStoreError, LLMInterpretationError
    access.py                            # AccessPolicy, AllowlistAccessPolicy
    command_parser.py                      # CommandParser
    intent_router.py                         # IntentRouter, RuleBasedIntentRouter,
                                              # LLMIntentRouter, FallbackIntentRouter
    symbol_resolver.py                         # SymbolResolver, StaticSymbolResolver
    asset_policy.py                              # AssetClassDecision, AssetClassPolicy,
                                                  # TickerPatternAssetClassPolicy
    session_store.py                               # SessionStore, InMemorySessionStore
    speech.py                                        # SpeechToTranscriber, TextToSpeechSynthesizer
    summarizer.py                                      # ResponseSummarizer, TemplateResponseSummarizer,
                                                        # LLMResponseSummarizer, FallbackResponseSummarizer
    service.py                                           # ConversationService
    factory.py                                             # build_default_conversation_service()
    tools/
        __init__.py                                          # re-exports (S24)
        base.py                                                # Tool, ToolOutcome, HelpContent,
                                                                 # NotYetAvailable, PolicyDeclined
        router.py                                               # ToolRouter
        analyze_asset.py                                          # AnalyzeAssetTool
        explain_decision.py                                         # ExplainDecisionTool
        decision_history.py                                           # DecisionHistoryTool
        compare_assets.py                                               # CompareAssetsTool
        portfolio_summary.py                                              # PortfolioSummaryTool
        performance_summary.py                                             # PerformanceSummaryTool
        help.py                                                             # HelpTool
        unsupported_trade.py                                                  # UnsupportedTradeRequestTool
    adapters/
        __init__.py                                                            # re-exports ConversationAdapter
        base.py                                                                  # ConversationAdapter

src/investment_committee/application/
    decision_query.py            # NEW - DecisionQueryService (S16)
    factory.py                     # EXISTING - gains build_default_decision_query_service() (S16)

src/investment_committee/interfaces/
    telegram/
        __init__.py
        adapter.py                # TelegramConversationAdapter - this Epic's real deliverable (S21)

src/investment_committee/integrations/
    llm/                          # NEW - LLMClient port + concrete provider adapter (S12, S17)
    speech/                       # NEW - concrete Speech-to-Text (required) and
                                   # Text-to-Speech (optional) adapters (S18)
```

`integrations/llm/` and `integrations/speech/` are named here for
completeness of the package map; their own internal shape (an `LLMClient`
port, a provider-specific adapter) follows the existing
`integrations/tradingagents/`-style isolation precedent and is not
specified further by this document — designing them in full is
implementation-Epic work.

`conversation/tools/` mirrors `committee/analysts/`'s existing one-file-
per-concrete-implementation shape (spec-003), and `conversation/adapters/`
mirrors `memory/ledgers/`'s `base.py` + concrete-adapter split (spec-005)
— both already-precedented package-internal layouts, not new conventions
invented by this spec.

## 24. Public API

### `conversation/__init__.py`

```python
__all__ = [
    # models
    "ChannelType", "UserIdentity", "ConversationIdentity", "AudioPayload",
    "ConversationMessage", "ConversationResponse", "IntentType", "Intent",
    "ConversationSession",
    # ports and their default/optional adapters
    "AccessPolicy", "AllowlistAccessPolicy",
    "CommandParser",
    "IntentRouter", "RuleBasedIntentRouter", "LLMIntentRouter", "FallbackIntentRouter",
    "SymbolResolver", "StaticSymbolResolver",
    "AssetClassDecision", "AssetClassPolicy", "TickerPatternAssetClassPolicy",
    "SessionStore", "InMemorySessionStore",
    "SpeechToTranscriber", "TextToSpeechSynthesizer",
    "ResponseSummarizer", "TemplateResponseSummarizer", "LLMResponseSummarizer",
    "FallbackResponseSummarizer",
    "ConversationAdapter",
    # orchestration
    "ConversationService",
    "build_default_conversation_service",
    # errors
    "ConversationError", "TranscriptionError", "SynthesisError", "SessionStoreError",
    "LLMInterpretationError",
]
```

### `conversation/tools/__init__.py`

```python
__all__ = [
    "Tool", "ToolOutcome", "HelpContent", "NotYetAvailable", "PolicyDeclined",
    "ToolRouter",
    "AnalyzeAssetTool", "ExplainDecisionTool", "DecisionHistoryTool",
    "CompareAssetsTool", "PortfolioSummaryTool", "PerformanceSummaryTool",
    "HelpTool", "UnsupportedTradeRequestTool",
]
```

### Proposed additions to `application/__init__.py` (not applied — §5)

```python
__all__ = [
    # ... existing names, unchanged ...
    "DecisionQueryService",
    "DecisionNotFoundInApplicationError",
    "DecisionQueryFailedError",
    "build_default_decision_query_service",
]
```

## 25. Composition root

### `conversation/factory.py`

```python
from __future__ import annotations

from investment_committee.application import (
    build_default_application_service,
    build_default_decision_query_service,
    build_default_record_decision_use_case,
)

from .access import AllowlistAccessPolicy
from .asset_policy import TickerPatternAssetClassPolicy
from .command_parser import CommandParser
from .intent_router import (
    FallbackIntentRouter,
    IntentRouter,
    LLMIntentRouter,
    RuleBasedIntentRouter,
)
from .service import ConversationService
from .session_store import InMemorySessionStore
from .speech import SpeechToTranscriber, TextToSpeechSynthesizer
from .summarizer import (
    FallbackResponseSummarizer,
    LLMResponseSummarizer,
    ResponseSummarizer,
    TemplateResponseSummarizer,
)
from .symbol_resolver import StaticSymbolResolver
from .tools.analyze_asset import AnalyzeAssetTool
from .tools.compare_assets import CompareAssetsTool
from .tools.decision_history import DecisionHistoryTool
from .tools.explain_decision import ExplainDecisionTool
from .tools.help import HelpTool
from .tools.performance_summary import PerformanceSummaryTool
from .tools.portfolio_summary import PortfolioSummaryTool
from .tools.router import ToolRouter
from .tools.unsupported_trade import UnsupportedTradeRequestTool
from .models import IntentType


def build_default_conversation_service(
    allowed_user_ids: frozenset[str],
    transcriber: SpeechToTranscriber,
    llm_client: LLMClient | None = None,
    synthesizer: TextToSpeechSynthesizer | None = None,
) -> ConversationService:
    """Assemble the production ConversationService.

    `transcriber` is required (S18) - voice is an Epic 8 acceptance
    requirement, not an optional extra, so there is no default that leaves
    voice silently unsupported. `synthesizer` stays optional (S18).

    `llm_client` is optional (S12, S17): when provided, IntentRouter and
    ResponseSummarizer are each an LLM-backed implementation wrapped by a
    Fallback* adapter that drops back to the deterministic implementation
    on any failure; when omitted (the CLAUDE.md-compatible default),
    the service runs on RuleBasedIntentRouter/TemplateResponseSummarizer
    alone, with no network dependency for interpretation at all.
    """
    record_decision_use_case = build_default_record_decision_use_case()
    decision_query_service = build_default_decision_query_service()
    asset_class_policy = TickerPatternAssetClassPolicy()

    intent_router: IntentRouter = RuleBasedIntentRouter(symbol_resolver=StaticSymbolResolver(DEFAULT_NAME_TO_TICKER))
    response_summarizer: ResponseSummarizer = TemplateResponseSummarizer()
    if llm_client is not None:
        intent_router = FallbackIntentRouter(primary=LLMIntentRouter(llm_client), fallback=intent_router)
        response_summarizer = FallbackResponseSummarizer(
            primary=LLMResponseSummarizer(llm_client, template=response_summarizer),
            fallback=response_summarizer,
        )

    tools_by_intent = {
        IntentType.ANALYZE_ASSET: AnalyzeAssetTool(record_decision_use_case, asset_class_policy),
        IntentType.EXPLAIN_DECISION: ExplainDecisionTool(decision_query_service),
        IntentType.DECISION_HISTORY: DecisionHistoryTool(decision_query_service),
        IntentType.COMPARE_ASSETS: CompareAssetsTool(record_decision_use_case, asset_class_policy),
        IntentType.PORTFOLIO_SUMMARY: PortfolioSummaryTool(),
        IntentType.PERFORMANCE_SUMMARY: PerformanceSummaryTool(),
        IntentType.HELP: HelpTool(),
        IntentType.UNKNOWN: HelpTool(),
        IntentType.UNSUPPORTED_TRADE_REQUEST: UnsupportedTradeRequestTool(),
    }

    return ConversationService(
        access_policy=AllowlistAccessPolicy(allowed_user_ids),
        command_parser=CommandParser(),
        intent_router=intent_router,
        tool_router=ToolRouter(tools_by_intent),
        response_summarizer=response_summarizer,
        session_store=InMemorySessionStore(),
        transcriber=transcriber,
        synthesizer=synthesizer,
    )
```

`build_default_application_service` is imported but not called directly
here — `build_default_record_decision_use_case()` and
`build_default_decision_query_service()` each already construct their own
`ApplicationService`/ledger internally (§16, spec-005 §17); this factory
does not duplicate that wiring. `allowed_user_ids` and `transcriber` are
the two required parameters — there is no safe default allowlist (an empty
one would lock every user out, which is at least safe; a wildcard one
would defeat §11's entire purpose) and no safe default transcriber (§18),
so the composition root forces the caller to supply both explicitly.
`LLMClient` (the type `llm_client` is injected as) is defined in
`integrations/llm/`, not `conversation/` — this factory is the one place
in `conversation/` allowed to import it (§8).

`DEFAULT_NAME_TO_TICKER` (the `StaticSymbolResolver` table, §13) is defined
in `conversation/factory.py` itself, next to the one place it is used in
production — mirroring `memory/versioning.py`'s `DETERMINISTIC_STRATEGY_METADATA`
placement precedent (spec-005 §13: hand-maintained constants live next to
their composition root, not scattered).

## 26. Testing strategy

Sketched for the implementation Epic, following existing conventions
exactly (duck-typed fakes, static import checks, frozen/validation tests
per dataclass, conformance suites for each port):

- **`tests/conversation/test_models.py`** — every frozen dataclass's
  validation (exactly-one-of text/audio on `ConversationMessage`, blank
  rejection, timezone-naive `received_at`/`created_at` rejected,
  `Intent.parameters` immutable after construction); `ConversationSession`
  confirmed mutable (assigning `last_asset` after construction succeeds,
  unlike every frozen sibling type).
- **`tests/conversation/test_command_parser.py`** — `/help`, `/analyze
  AAPL`, `/history AAPL` each parse to the expected `Intent`; an
  unrecognized command (`/frobnicate`) parses to `Intent(type=UNKNOWN, ...)`,
  not `None`; text not starting with `/` returns `None`; a `/compare`
  command's argument normalizes to the comma-joined `symbols` form
  `CompareAssetsTool` expects.
- **`tests/conversation/test_intent_router.py`** — `RuleBasedIntentRouter`
  against a fixed table of (input text, expected `IntentType`,
  expected `parameters`) cases covering every intent, including the
  paired disambiguation case that must resolve differently: `"Should I buy
  Nvidia?"` → `ANALYZE_ASSET`, `"Buy Bitcoin."` →
  `UNSUPPORTED_TRADE_REQUEST`; unrecognized text → `UNKNOWN`; never raises
  for empty or nonsense input.
- **`tests/conversation/test_fallback_routers.py`** — `FallbackIntentRouter`/
  `FallbackResponseSummarizer` against fakes: primary succeeds → primary's
  result is returned and the fallback is never called; primary raises
  `LLMInterpretationError` → the fallback is called and its result is
  returned; the wrapper itself never raises. For `LLMIntentRouter`
  specifically (using a fake `LLMClient`): a response naming a valid
  `IntentType` constructs the matching `Intent`; a response naming
  anything else (malformed JSON, an unrecognized intent string, a
  hallucinated tool name) resolves to `IntentType.UNKNOWN`, never to a
  dynamically-typed `Intent` — the concrete test for "cannot execute
  arbitrary tools." For `LLMResponseSummarizer` (using a fake `LLMClient`):
  rephrased text containing only numbers present in the template input is
  accepted; rephrased text introducing any new number raises
  `LLMInterpretationError` (the grounding check).
- **`tests/conversation/test_symbol_resolver.py`** — `StaticSymbolResolver`
  resolves known names case-insensitively; returns `None` (not an
  exception) for an unknown name; contains no crypto/commodity entries.
- **`tests/conversation/test_asset_policy.py`** — `TickerPatternAssetClassPolicy.classify`
  against a fixed table: `AAPL`, `BRK.B`, `SPY` (ETF-shaped) → `SUPPORTED`;
  `EURUSD=X`, `GBPJPY=X` → `SUPPORTED`; `XAUUSD=X`, `XAGUSD=X` →
  `UNSUPPORTED_COMMODITY` (the explicit precious-metal-as-FX-pair trap);
  `BTC-USD`, `ETH-USD` → `UNSUPPORTED_CRYPTO`; `GC=F`, `CL=F` →
  `UNSUPPORTED_COMMODITY`; an option-shaped or otherwise unrecognized
  string → `UNSUPPORTED_UNRECOGNIZED`; never raises.
- **`tests/conversation/tools/test_tool_router.py`** — constructing a
  `ToolRouter` missing any `IntentType` raises `ValueError` at construction;
  a fully-populated router's `tool_for` returns exactly the registered
  `Tool` for every member, including `HELP`/`UNKNOWN` both mapping to the
  same `HelpTool` instance passed in.
- **`tests/conversation/tools/test_*.py`** (one per concrete `Tool`) —
  using duck-typed fakes for `RecordDecisionUseCase`/`DecisionQueryService`
  (mirroring `tests/application/_support.py`'s existing fake pattern):
  each `Tool.execute` calls its one permitted collaborator with the
  expected arguments and returns the documented `ToolOutcome` type;
  `HelpTool`/`PortfolioSummaryTool`/`PerformanceSummaryTool`/
  `UnsupportedTradeRequestTool` call no collaborator at all (assert zero
  calls on every fake). `AnalyzeAssetTool`/`CompareAssetsTool` specifically:
  an unsupported symbol (e.g. `BTC-USD`, `XAUUSD=X`) returns
  `PolicyDeclined` **and the fake `RecordDecisionUseCase.analyze_and_record`
  is never called** — the concrete, automated proof that an unsupported
  asset never reaches the Application layer or the Decision Ledger; a
  supported symbol calls it normally; for `CompareAssetsTool`, one
  supported and one unsupported symbol results in `PolicyDeclined` with
  **zero** `analyze_and_record` calls (neither asset is analyzed).
- **`tests/conversation/test_summarizer.py`** — `TemplateResponseSummarizer`
  produces non-empty, ticker-containing text for a `DecisionRecord`
  outcome; a `Sequence[DecisionRecord]` with mixed actions produces a
  correct BUY/HOLD/SELL tally for a `DECISION_HISTORY` count-style intent;
  a `NotYetAvailable`/`PolicyDeclined` outcome's `message` field appears
  verbatim in the summary (no re-wording); a `HelpContent` outcome with
  `intent.type == UNKNOWN` and `intent.parameters["clarification"]` set
  surfaces that clarification text instead of the generic capability list.
- **`tests/conversation/test_service.py`** — the core orchestration
  (§9), using fakes for every collaborator (`transcriber` is always a fake,
  never omitted, since it is now a required constructor parameter — §18):
  - unauthorized user → fixed decline response; zero calls to
    `SessionStore`, `CommandParser`, `IntentRouter`, or any `Tool`;
  - happy path for free-form text: `SessionStore.get` → `CommandParser.parse`
    (returns `None`) → `IntentRouter.route` → `ToolRouter.tool_for` →
    `Tool.execute` → `ResponseSummarizer.summarize` → `SessionStore.save`,
    in that order;
  - happy path for an explicit `/command`: `CommandParser.parse` returns
    an `Intent` → `ToolRouter.tool_for` → `Tool.execute` → ... — **a fake
    `IntentRouter` records zero calls**, the concrete, automated proof
    that an explicit command never reaches (and therefore never invokes)
    an LLM-backed router;
  - a fake `Tool.execute` raising each `ApplicationServiceError` subclass
    (including `DecisionRecordingError`, `DecisionNotFoundInApplicationError`,
    `DecisionQueryFailedError`) is caught and produces a friendly response,
    never propagates;
  - a fake raising a bare `RuntimeError` is caught by the outer
    `except Exception` (§22) and produces the generic fallback response —
    proving the one deliberately broad `except` exists and behaves as
    specified, not that it is accidentally present;
  - a message with `audio` set and the fake `transcriber` configured to
    succeed calls `transcribe` exactly once, then proceeds through the
    normal text path with the transcribed string, through the same
    `CommandParser`/`IntentRouter`/`ToolRouter`/`Tool`/`ResponseSummarizer`
    pipeline a typed message uses — the concrete, automated proof of the
    end-to-end voice path (§18);
  - a message with `audio` set and the fake `transcriber` configured to
    raise `TranscriptionError` returns the "couldn't understand that voice
    message" response without calling `CommandParser` or `IntentRouter`;
  - `ConversationService.handle_message` never raises, exhaustively, for
    every fake-failure scenario exercised above (a property asserted once
    per scenario, not just implied by "the test didn't fail").
- **`tests/interfaces/telegram/test_adapter.py`** — `TelegramConversationAdapter`
  against a fake Bot API client (never a real Telegram server, per
  CLAUDE.md's "Local Windows development only" and this codebase's
  no-live-network-calls-in-tests convention, spec-002 through spec-005): a
  text update builds a `ConversationMessage` with `text` set and `audio`
  `None`; a voice update downloads the file via the fake client and builds
  a `ConversationMessage` with `audio` set and `text` `None`; `send`
  calls the fake client's `send_message` (and `send_voice`, if
  `response.audio` is set) with the right chat id.
- **`tests/conversation/test_session_store.py`** — `InMemorySessionStore.get`
  on an unseen `ConversationIdentity` returns a fresh, empty
  `ConversationSession` (never raises); `save` then `get` round-trips.
- **`tests/conversation/test_access.py`** — `AllowlistAccessPolicy` accepts
  exactly the configured ids and rejects everything else, including a
  blank `user_id`.
- **`tests/conversation/test_no_forbidden_imports.py`** — mirroring
  `tests/application/test_no_forbidden_imports.py` and
  `tests/memory/test_no_forbidden_imports.py` exactly: a static scan
  proving nothing under `conversation/` (outside `factory.py`, the
  documented composition-root exception, §8) imports
  `investment_committee.memory`, `investment_committee.committee`,
  `investment_committee.indicators`, or
  `investment_committee.market_intelligence` for anything other than a
  type reference — approximated the same way spec-005's equivalent check
  does, as an import-statement scan, not a full call-graph analysis;
  `conversation/` importing only `investment_committee.application` (plus
  `investment_committee.domain` for `Asset` type references) for behavior
  is asserted as the positive case.
- **`tests/application/test_decision_query.py`** — `DecisionQueryService`
  wraps `DecisionNotFoundError`/`LedgerReadError` from a fake
  `DecisionLedger` into `DecisionNotFoundInApplicationError`/
  `DecisionQueryFailedError` with `__cause__` preserved (mirrors
  `tests/application/test_record_decision.py`'s existing exception-chaining
  assertions); `list_by_asset` normalizes ticker case/whitespace before
  constructing the lookup `Asset`; `list_recent(limit=N)` returns at most
  `N` records, most-recent-`recorded_at`-first, from an unordered fake
  ledger response.
- **`tests/conversation/test_init.py`** and
  **`tests/conversation/tools/test_init.py`** — every name in the
  respective `__all__` is importable, mirroring
  `tests/application/test_init.py`'s existing pattern.

## 27. Acceptance criteria

(For the implementation Epic this spec precedes — not claimed as satisfied
by this document, which contains no code.)

- Every file listed in §23 exists and implements exactly the public API
  defined in this spec.
- `application/decision_query.py` exists; `build_default_decision_query_service()`
  is added to the **existing** `application/factory.py` (no new file for
  it); `application/__init__.py` exports the four names in §24's proposed
  addition.
- `ConversationMessage`, `ConversationResponse`, `Intent` are frozen,
  self-validating dataclasses; `ConversationSession` is the sole
  intentionally-mutable dataclass in `conversation/`. No `PendingConfirmation`
  type exists (§19).
- `CommandParser.parse`, `IntentRouter.route`, `Tool.execute`'s internal
  collaborators (`SymbolResolver.resolve`, `AssetClassPolicy.classify`),
  and `SessionStore.get` never raise, per their documented contracts;
  `ConversationService.handle_message` never raises, under any
  fake-collaborator failure mode exercised in §26.
- `ToolRouter` requires a `Tool` registered for every `IntentType` member at
  construction time; `HELP` and `UNKNOWN` both route to `HelpTool`.
- `CommandParser` is checked before `IntentRouter` for every message, and
  an explicit `/command` never reaches `IntentRouter` — proven by a test
  asserting zero calls to a fake `IntentRouter` when processing a
  `/command` message (§26).
- `RuleBasedIntentRouter` and `TemplateResponseSummarizer` are always
  shipped and require no network access; `LLMIntentRouter` and
  `LLMResponseSummarizer` are optional, Epic-8-in-scope adapters, used only
  when wrapped in `FallbackIntentRouter`/`FallbackResponseSummarizer` and
  only when a `llm_client` is supplied to
  `build_default_conversation_service` (§25). An `IntentRouter`'s output is
  always validated against `IntentType` before an `Intent` is constructed —
  an unrecognized model response resolves to `UNKNOWN`, never a
  dynamically-typed `Intent` or an unregistered tool call (§12).
- `AnalyzeAssetTool`/`CompareAssetsTool` reject crypto, commodity
  (including `XAUUSD=X`), option, and futures-shaped tickers via
  `AssetClassPolicy` **before** calling `RecordDecisionUseCase.analyze_and_record`
  — proven by a test asserting zero calls to a fake
  `RecordDecisionUseCase` for an unsupported symbol (§13, §26).
- `AnalyzeAssetTool`/`CompareAssetsTool`/`ExplainDecisionTool`/
  `DecisionHistoryTool`/`HelpTool`/`PortfolioSummaryTool`/
  `PerformanceSummaryTool`/`UnsupportedTradeRequestTool` each call only
  `investment_committee.application` collaborators (`RecordDecisionUseCase`,
  `DecisionQueryService`) or nothing at all — never `memory`, `committee`,
  `indicators`, or `market_intelligence` directly.
- No `Tool.requires_confirmation` attribute and no `PendingConfirmation`
  mechanism exist anywhere in `conversation/` (§19).
- `SpeechToTranscriber` is a required constructor parameter of
  `ConversationService`/`build_default_conversation_service`; a Telegram
  voice message is downloaded by `TelegramConversationAdapter`,
  transcribed, and re-enters the same pipeline typed text uses, ending in
  a text `ConversationResponse` (§18, §26). `TextToSpeechSynthesizer`
  remains optional; its absence never blocks a reply.
- `TelegramConversationAdapter` (`interfaces/telegram/adapter.py`) is
  implemented and tested against a fake Bot API client — no real Telegram
  or network calls in any test (§21, §26).
- `AllowlistAccessPolicy` rejects any `user_id` not in its configured set;
  `ConversationService` checks authorization before any other collaborator
  is called.
- No import of `interfaces` anywhere in `conversation/`; no import of
  `conversation/` anywhere in `application/`, `memory/`, `committee/`,
  `indicators/`, `market_intelligence/`, or `domain/`; no new member added
  to `domain.AssetType` (§13).
- No new external dependency in `conversation/`'s own core beyond what
  Epic 8's own concrete adapters require (`python-telegram-bot` in
  `interfaces/telegram/`; an LLM SDK in `integrations/llm/`; an STT/TTS
  SDK in `integrations/speech/`) — `conversation/`'s core logic
  (everything outside those adapter-facing edges) remains standard library
  only.
- `pytest` passes, including every new test package listed in §26, with no
  regressions in any existing suite, and with no real Telegram, LLM, or
  speech-provider network calls in any test (§26).
- `docs/architecture.md` is corrected to add the `conversation/` layer and
  its dependency direction (§8) as part of this Epic's implementation PR,
  before it merges — the one required documentation edit, mirroring
  spec-005's identical `memory → committee, indicators` requirement.
  `docs/roadmap.md` is updated to reflect that Epic 8 (not a later,
  separately-numbered epic) delivers the Telegram interface, as a
  separate, deliberate follow-up.

## 28. Alternatives considered

- **Package named `assistant/` instead of `conversation/`.** Rejected —
  §8 explains this names the product, not the abstraction, and risks
  confusion with an "agent" concept this spec explicitly excludes (§5).
- **`IntentRouter`/`ResponseSummarizer` requiring an LLM, with no
  deterministic-only mode.** Rejected: Epic 8 must remain fully functional
  with no LLM configured — `RuleBasedIntentRouter`/`TemplateResponseSummarizer`
  are never optional themselves, only the LLM layer on top of them is
  (§6, §12, §17, §25). This is also the concrete mechanism satisfying
  "deterministic fallback behavior when the provider is disabled or
  unavailable."
- **Adding `domain.AssetType.FOREX`** to represent the newly-supported
  Forex asset class. Rejected — see §13's full reasoning: the rejection
  check has to run on a raw ticker string before an `Asset` exists, and
  `AssetType` is not populated reliably today (`YFinanceProvider` always
  assumes `STOCK`), so a new enum member would be unused, false-precision
  surface rather than a working classifier.
- **Letting the LLM decide asset-class eligibility** (as part of intent
  interpretation) instead of a separate deterministic `AssetClassPolicy`.
  Rejected: eligibility is a hard product exclusion (`CLAUDE.md`-level, not
  a judgment call), and the Epic prompt's own constraints forbid the LLM
  from being the thing that decides what reaches the Application layer —
  `AssetClassPolicy` is plain, deterministic, standard-library Python,
  checked inside the `Tool`, never inside `IntentRouter` (§13, §15.1).
- **A generic `Any`-typed `ToolResult.data` payload** instead of the closed
  `ToolOutcome` union. Rejected: an untyped payload would force
  `ResponseSummarizer` to introspect or duck-type its input, and would let
  a `Tool` return anything at all with no compile-time or review-time
  signal about whether `ResponseSummarizer` actually handles it. The closed
  union (§14) makes an unhandled case a `match`-statement gap a type
  checker or reviewer can catch.
- **`CompareAssets` computing a "which is better" score.** Rejected — no
  such comparison logic exists anywhere in this codebase yet (Strategy
  Comparison, Epic 13, compares *strategies*, not *assets* — §7), and
  inventing one here would be exactly the kind of speculative, unreviewed
  domain logic `CLAUDE.md` and ADR-001 both warn against.
- **Routing `EXPLAIN_DECISION` etc. through `ApplicationService.analyze_asset`
  again** instead of adding `DecisionQueryService`. Rejected: re-analyzing
  fetches fresh market data and can silently produce a *different* decision
  than the one being asked about — the same reasoning spec-005 §17 already
  used to justify splitting `record()` from `analyze_and_record()`.
- **Conversational `AnalyzeAsset` not recording to the ledger** (matching
  the dev CLI's unrecorded behavior). Rejected as the default — see §15.1's
  rationale; flagged in §29 as a judgment call, not a closed question.
- **A durable `SessionStore` adapter shipped in this Epic.** Rejected —
  the Epic prompt's "intentionally limited" conversational memory is best
  honored by session state that is *allowed* to be lost, not merely
  *currently* non-durable (§20).
- **One shared `IntentClassifier`-named port instead of the Epic prompt's
  `IntentRouter` name.** Rejected in favor of using the prompt's own
  requested name directly (§12) — introducing a second, near-synonymous
  name would add a distinction with no behavioral difference.
- **Async `ConversationService.handle_message`.** Rejected — see §21's
  "sync core, adapter owns any async boundary" reasoning: keeps `conversation/`
  consistent with every other layer's synchronous style rather than
  introducing `asyncio` as a new, cross-cutting dependency of the core
  package.

## 29. Design decisions requiring approval

Gathered here because each involves a genuine judgment call this spec made
without an unambiguous instruction to resolve it:

1. **Conversational `AnalyzeAsset` always records to the Decision Ledger,
   with no opt-out in this Epic** (§15.1). An argument exists for a
   lighter-weight "just curious, don't record" mode, at the cost of a
   second `AnalyzeAssetTool` code path or an intent parameter
   (`parameters["record"] = "false"`) this spec does not currently define.
2. **The imperative-vs-interrogative trade-language heuristic** (§12) is a
   best-effort natural-language rule, not a robust classifier. It is
   deliberately biased toward the safe failure mode (§12 explains why), but
   a reviewer may want a stricter or differently-phrased rule set before
   implementation.
3. **`ToolRouter` requires all nine `IntentType` members registered at
   construction, including `UNKNOWN`.** An alternative — a router with a
   built-in fallback for any unregistered intent — was considered and
   rejected because it would let a genuinely missing tool wiring pass
   silently instead of failing fast at construction (§14); a reviewer
   could still prefer a softer default.
4. **The LLM grounding check (§17) is numeric-token containment, not full
   semantic verification.** It catches a fabricated number cheaply but
   would not catch every possible ungrounded claim phrased without a new
   digit. A stricter check is a legitimate ask for a future revision.
5. **`TickerPatternAssetClassPolicy`'s coverage** (§13) is a fixed,
   hand-written pattern set (a handful of crypto suffixes, one futures
   suffix, four metal codes). It is deliberately conservative
   (unrecognized shapes are rejected, not allowed), but a reviewer may want
   a larger or differently-sourced pattern list before implementation.

`DecisionQueryService`'s placement in `application/` (previously listed
here) is no longer an open question — confirmed (§16).

## 30. Unresolved questions

Carried forward for architecture review, not resolved by this spec:

1. **Where `AllowlistAccessPolicy`'s allowed user id(s) are actually read
   from** (environment variable vs. local config file) — an
   implementation-time decision (§11), deliberately not made here.
2. **Which LLM provider Epic 8 wires by default (if any) behind
   `integrations/llm/`**, and which Speech-to-Text provider behind
   `integrations/speech/` (§18, §25) — both are now required design
   decisions (voice) or approved-but-unselected options (LLM), not
   deferred architecture. Neither vendor is named by this spec.
3. **`ConversationSession.last_asset` is defined but currently unused by
   any `Tool` in this spec** (§20) — included for forward-compatibility
   with a future elliptical-follow-up intent, but no such intent exists
   yet; a reviewer may prefer to omit it until a concrete consumer needs
   it, matching spec-005 §14's "no speculative port surface" precedent
   applied to this field instead of a method.
4. **`StaticSymbolResolver`'s name-to-ticker table's coverage and
   maintenance** — how large it should be, and who updates it as new
   assets are analyzed, is not decided (§13).
5. **The LLM grounding check (§17) is a partial, not complete,
   verification of "the summary never states an unsupported fact"** — see
   §29.4. It catches fabricated numbers; it does not verify every possible
   ungrounded claim.
6. **The `docs/architecture.md`/`docs/roadmap.md` renumbering this Epic
   requires** (§8, §27) — inserting `conversation/` as layer 7 pushes
   `interfaces/` to layer 8; whether any other document
   (`docs/adr/`, `README`) references the current layer numbers and would
   need a matching update is not audited by this spec.
7. **Whether `DecisionHistoryTool`'s client-side date-range filtering
   (§15.3) should instead become a `DecisionLedger` port addition** (e.g.
   `find_recorded_between(start, end)`) once a second concrete consumer
   needs it — spec-005 §20 already anticipated this exact question and
   left it additive; this spec does not resolve it either.
8. **Rate limiting / abuse handling** is not designed — `AccessPolicy`
   handles identity, not request volume. Lower risk now that Epic 8 is
   confirmed Telegram-only, typically one-to-one DM usage (§7), but not
   formally out of scope for a determined single authorized user sending a
   high volume of requests.
9. **`TickerPatternAssetClassPolicy`'s pattern coverage** (§13, §29.5) may
   need to grow as real tickers surface shapes the current patterns don't
   anticipate — no process for updating it is defined.

## 31. Revision Summary (Iteration 2)

This revision responds to architecture review of revision 1
against a frozen Epic 8 product scope. Eight product decisions were
approved and applied; one additional architectural question (Forex asset
modeling) was resolved with an explanation before the spec was changed, per
the review request. Section numbers are unchanged from revision 1 — every
change below was made *within* an existing section to avoid invalidating
the document's own extensive internal cross-references (§ citations
throughout).

1. **Telegram is now Epic 8's real, in-scope deliverable, not a future
   adapter.** §2, §4, §5, §21, and §23 no longer defer
   `TelegramConversationAdapter` to a separate "Epic 10" — it is this
   Epic's implemented, tested adapter, including the voice-message
   download path (§21).
2. **An optional, structurally bounded LLM is now an Epic 8 adapter.**
   `LLMIntentRouter` and `LLMResponseSummarizer` (§12, §17) are designed in
   full, each wrapped by a `Fallback*` adapter
   (`FallbackIntentRouter`/`FallbackResponseSummarizer`) that transparently
   reverts to the deterministic implementation on any failure or invalid
   output — the concrete "deterministic fallback when the provider is
   disabled or unavailable" mechanism. `LLMIntentRouter`'s output is
   validated against the closed `IntentType` enum before an `Intent` is
   ever constructed; `LLMResponseSummarizer` is grounded by construction
   (it rephrases already-computed template text, never raw data) and
   validated by a numeric-token containment check. A table in §12
   cross-references every "the LLM must not..." constraint from the Epic
   prompt against the specific mechanism that enforces it.
3. **Explicit `/commands` are parsed by a new, wholly deterministic
   `CommandParser`** (§9, §12), checked before `IntentRouter` and never
   falling through to it — the LLM is structurally unreachable from a
   `/command` message. Both paths converge on the same `Intent` type and
   downstream `ToolRouter`/`Tool` pipeline.
4. **Voice (Speech-to-Text) is now a required Epic 8 capability.**
   `ConversationService`'s `transcriber` parameter is required, not
   optional (§18, §25) — a service cannot be constructed without one. The
   full path (Telegram download → transcription → the same pipeline typed
   text uses → text reply) is specified end to end (§9, §18, §21) and has
   a dedicated acceptance criterion and test (§26, §27). Text-to-Speech
   remains optional, unchanged.
5. **A new `AssetClassPolicy` rejects unsupported asset classes before any
   Application Service call.** §13 adds `AssetClassDecision`,
   `AssetClassPolicy`, and the deterministic `TickerPatternAssetClassPolicy`
   adapter, explicitly catching the XAU/USD-as-FX-pair case by name.
   `AnalyzeAssetTool`/`CompareAssetsTool` (§15.1, §15.4) call it before
   `RecordDecisionUseCase.analyze_and_record`, so an unsupported asset is
   never analyzed and never written to the Decision Ledger. §13 also
   records why `domain.AssetType.FOREX` was considered and rejected in
   favor of this ticker-pattern approach (see the standalone explanation
   above the edits, and §28).
6. **The generic confirmation-flow mechanism is removed.** `Tool.requires_confirmation`,
   `PendingConfirmation`, and `conversation/confirmation.py` no longer
   exist (§10, §14, §19); §19 is reduced to a short explanation of why the
   mechanism was premature for Epic 8 and where it would belong later
   (a future, unscheduled broker-execution epic).
7. **Multi-channel framing is reduced.** §4, §7, and §8 now state plainly
   that Telegram is Epic 8's only built and tested channel; the
   `ConversationAdapter`/`ChannelType` abstraction is kept only because it
   is what isolates the Telegram SDK (a `CLAUDE.md` requirement in its own
   right), not because Epic 8 targets any other channel.
8. **`DecisionQueryService`'s placement in `application/` is confirmed**,
   not merely proposed — removed from "design decisions requiring
   approval" (§29); §16 itself is otherwise unchanged.
9. **Forex asset-class support does not add `domain.AssetType.FOREX`.**
   Resolved, with reasoning recorded in §13: the rejection check must run
   on a raw ticker string before an `Asset`/`AssetType` exists at all, and
   `AssetType` is not populated reliably by `YFinanceProvider` today, so a
   new enum member would be unused false-precision surface rather than a
   working classifier. Forex eligibility is decided by the same
   ticker-pattern `AssetClassPolicy` introduced for decision 5, using the
   `EURUSD=X`-style convention `market_intelligence` already passes
   through unchanged.

**Not changed in this revision:** the core domain model shape (`Intent`,
`ConversationMessage`/`ConversationResponse`), `Tool`/`ToolOutcome`/`ToolRouter`'s
design, `DecisionQueryService`'s implementation (§16), the session model's
size and purpose beyond removing the confirmation slot (§20), the error
model's core shape (§22), and the overall layered architecture
(`interfaces → conversation → application`, §8) — these were reviewed and
found sound, and are preserved as-is per the instruction to keep this
revision minimal and additive rather than a rewrite.
