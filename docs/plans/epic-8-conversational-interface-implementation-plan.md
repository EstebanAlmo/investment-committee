# Epic 8 — Conversational Interface: Implementation Plan

## 1. Status

Active. This is the phased implementation plan for the architecture already
approved in `docs/specs/spec-006-conversational-interface.md` ("Draft.
Spec-only"). This document does not change that architecture — it sequences
building it. Where this plan and spec-006 could be read as disagreeing,
spec-006 is authoritative; this plan exists to order the work, not to
redesign it.

This plan was recreated in the current session. An earlier version (Phases
1–12, no Phase 0) was produced in a prior session but was never committed
or saved as a document, so it no longer exists as an artifact. It is
reconstructed here directly from spec-006's own section structure — each
phase below maps onto one or a small group of spec-006 sections, in the
dependency order spec-006 itself already implies (models → identity/session
→ eligibility → interpretation → application read path → tools →
summarization → voice → orchestration → composition → channel → hardening).
Two decisions were made explicitly in this session and are called out where
they apply:

- **Phase 0** (Telegram connectivity bootstrap) is inserted before Phase 1,
  approved separately from the Phases 1–12 architecture work, to prove
  local config, long polling, and allowlisting before any conversation/
  code exists.
- **Phase 4**'s first concrete LLM provider is **DeepSeek**, not OpenAI.
  This changes only which concrete adapter class Phase 4 builds — the
  `LLMClient` abstraction, and everything spec-006 specifies about how it's
  used, is unchanged. See Phase 4 below.

## 2. Ground rules for every phase

- No phase may start work another phase is scoped to do. In particular:
  no phase before Phase 4 touches `integrations/llm/`, loads an LLM
  credential, or imports a concrete LLM SDK; no phase before Phase 11
  builds `TelegramConversationAdapter`.
- Every phase ships with its own automated tests before being considered
  done, per `CLAUDE.md`'s "add a corresponding minimal test" rule and
  spec-006 §26's testing strategy. No test in this Epic makes a live
  network call — Telegram, LLM provider, or Speech-to-Text/Text-to-Speech
  — matching spec-006 §26 and this codebase's existing convention
  (spec-002 through spec-005).
- `conversation/` code never imports `memory`, `committee`, `indicators`,
  or `market_intelligence` directly, except type references — enforced by
  `tests/conversation/test_no_forbidden_imports.py` (spec-006 §26), which
  Phase 1 introduces and every later phase's new files must keep passing.
- `docs/architecture.md` and `docs/roadmap.md` are updated once, in Phase
  12, per spec-006 §27 — not touched piecemeal in earlier phases.
- Run `pytest` from the repository root after every phase.

## 3. Phase 0 — Telegram connectivity bootstrap

**Objective:** Prove local configuration, Telegram long polling, and
identity allowlisting before implementing the conversation architecture.

**Scope:**

- Load configuration from environment variables and a local uncommitted
  `.env` file.
- Validate `TELEGRAM_BOT_TOKEN`.
- Validate `TELEGRAM_ALLOWED_USER_IDS`.
- Start the Telegram bot using long polling.
- Reject users not present in the allowlist.
- Implement `/ping`, responding with a simple health response.
- Implement `/id`, responding with the requesting Telegram user ID.
- Add focused automated tests using fabricated credentials and fake
  Telegram updates.
- Add or verify `.env` and secret files are ignored by git.

**Explicit exclusions:**

- No `conversation/` package.
- No `ConversationService`.
- No `LLMClient` or LLM calls.
- No DeepSeek integration.
- No speech-to-text.
- No Investment Committee application services.
- No Decision Ledger access.
- No webhook or web server.
- No implementation of Phases 1–12.

Phase 0 lives entirely under `src/investment_committee/interfaces/telegram/`
as a standalone bootstrap — it does not depend on anything Phase 1+ builds.
Its config-loading and allowlist logic is expected to be reused (not
duplicated) by `TelegramConversationAdapter` in Phase 11, once that adapter
exists; how much of Phase 0's code survives unchanged into Phase 11 is that
phase's own decision, not committed to here.

## 4. Phase 1 — Conversation domain models & error hierarchy

**Spec refs:** spec-006 §10, §22 (exception names only), §23 (partial).

**Builds:** `conversation/__init__.py` (models/errors exports only),
`conversation/_validation.py`, `conversation/models.py`,
`conversation/errors.py`.

**Delivers:** `ChannelType`, `UserIdentity`, `ConversationIdentity`,
`AudioPayload`, `ConversationMessage`, `ConversationResponse`, `IntentType`,
`Intent`, `ConversationSession`; `ConversationError`, `TranscriptionError`,
`SynthesisError`, `SessionStoreError`, `LLMInterpretationError`.

**Tests:** `tests/conversation/test_models.py`,
`tests/conversation/test_no_forbidden_imports.py` (introduced here, kept
green by every later phase).

**Exit criteria:** every dataclass except `ConversationSession` is frozen
and self-validating per spec-006 §10's field notes (exactly-one-of
text/audio, blank rejection, timezone-aware timestamps, immutable
`Intent.parameters`); `ConversationSession` is confirmed mutable.

## 5. Phase 2 — Identity, access control & session state

**Spec refs:** spec-006 §11, §20.

**Builds:** `conversation/access.py`, `conversation/session_store.py`.

**Delivers:** `AccessPolicy`/`AllowlistAccessPolicy`,
`SessionStore`/`InMemorySessionStore`.

**Tests:** `tests/conversation/test_access.py`,
`tests/conversation/test_session_store.py`.

**Exit criteria:** `AllowlistAccessPolicy` accepts exactly the configured
ids, rejects everything else including a blank id;
`InMemorySessionStore.get` never raises on an unseen
`ConversationIdentity` and returns a fresh session; `save`→`get`
round-trips.

**Note:** Phase 0 already established `TELEGRAM_ALLOWED_USER_IDS` as the
env var carrying allowed ids for the Telegram channel. Phase 9's
composition root reuses that same var to build the production
`AllowlistAccessPolicy` — resolving spec-006 §30.1 for the Telegram
channel specifically (still deliberately unresolved for any hypothetical
future channel).

## 6. Phase 3 — Symbol resolution & asset-class eligibility

**Spec refs:** spec-006 §13.

**Builds:** `conversation/symbol_resolver.py`, `conversation/asset_policy.py`.

**Delivers:** `SymbolResolver`/`StaticSymbolResolver`;
`AssetClassDecision`/`AssetClassPolicy`/`TickerPatternAssetClassPolicy`.

**Tests:** `tests/conversation/test_symbol_resolver.py`,
`tests/conversation/test_asset_policy.py` — the fixed classification table
from spec-006 §26, including the `XAUUSD=X`-as-commodity trap.

**Exit criteria:** classifier is deny-by-default (anything not positively
recognized is `UNSUPPORTED_*`); `StaticSymbolResolver`'s table contains no
crypto/commodity names; neither collaborator ever raises.

## 7. Phase 4 — LLM provider integration (intent recognition)

**Spec refs:** spec-006 §12, §23 (`integrations/llm/`).

**Provider decision made this session — supersedes any earlier assumption
of OpenAI-first:**

- The `LLMClient` abstraction is kept exactly as spec-006 §12 designed it
  (the sole dependency of `LLMIntentRouter`/`LLMResponseSummarizer`) — not
  simplified, not removed.
- The **first concrete implementation is `DeepSeekClient`**, living behind
  `integrations/llm/`, isolating the DeepSeek API the same way
  `YFinanceProvider` isolates `yfinance` (`CLAUDE.md`: "external
  providers... sit behind an interface/adapter").
- `conversation/` never imports `DeepSeekClient` (or any concrete
  provider) — only the `LLMClient` ABC. `conversation/factory.py` (Phase
  9) is the one file allowed to import a concrete provider class, per
  spec-006 §8/§25.
- **OpenAI is no longer the primary provider** but remains a legitimate
  future adapter (`OpenAIClient`), built only if/when requested — not part
  of this phase's scope. Ollama is likewise a future adapter, not built
  here.
- Provider selection happens in the composition root (Phase 9) through an
  `LLM_PROVIDER` environment variable: `deepseek` (first, and the only
  value this phase actually implements), with `openai` and `ollama`
  reserved as future values. An unrecognized or missing `LLM_PROVIDER`
  when an LLM client is actually requested fails fast with a clear error —
  it never silently guesses a provider.
- DeepSeek credentials (`DEEPSEEK_API_KEY`) are only ever loaded when
  `LLM_PROVIDER=deepseek` is configured. The deterministic path
  (`RuleBasedIntentRouter`/`TemplateResponseSummarizer` alone) remains
  fully functional with zero LLM configuration — spec-006 §25/§28's
  existing requirement, unaffected by which provider is first.

**Builds:** `conversation/command_parser.py`, `conversation/intent_router.py`
(`IntentRouter` ABC, `RuleBasedIntentRouter`, `LLMIntentRouter`,
`FallbackIntentRouter`); `integrations/llm/__init__.py`,
`integrations/llm/client.py` (`LLMClient` ABC), `integrations/llm/deepseek_client.py`
(`DeepSeekClient`), `integrations/llm/config.py` (provider selection helper
consumed only by Phase 9's `conversation/factory.py`).

**Tests:** `tests/conversation/test_command_parser.py`,
`tests/conversation/test_intent_router.py` (including the paired
disambiguation case — "Should I buy Nvidia?" → `ANALYZE_ASSET`, "Buy
Bitcoin." → `UNSUPPORTED_TRADE_REQUEST`), `tests/conversation/test_fallback_routers.py`
(`FallbackIntentRouter` and `LLMIntentRouter` against a fake `LLMClient` —
never a real DeepSeek call), `tests/integrations/llm/test_deepseek_client.py`
(fake HTTP layer only).

**Exit criteria:** identical to spec-006 §27's `IntentRouter`-related
criteria — `CommandParser` checked before `IntentRouter` and an explicit
`/command` never reaches it; an `IntentRouter`'s output is always validated
against `IntentType` before an `Intent` is constructed, never a
dynamically-typed `Intent`; `RuleBasedIntentRouter` requires no network
access. `tests/conversation/test_no_forbidden_imports.py` (Phase 1) is
extended to also assert no file under `conversation/` other than
`factory.py` imports `integrations.llm.deepseek_client` (or any future
concrete provider module).

## 8. Phase 5 — Application layer read path: `DecisionQueryService`

**Spec refs:** spec-006 §16.

**Builds:** `application/decision_query.py` (new file);
`application/factory.py` gains `build_default_decision_query_service()`;
`application/__init__.py` exports the four new names.

**Tests:** `tests/application/test_decision_query.py`.

**Exit criteria:** `DecisionNotFoundError`/`LedgerReadError` from a fake
`DecisionLedger` are wrapped into `DecisionNotFoundInApplicationError`/
`DecisionQueryFailedError` with `__cause__` preserved; `list_by_asset`
normalizes ticker case/whitespace; `list_recent(limit=N)` returns at most
`N` records, most-recent-first, from an unordered fake ledger.

## 9. Phase 6 — Tool routing & per-intent tools

**Spec refs:** spec-006 §14, §15.

**Builds:** `conversation/tools/base.py`, `router.py`, `analyze_asset.py`,
`explain_decision.py`, `decision_history.py`, `compare_assets.py`,
`portfolio_summary.py`, `performance_summary.py`, `help.py`,
`unsupported_trade.py`, `conversation/tools/__init__.py`.

**Tests:** `tests/conversation/tools/test_tool_router.py`,
`tests/conversation/tools/test_*.py` — one per concrete `Tool`, using
duck-typed fakes for `RecordDecisionUseCase`/`DecisionQueryService`.

**Exit criteria:** `ToolRouter.__init__` raises `ValueError` if any
`IntentType` member (including `UNKNOWN`) has no registered `Tool`;
`AnalyzeAssetTool`/`CompareAssetsTool` reject unsupported asset classes via
`AssetClassPolicy` **before** calling `RecordDecisionUseCase.analyze_and_record`
— proven by asserting zero calls on a fake for an unsupported symbol
(`BTC-USD`, `XAUUSD=X`); every tool calls only `application/` collaborators
or nothing at all.

## 10. Phase 7 — Response summarization

**Spec refs:** spec-006 §17. Consumes the `LLMClient` port Phase 4 already
built — no new provider decision here.

**Builds:** `conversation/summarizer.py` (`ResponseSummarizer`,
`TemplateResponseSummarizer`, `LLMResponseSummarizer`,
`FallbackResponseSummarizer`).

**Tests:** `tests/conversation/test_summarizer.py`; the grounding-check
case (rephrased text introducing any number the template didn't contain
raises `LLMInterpretationError`) added to
`tests/conversation/test_fallback_routers.py`.

**Exit criteria:** every `ToolOutcome` variant has a deterministic template
branch; the LLM path only ever rephrases already-rendered template text,
never raw `ToolOutcome` fields; the numeric-token grounding check is
enforced in code, not only documented.

## 11. Phase 8 — Voice support

**Spec refs:** spec-006 §18.

**Builds:** `conversation/speech.py` (`SpeechToTranscriber`,
`TextToSpeechSynthesizer` ports); `integrations/speech/__init__.py` plus a
concrete Speech-to-Text adapter (required) and an optional Text-to-Speech
adapter. Which STT/TTS vendor is used is an open decision per spec-006
§30.2, resolved at the start of this phase, not before.

**Tests:** fakes only for both ports; no live network calls.

**Exit criteria:** a `ConversationService` cannot be constructed without a
`transcriber` (required, no default); a `TranscriptionError` produces the
"couldn't understand that voice message" fallback; absence of a
`synthesizer`, or a `SynthesisError`, degrades to a text-only reply.

## 12. Phase 9 — Conversation orchestration & composition root

**Spec refs:** spec-006 §9, §22, §25.

**Builds:** `conversation/service.py` (`ConversationService`, the full
11-step message flow); `conversation/factory.py`
(`build_default_conversation_service`) — this is where `LLM_PROVIDER` is
actually read and dispatched to `DeepSeekClient` (via Phase 4's
`integrations/llm/config.py`), and where Phase 2's `TELEGRAM_ALLOWED_USER_IDS`
becomes the production `AllowlistAccessPolicy`.

**Tests:** `tests/conversation/test_service.py` — the full scenario list
from spec-006 §26 (unauthorized user short-circuits everything; explicit
`/command` never invokes a fake `IntentRouter`; every `ApplicationServiceError`
subclass is caught and translated; a bare `RuntimeError` is caught by the
one permitted outer `except Exception`; the full voice path; a
`TranscriptionError` short-circuits before `CommandParser`/`IntentRouter`).

**Exit criteria:** `ConversationService.handle_message` never raises, under
every fake-failure mode exercised in §26; `build_default_conversation_service`
requires `allowed_user_ids` and `transcriber` explicitly, with no unsafe
default for either.

## 13. Phase 10 — Channel adapter contract

**Spec refs:** spec-006 §21 (the abstract base only).

**Builds:** `conversation/adapters/base.py`, `conversation/adapters/__init__.py`
(`ConversationAdapter`).

**Exit criteria:** sync core, adapter owns any async boundary — the ABC
defines only `send()`; no `start()`/`run()`/`listen()` is forced onto every
channel.

## 14. Phase 11 — Telegram conversational adapter

**Spec refs:** spec-006 §21 (concrete), §23
(`interfaces/telegram/adapter.py`).

**Builds:** `interfaces/telegram/adapter.py` (`TelegramConversationAdapter`).
This phase decides how much of Phase 0's `config.py`/long-polling wiring is
reused versus rebuilt, and whether `/ping`/`/id` are retained as
lightweight diagnostic commands alongside full conversational routing or
retired in favor of `HelpTool` — an implementation-time call, not
prescribed here.

**Tests:** `tests/interfaces/telegram/test_adapter.py` against a fake Bot
API client — never a real Telegram server, per `CLAUDE.md`'s "Local
Windows development only."

**Exit criteria:** a text update builds a `ConversationMessage` with `text`
set, `audio` `None`; a voice update downloads the file via the fake client
and builds the reverse; `send()` calls the fake client's `send_message`
(and `send_voice` when `response.audio` is set) with the right chat id.

## 15. Phase 12 — Documentation, acceptance & hardening

**Spec refs:** spec-006 §27 (acceptance criteria, in full), §29, §30.

**Work:**

- Update `docs/architecture.md`'s layer diagram and "Dependency direction,
  summarized" section to add `conversation/` as layer 7 and renumber
  `interfaces/` to layer 8 — required before this Epic's implementation PR
  merges, per spec-006 §27.
- Update `docs/roadmap.md` so Epic 8 (not a separately-numbered later
  epic) is recorded as the one that delivered the Telegram interface.
- Full `pytest` run across every test package introduced in Phases 0–11,
  zero regressions.
- Walk spec-006 §27's acceptance-criteria list item by item and §29's
  five flagged judgment calls, for explicit reviewer sign-off (or a
  recorded decision) on each.

**Exit criteria:** every bullet in spec-006 §27 is either satisfied or has
a recorded, deliberate exception; no new external dependency exists outside
the adapter-facing edges spec-006 §27 names
(`python-telegram-bot` in `interfaces/telegram/`, an LLM SDK in
`integrations/llm/`, an STT/TTS SDK in `integrations/speech/`).

## 16. Environment variable registry (cumulative)

| Variable | Introduced | Required when |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Phase 0 | Always, to run the Telegram bot at all |
| `TELEGRAM_ALLOWED_USER_IDS` | Phase 0 | Always, to run the Telegram bot at all |
| `LLM_PROVIDER` | Phase 4 | Only if a `llm_client` is being wired (optional feature) |
| `DEEPSEEK_API_KEY` | Phase 4 | Only when `LLM_PROVIDER=deepseek` |

No LLM credential is required for Phases 0–3, 5–6, 8, 10–11, or for a
deployment that never sets `LLM_PROVIDER` — the deterministic path is the
default, matching spec-006 §25/§28.
