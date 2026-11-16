# Spec 005 - Decision Ledger

## 1. Status

Draft — revision 2. Revision 1 went through architecture review; this
revision applies every required change from that review (EvidenceItem
serialization ambiguity, the `record`/`analyze_and_record` split, strategy
metadata binding, the `memory/` dependency-direction documentation gap,
`RecordDecisionResult`'s shape, the ledger port's operation set, the
default JSONL path, construction-time file validation, concurrency/
durability scope, and the composition-root file layout). No production
code has been written or modified for this Epic; this document remains a
spec only (spec → architecture review → implementation → code review →
tests → commit → merge).

## 2. Context

Epics 1–6 are complete. `ApplicationService.analyze_asset(symbol)`
(`docs/specs/spec-004-application-service.md`) composes
`market_intelligence` → `indicators` → `committee` into one immutable,
**transient**, in-memory `AnalysisResult(market_snapshot, indicator_snapshot,
decision)` per call. Nothing downstream of that call currently persists
anything: `memory/` is an empty placeholder package
(`src/investment_committee/memory/__init__.py` has no content), and every
`AnalysisResult` is discarded the moment the caller's reference to it goes
out of scope.

`docs/architecture.md` already names the destination for this Epic: layer 4,
"Memory and Storage" (`memory/`), centered on a **Recommendation Ledger**
(the architecture doc's name; `docs/roadmap.md` Epic 7 also says
"Recommendation Ledger"; the Epic prompt that produced this document calls
it "Decision Ledger" and asks this spec to resolve the naming — see
§7). Product principle 2 already states the invariant this Epic exists to
enforce: *"`InvestmentDecision` is immutable historical truth. Once recorded
in the Recommendation Ledger (`memory/`), a decision is never recalculated
using newer market data... This is why `InvestmentDecision` and
`AgentReport` are frozen dataclasses with no update path."*

Spec-004 explicitly deferred two things to this Epic, on the record:

- **Version metadata** (spec-004, Unresolved Question 3): `AnalysisResult`
  intentionally carries no strategy/indicator/application version field,
  because "no meaningful versioning convention currently exists to reference
  truthfully" and because `docs/architecture.md` already assigns that
  responsibility to `memory/`. This spec defines it (§13).
- **The shape of the persisted record** (spec-004, Unresolved Question 2):
  "Whether Epic 7's ledger will persist `AnalysisResult` directly, or wrap it
  in its own richer record type... This spec only guarantees `AnalysisResult`
  carries everything Epic 7 needs verbatim; it does not decide Epic 7's own
  storage shape." This spec decides that shape (§10).

## 3. Problem

There is currently no way to answer, after the fact, "what did the system
recommend for AAPL on a given day, and why?" — every `AnalysisResult` is
gone as soon as the call that produced it returns. This blocks every later
Epic that reads history: Epic 8 (Paper Portfolio) needs a recorded decision
to simulate a position against; Epic 9 (Performance Evaluation) needs
recorded decisions to score against later market data; Epic 13 (Strategy
Comparison) needs a durable, strategy-attributed record to compare
deterministic, LLM, and hybrid committees fairly. None of that is buildable
without a durable, auditable, append-only record of every completed
analysis — which is what this Epic defines.

## 4. Goals

- Define a persisted, auditable record of one completed analysis pass:
  what was recommended, on what evidence, by which strategy and version, at
  what point in time (§10, §12, §13).
- Define identity and duplicate semantics precise enough to reason about
  retries and repeated analyses without ambiguity (§11).
- Define an explicit, minimal ledger port and exactly one durable adapter,
  plus an in-memory adapter for tests (§14, §15).
- Separate "run an analysis" from "durably record a result" into two
  explicit operations, so a caller can record an `AnalysisResult` it
  already obtained without paying for, or risking drift from, a second
  analysis (§17).
- Define exactly where persistence is triggered from, without changing
  `ApplicationService.analyze_asset()`'s existing public behavior (§17).
- Define the serialization schema precisely enough that a persisted record
  can be read back and re-validated — including every field's exact type —
  not just written (§16).
- Preserve append-only, immutable-history semantics at both the Python
  object level (frozen dataclasses) and the storage level (no update/delete
  operation exists anywhere in the port) (§11, §14, §19).
- Leave a clean seam for Epic 9's `EvaluationRecord` to reference a
  `DecisionRecord` without ever mutating it (§9, "Domain model").

## 5. Non-goals

Per the Epic prompt and `CLAUDE.md`, this Epic explicitly does not include:

- Implementing any of the code proposed below — this document is a spec
  only.
- Trade execution, broker integration, portfolio positions, or realized/
  unrealized P&L (`portfolio/`, `simulation/` — Epic 8; broker — Epic 14,
  optional and deferred).
- Performance evaluation or strategy comparison logic itself (Epic 9, Epic
  13) — only the seam `EvaluationRecord` will need is sketched (§9).
- Scheduling, Telegram, or any product-facing interface (Epic 10).
- An ORM, a web API, a background worker, an automatic migrations
  framework, or file locking / concurrent-writer coordination (§15).
- A new external dependency of any kind, including a database dependency —
  `sqlite3` is standard library and is discussed as a **deferred** future
  adapter (§15), not adopted now.
- Modifying `ApplicationService.analyze_asset()`'s existing signature,
  return type, or behavior; modifying any existing file in `src/` during
  this spec-only pass.
- Modifying `docs/architecture.md` or `docs/roadmap.md` **in this
  spec-revision pass** — §6 identifies a documentation correction that
  `docs/architecture.md` needs, but that correction is scoped to the Epic
  7 *implementation* PR, not this document.

## 6. Existing architecture

Relevant, already-established facts this spec builds on without repeating
their justification (see the cited specs for the reasoning):

- **Frozen-dataclass, self-validating value objects** are the house style
  everywhere: `domain/` (`Asset`, `PriceBar`, `MarketSnapshot`,
  `Recommendation`), `indicators/models.py` (`IndicatorSnapshot`),
  `committee/models.py` (`EvidenceItem`, `AgentReport`, `RiskItem`,
  `InvestmentDecision`), `application/models.py` (`AnalysisResult`). Every
  one of them: is `@dataclass(frozen=True)`; validates itself in
  `__post_init__`, raising `ValueError`; normalizes every sequence-typed
  field to a `tuple` via `object.__setattr__`; requires timezone-aware
  `datetime`s via a small `require_timezone_aware` helper **duplicated**
  per package rather than imported across a package boundary (spec-002,
  approved decision D4); uses `Decimal`, never `float`.
- **Port + adapter pattern, already precedented twice**:
  `market_intelligence/providers/base.py` defines `MarketDataProvider(ABC)`
  and `MarketDataError`; `market_intelligence/providers/yfinance_provider.py`
  is the one concrete adapter; `MarketIntelligenceService` takes the
  provider by constructor injection, defaulting to the concrete adapter
  (`provider: MarketDataProvider | None = None`). This Epic's `DecisionLedger`
  follows the identical shape (§14, §15).
- **Composition roots are separate from orchestration**:
  `application/factory.py`'s `build_default_application_service()` is the
  only place allowed to import concrete implementations for production
  wiring; `application/service.py`'s `ApplicationService` itself never
  constructs its own collaborators. This Epic's new factory function is
  added to that same file rather than a second composition root (§17, §22).
- **Typed exception hierarchies at each layer's boundary, chained with
  `raise ... from ...`**: `MarketDataError` → `MarketDataUnavailableError`,
  `IndicatorCalculationError` → `IndicatorCalculationFailedError`,
  `CommitteeEngineError` → `CommitteeDecisionFailedError`, all defined in
  `application/errors.py` and all preserving the original exception as
  `__cause__`. This Epic's ledger failures follow the same translation
  pattern at the point they cross from `memory/` into `application/` (§18).
- **`AnalysisResult` is deliberately thin and transient** (spec-004, "E3",
  "E9"): it exists to hand the exact `MarketSnapshot`, `IndicatorSnapshot`,
  and `InvestmentDecision` objects from one `analyze_asset()` call to
  whatever needs them next, without adding storage-shaped fields of its own.
  Per the Epic prompt's own instruction, this remains true — `AnalysisResult`
  is **not modified** by this spec.

### Dependency direction — a documented inconsistency this spec relies on, and requires be fixed

`docs/architecture.md` contains two descriptions of what `memory/` may
depend on, and they disagree:

- The prose "4. Memory and Storage" section says: *"every `InvestmentDecision`
  (with its `AgentReport`s) is written here once and never modified."*
  `InvestmentDecision` and `AgentReport` are `committee/` types — this
  sentence is only true if `memory/` depends on `committee/`.
- The "Dependency direction, summarized" section, however, lists
  `market_intelligence, memory → integrations` (via adapters only) as
  `memory/`'s *only* explicit dependency beyond the universal `→ domain`
  rule. It does **not** list `memory → committee` or `memory → indicators`.

This spec's design (§9, §10) requires the prose reading to be the correct
one — `DecisionRecord` embeds an `InvestmentDecision` (`committee`) and an
`IndicatorSnapshot` (`indicators`) directly, which is only architecturally
sound if `memory/` is permitted to depend on both. The **intended,
authoritative dependency direction for this Epic** is:

```
memory → domain
memory → indicators
memory → committee
```

`memory/` must **not** depend on: `application/`, the CLI
(`src/investment_committee/cli.py`, `__main__.py`), any other layer's
concrete persistence/integration adapters (e.g.
`market_intelligence/providers/*`), or `integrations/`. (`memory/` needs no
dependency on `market_intelligence/` at all — `MarketSnapshot` is already
a `domain/` type, reachable through the universal `→ domain` rule.)

**`docs/architecture.md`'s "Dependency direction, summarized" section must
be corrected to add `memory → committee, indicators` explicitly before this
Epic's implementation PR merges.** This spec does not make that edit now
(Non-goals, §5) — architecture review is asked to confirm this is the
correct resolution (rather than, say, the prose being wrong) as part of
approving this revision; the actual doc edit is implementation-PR scope,
tracked again in §25 and §28.

## 7. Terminology

To avoid the ambiguity `docs/roadmap.md`/`docs/architecture.md` currently
have between "Recommendation Ledger" and "Decision Ledger":

| Term | Meaning in this document |
|---|---|
| **Analysis** | One `ApplicationService.analyze_asset(symbol)` call: fetch → indicators → committee. Transient. |
| **`AnalysisResult`** | The existing, unmodified, transient in-memory output of one analysis (spec-004). |
| **Decision** | The committee's `InvestmentDecision` for one asset, produced by one analysis. Already immutable (spec-002). |
| **Decision Record** | The new, *durable* object this Epic defines: everything one analysis produced, plus ledger identity, recording time, and version metadata. Never mutated once appended. |
| **Decision Ledger** | The new *port* (interface) this Epic defines for appending and reading Decision Records. See §9 for why this name, not "Repository." |
| **Recording** | The act of turning a completed `AnalysisResult` into a `DecisionRecord` and appending it to a `DecisionLedger`, via `RecordDecisionUseCase.record()`. `analyze_and_record()` is the composed convenience that runs an analysis first and then records it — see §17. Neither is something `analyze_asset()` does implicitly. |
| **Evaluation Record** | *(Epic 9, not implemented here)* A future, separate record that references a `DecisionRecord`'s identity and attaches outcome data, without ever changing the `DecisionRecord` itself. Sketched in §9 only. |

This spec adopts **"Decision Ledger"** / **`DecisionRecord`** as the
project's terminology going forward (§9 has the justification). Renaming
`docs/roadmap.md`'s "Epic 7 — Recommendation Ledger" heading and
`docs/architecture.md`'s "Recommendation Ledger" pipeline-stage label to
match is a documentation follow-up for whoever begins implementation —
this spec does not touch either file (Non-goals; the one documentation
change this spec *does* require, the dependency-direction correction in
§6, is a distinct, narrower edit from this naming question).

## 8. Decision lifecycle

Two entry points exist into recording, both converging on the same
`record()` step (§17):

```
symbol: str
     │
     ▼
ApplicationService.analyze_asset(symbol)          <- UNCHANGED (spec-004)
     │
     ▼
AnalysisResult(market_snapshot, indicator_snapshot, decision)   <- transient, in-memory
     │
     ├─────────────────────────────────────────────┐
     │ (already have an AnalysisResult - e.g.        │ (no AnalysisResult yet)
     │  shown to a user and now being saved)          │
     ▼                                                ▼
RecordDecisionUseCase.record(analysis_result)   RecordDecisionUseCase.analyze_and_record(symbol)
     │  never calls analyze_asset() or                │  1. calls ApplicationService.analyze_asset(symbol)
     │  fetches market data                            │  2. calls record(analysis_result) - same path as left
     │                                                 │
     └──────────────────────┬──────────────────────────┘
                            ▼
     1. mints a DecisionRecordId and recorded_at
     2. attaches StrategyMetadata (bound at construction - see S13)
     3. constructs DecisionRecord(record_id, recorded_at, strategy_metadata,
           market_snapshot, indicator_snapshot, decision)
                            ▼
     DecisionLedger.append(record)                       <- NEW (this Epic)
                            ▼
     Durable ledger (JSON Lines file today; SQLite is a
     future adapter behind the same port — see §15)
                            ▼
     RecordDecisionResult(record, persisted_at)           <- returned to the caller
```

Once a `DecisionRecord` is appended, it is never updated or deleted through
any code path this Epic defines (§11, §19). A corrected or improved analysis
of the same asset is a **new** `analyze_asset()` call producing a **new**
`DecisionRecord` with a new identity — never an edit to a past one (product
principle 2).

### Future relationship to evaluation (not implemented here)

```
DecisionRecord (this Epic)
     │  record_id
     ▼
EvaluationRecord (Epic 9, NOT part of this Epic)
     - decision_record_id: DecisionRecordId   (reference, not embedding)
     - evaluated_at, realized_return, benchmark_comparison, ...
     - never written until real, later-dated market data exists
     - never causes any field on the referenced DecisionRecord to change
     │
     ▼
Strategy Comparison (Epic 13, NOT part of this Epic)
     - groups DecisionRecords by StrategyMetadata.strategy_name/version
     - compares EvaluationRecords across strategies over identical
       historical windows
```

## 9. Domain model

### `DecisionRecord`: wrap, copy, or normalize?

The Epic prompt asks this spec to evaluate, not assume, whether
`DecisionRecord` should wrap `AnalysisResult`, copy selected values from it,
hold a normalized persistence snapshot, or something else explicitly
justified. This is decided by a fact already established in §6, not by
style preference:

**`DecisionRecord` cannot hold a reference to `AnalysisResult`.**
The intended dependency direction (§6) is `application → memory`.
`AnalysisResult` is defined in `application/models.py`. If `memory/models.py`
imported `AnalysisResult` to use as a field type, `memory` would depend on
`application` — a reversed, cyclic dependency, since `application` already
depends on `memory` (per the layer diagram, once this Epic wires
`DecisionLedger` in). This is not a matter of taste; it is a hard
architectural constraint the "wraps `AnalysisResult`" option violates
structurally, independent of any other trade-off.

That leaves the two remaining options — "copies selected values" and
"normalized persistence representation" — which turn out to be closer than
they first appear. `DecisionRecord` is defined independently in `memory/`,
with its own `market_snapshot: MarketSnapshot`, `indicator_snapshot:
IndicatorSnapshot`, and `decision: InvestmentDecision` fields — structurally
identical in shape to `AnalysisResult`'s three fields, because `memory/`
*can* depend on `domain`, `indicators`, and `committee` (§6) — but populated
by copying the three object *references* out of an already-fully-formed
`AnalysisResult`, exactly the way `AnalysisResult` itself was built by
copying references out of three already-fully-formed objects (spec-004:
"never copies, never reconstructions from summary data"). No deep copy, no
re-serialization at this step — the same frozen objects the committee
produced are the ones embedded in the record. `DecisionRecord` then adds
the ledger-specific fields `AnalysisResult` structurally cannot have
(identity, recording time, version metadata) — see "Full embedding, not
normalization" below for why nothing is trimmed.

The translation itself (`AnalysisResult` → `DecisionRecord`) happens in
`RecordDecisionUseCase.record()` (§17), in `application/`, the one layer
allowed to import both `application.AnalysisResult` and `memory.DecisionRecord`
— exactly mirroring how `application/factory.py` is the one place allowed
to import concrete provider/analyst implementations for wiring purposes.

**`DecisionRecord` never inspects engine internals.** It has no field, no
method, and no constructor parameter that reads from `ApplicationService`,
`DeterministicCommitteeEngine`, or any other engine/service object — its
only inputs are the three already-formed value objects above, plus the
plain data described in §10. Where a decision was produced (which
`ApplicationService` instance, which committee wiring) is a composition-root
concern (§13, §17), never something `DecisionRecord` itself introspects or
derives.

### `DecisionRecord`

```python
# src/investment_committee/memory/models.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import NewType

from investment_committee.committee import InvestmentDecision
from investment_committee.domain import Asset, MarketSnapshot
from investment_committee.indicators import IndicatorSnapshot

from ._validation import require_timezone_aware

DecisionRecordId = NewType("DecisionRecordId", str)
"""A ledger-assigned identity, minted once per DecisionRecord (see spec-005 S11).

A NewType wrapping str, not a full value-object dataclass: this is a single
opaque identifier with no independent validation rules of its own beyond
"non-blank" (checked in DecisionRecord.__post_init__), so a full frozen
dataclass would be a speculative abstraction for one field.
"""


@dataclass(frozen=True)
class StrategyMetadata:
    """Which strategy, and which version of it, produced a DecisionRecord.

    All four fields are plain, hand-maintained strings bumped manually when
    the relevant ruleset changes - no semantic-versioning machinery, no
    auto-derivation from a git SHA (see spec-005 S13). This is the concrete,
    minimal versioning model spec-004 (Unresolved Question 3) deferred here.
    """

    strategy_name: str
    strategy_version: str
    indicator_engine_version: str
    application_version: str

    def __post_init__(self) -> None:
        for field_name in (
            "strategy_name",
            "strategy_version",
            "indicator_engine_version",
            "application_version",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"StrategyMetadata.{field_name} must not be blank")


@dataclass(frozen=True)
class DecisionRecord:
    """A durable, append-only record of one completed analysis.

    Embeds the exact MarketSnapshot, IndicatorSnapshot, and InvestmentDecision
    objects produced by one ApplicationService.analyze_asset() call, verbatim
    - see spec-005 S9 for why this is not a reference to AnalysisResult, and
    S9 ("Full embedding") for why nothing is trimmed. Never updated after
    construction; a corrected analysis is a new DecisionRecord, not an edit
    to this one (product principle 2, docs/architecture.md).
    """

    record_id: DecisionRecordId
    recorded_at: datetime
    strategy_metadata: StrategyMetadata
    market_snapshot: MarketSnapshot
    indicator_snapshot: IndicatorSnapshot
    decision: InvestmentDecision

    def __post_init__(self) -> None:
        if not str(self.record_id).strip():
            raise ValueError("DecisionRecord.record_id must not be blank")
        require_timezone_aware(self.recorded_at, "recorded_at")
        if not (
            self.market_snapshot.asset
            == self.indicator_snapshot.asset
            == self.decision.asset
        ):
            raise ValueError(
                "DecisionRecord requires market_snapshot, indicator_snapshot, "
                "and decision to share the same asset"
            )

    @property
    def asset(self) -> Asset:
        """The asset this record's analysis was performed for."""
        return self.decision.asset
```

`memory/_validation.py` duplicates the same one-function
`require_timezone_aware` helper `domain/_validation.py` and
`committee/_validation.py` already define, following spec-002's approved
decision D4 rather than importing across a package boundary for a
four-line helper.

### Full embedding, not normalization

`DecisionRecord` embeds the complete `MarketSnapshot` (including every
`PriceBar` — up to ~252 for a year of daily bars), the complete
`IndicatorSnapshot`, and the complete `InvestmentDecision` (including all
three `AgentReport`s with their full `EvidenceItem` lists). This directly
answers required domain question 6: no normalized/trimmed persistence
representation is used. Reasoning:

- The Epic's own stated requirement is "reconstruct or audit later"
  *without* re-running the analysis — spec-004 was explicit that returning
  the richer `AnalysisResult` (rather than a bare `InvestmentDecision`) from
  `analyze_asset()` exists *specifically* so this Epic would not need to
  reconstruct historical context from a trimmed summary. Trimming it back
  down here would waste that design work and reintroduce the exact problem
  spec-004 solved.
- Every one of the embedded objects is already a frozen, self-validating
  dataclass with well-defined serialization needs (§16) — there is no
  "extra" data to strip; every field already has a documented reason to
  exist (spec-001, spec-002, spec-004).
- The storage cost is bounded and known: one asset's one year of daily bars
  is small (kilobytes as JSON), and this is a personal, local, single-user
  system, not a multi-tenant service — `CLAUDE.md`'s "avoid overengineering"
  cuts against optimizing storage for a scale this project does not have.

The trade-off — one `DecisionRecord` per asset stores its own copy of
overlapping price history if the same asset is analyzed repeatedly in a
short window — is real but explicitly deferred; see §26 ("Alternatives
considered") and §28 ("Unresolved questions").

### The `DecisionLedger` port: naming (required domain question 9)

Three candidate names were evaluated: `DecisionLedger`, `DecisionRepository`,
`DecisionRecordRepository`.

**Chosen: `DecisionLedger`.**

- It matches the vocabulary `docs/architecture.md` and `docs/roadmap.md`
  already use for this exact concept ("Recommendation Ledger" as a pipeline
  stage and as the centerpiece of the `memory/` layer description) — adopting
  it here resolves the Epic prompt's own "likely to become Decision Ledger"
  note (spec-004) rather than introducing a third name.
- "Ledger" specifically connotes an append-only, chronological record of
  events — which is exactly the behavior this Epic requires (§11, §19) and
  which "Repository," a generic DDD term that conventionally supports
  update and delete, does not inherently promise. Naming the port
  `DecisionLedger` makes the append-only contract legible from the type
  name itself, not just from a docstring.
- `DecisionRecordRepository` is the most literal option but adds a
  redundant word (`Record`) without adding clarity over `DecisionLedger`.

## 10. DecisionRecord design

See §9 for the full class definition and the wrap-vs-copy-vs-normalize
decision. Summary of the design:

- Six fields: `record_id`, `recorded_at`, `strategy_metadata`,
  `market_snapshot`, `indicator_snapshot`, `decision`. No more, no fewer —
  every field maps directly to one of the fifteen "what data must be
  preserved" items in the Epic prompt (asset/recommendation/confidence/
  agent reports/chair result are all reachable through `decision`;
  market/indicator snapshots are direct fields; strategy identity/version/
  indicator version/application version are grouped in `strategy_metadata`;
  analysis time is `decision.generated_at`; recording time is
  `recorded_at`).
- `asset` is a computed property (`self.decision.asset`), matching the
  existing pattern in `AnalysisResult.asset` and `IndicatorSnapshot.asset` —
  never a seventh stored field that could drift from `decision.asset`.
- Frozen, self-validating, `Decimal`-only (inherited transitively from its
  embedded objects, which already enforce this) — no new numeric handling
  is introduced by `DecisionRecord` itself.
- Holds no reference to any engine, service, or adapter object, and no
  logic for validating its own `strategy_metadata` against anything else —
  see §9 and §13.

## 11. Identity and idempotency

### Identity (required domain question 2)

Four options were evaluated:

| Option | Verdict |
|---|---|
| UUID generated by the application | **Chosen** |
| Deterministic content-derived ID (hash of the record's contents) | Rejected |
| Repository-generated identity | Rejected |
| Database-generated identity (e.g. autoincrement) | Rejected for this Epic |

**Chosen: a UUID4 minted by the application layer** (`RecordDecisionUseCase`,
§17), *before* `DecisionLedger.append()` is called — not by the ledger or
any storage backend.

- **Content-derived ID rejected**: it would require a canonical
  serialization of a structure containing nested `Decimal`s, tuples of
  frozen dataclasses, and enums, stable across Python versions and library
  updates — fragile against schema evolution (§16) for no real benefit. It
  would also conflate "same content" with "same identity," when required
  domain question 8 explicitly wants those treated as *different* concerns
  (two independent analyses that happen to agree are not duplicates; see
  below).
- **Repository-generated identity rejected**: the JSON Lines adapter chosen
  for this Epic (§15) has no natural autoincrement primitive — computing
  "the next ID" would require scanning the file, which is both slow and
  unsafe under concurrent writers (not a concern for a single local process
  today, §15's "Concurrency and durability scope," but a needless
  constraint to bake into the port's contract). Assigning identity in the
  application layer keeps identity assignment identical regardless of which
  adapter is behind the port — in-memory, JSON Lines, or a future SQLite
  adapter all receive an already-identified record.
- **Database-generated identity rejected for this Epic** specifically
  because no database is being introduced in this Epic (§15, §5). If a
  future SQLite adapter is added, it should still accept an
  application-assigned `DecisionRecordId` (e.g. as a `TEXT PRIMARY KEY`)
  rather than switching identity assignment to the database, so that
  swapping adapters never changes what identity means to a caller.

This mirrors the existing convention that every value object in this
codebase is fully constructed by its caller with no hidden field
population (no ORM-style deferred ID assignment anywhere else in the
codebase) — identity is just another required constructor argument, minted
by an injected, testable `IdFactory` (§17) exactly like every other
non-deterministic input (`Clock`) in this codebase is injected rather than
hardcoded.

### Duplicate semantics (required domain question 8)

Three distinct scenarios, given explicit, different treatment:

1. **Two independent analyses of the same asset, seconds (or moments)
   apart.** Each `record()`/`analyze_and_record()` call mints its own
   `DecisionRecordId` and `recorded_at`. These are **not** duplicates — they
   are two legitimate, separately-recorded decisions. This is intentional:
   tracking how a recommendation changes over repeated real analyses is the
   entire point of the ledger.
2. **The same `DecisionRecord` object appended twice** (e.g. a caller
   catches a `LedgerWriteError` from step 1 of a retry and calls
   `ledger.append(record)` again with the *same* `record_id`). The second
   `append()` for an already-present `record_id` **raises
   `DuplicateDecisionError`** — it neither silently overwrites (which would
   violate append-only) nor silently no-ops (which would hide a caller bug
   or a genuine double-send). The caller decides what to do with the error.
   The JSON Lines adapter additionally detects this case for record_ids
   already present in the file *before* the current process's index was
   even built — see §15's construction-time validation.
3. **"Same content, different identity"** (two `DecisionRecord`s with
   different `record_id`s and different `recorded_at`s, but byte-identical
   `market_snapshot`/`indicator_snapshot`/`decision` payloads — e.g. the
   market genuinely hadn't moved between two runs). This is **not** treated
   as a duplicate. Content-based deduplication is out of scope: it would
   require canonical hashing of the same fragile nested structure discussed
   above, and two independent analyses that happen to agree are still two
   separate audit events, not an error condition.

Idempotency is therefore **identity-based, not content-based**, and is
enforced by the ledger (case 2), not by the application layer pre-checking
existence before every append — see §19 for what this means when a write's
success is genuinely unknown (e.g. a crash between write and confirmation),
and §15 for the scope of this guarantee across processes.

## 12. Timestamp semantics

Required domain question 3 asks this spec to avoid ambiguous fields named
only `timestamp` and to distinguish market-data, analysis, decision-creation,
and persistence timestamps. Every timestamp in this Epic already has, or is
given, an unambiguous name and owner:

| Timestamp | Field | Owner / when it is set | Pre-existing? |
|---|---|---|---|
| Market data as-of time | `market_snapshot.generated_at` | `market_intelligence`, at fetch time | Yes (Epic 2) |
| Indicator calculation time | `indicator_snapshot.generated_at` | `indicators`, at calculation time | Yes (Epic 3) |
| Analysis / decision time | `decision.generated_at` | `committee`'s injected `Clock`, called once per `analyze_asset()` run | Yes (Epic 5) |
| Recording time | `DecisionRecord.recorded_at` | `RecordDecisionUseCase`'s injected `Clock`, called once per `record()` call (whether reached directly or via `analyze_and_record()`) | **New (this Epic)** |
| Persistence confirmation time | `RecordDecisionResult.persisted_at` | `RecordDecisionUseCase`'s injected `Clock`, called once immediately after `DecisionLedger.append()` returns successfully | **New (this Epic)** |

Two design choices here are worth calling out explicitly:

- **`recorded_at` lives on `DecisionRecord`; `persisted_at` does not.**
  `DecisionRecord` is constructed *before* `append()` is called (it has to
  be — `append()`'s parameter is a `DecisionRecord`), so the instant the
  storage write actually completes is knowable only *after* the object
  already exists. Modeling `persisted_at` as a field on the frozen
  `DecisionRecord` itself would require either mutating it after
  construction (violates the frozen/immutable design used everywhere in
  this codebase) or leaving it `None` until some later step sets it (a
  second construction step, or a mutable field, both of which are exactly
  the kind of "hidden mutable state" `CLAUDE.md` asks this Epic to avoid).
  Instead, "was it persisted, and when" is answered by the **return value**
  of `record()`/`analyze_and_record()` (`RecordDecisionResult.persisted_at`,
  a required field on a frozen dataclass — see §17 — present only on
  success) versus an **exception** (§18) on failure — a plain
  function-result/exception distinction, not a nullable field a caller could
  forget to check. `persisted_at` is always timezone-aware UTC (the same
  injected `Clock` used for `recorded_at`), and is constructed **only**
  after `DecisionLedger.append()` has returned without raising — never
  speculatively, never before the write is confirmed.
- **Now that `record(analysis_result)` exists as a first-class entry point
  (§17), `recorded_at` and `decision.generated_at` can genuinely diverge**
  — e.g. a caller runs `analyze_asset()`, shows the result to a user, and
  only calls `record()` some time later once the user confirms. This is the
  concrete scenario `recorded_at` exists for, beyond the retry case
  described in revision 1 of this spec. Code that needs "the instant the
  committee actually decided" (e.g. Epic 9's look-ahead-bias check) should
  read `decision.generated_at`, never `recorded_at`.

No cross-timestamp ordering is enforced in `DecisionRecord.__post_init__`
(e.g. `recorded_at >= decision.generated_at` is not checked) — this follows
the same precedent as spec-002's unresolved question 2 (no ordering enforced
between `InvestmentDecision.generated_at` and its reports' timestamps),
deferred as a possible future invariant rather than added speculatively now.

## 13. Version metadata

Spec-004 deferred this deliberately (its Unresolved Question 3) rather than
inventing a placeholder value with no real meaning. This spec defines a
concrete, minimal, and honest version model — `StrategyMetadata` (§9) — with
four hand-maintained string fields:

| Field | Meaning | Where it comes from |
|---|---|---|
| `strategy_name` | Which committee configuration produced this decision, e.g. `"deterministic_committee"` | A literal, chosen when the composition root wires a strategy (§13, "Production binding guarantee") |
| `strategy_version` | Version of that strategy's rule tables/thresholds (spec-003's Rule Strength Table, decision matrix, etc.) | A literal, bumped by hand whenever spec-003's rules change |
| `indicator_engine_version` | Version of `IndicatorEngine`'s calculation logic | A literal, bumped by hand whenever spec-001's calculations change |
| `application_version` | The overall `investment_committee` package version | `investment_committee.__version__` (already exists, `src/investment_committee/__init__.py`) |

### Why no semantic versioning, hashing, or git-SHA derivation

Auto-deriving a version from a git commit SHA or introducing full semantic
versioning (`MAJOR.MINOR.PATCH` with defined compatibility rules) was
considered and rejected as premature: nothing in `committee/` or
`indicators/` today has any versioning convention to build on (spec-004's
own finding), and inventing one under this Epic — which is about the ledger,
not about `committee/`'s or `indicators/`'s internal change-tracking — would
be scope creep beyond what "concrete and minimal" requires. A single
hand-maintained string per component (starting at `"1"`), bumped whenever
that component's rules materially change, is honest about what is actually
known today and costs nothing to maintain at this project's current scale.

### Production binding guarantee — how `StrategyMetadata` stays trustworthy

`StrategyMetadata` remains a plain, explicit, immutable value object (§9) —
this spec does **not** derive it by introspecting `ApplicationService` or
`DeterministicCommitteeEngine` internals (no reflection over which analyst
classes are wired in, no reading private attributes). Deriving metadata
that way would couple `memory/` to `committee/`'s and `application/`'s
internal composition in a much deeper way than the explicit, data-only
dependency §6 already grants, and would make `StrategyMetadata` a moving
target that changes shape whenever an unrelated internal refactor happens.

Instead, correctness is guaranteed procedurally, at exactly one point:

- **The sanctioned composition root** — `build_default_record_decision_use_case()`
  in `application/factory.py` (§17, §22) — is the only place responsible
  for constructing a matching `(ApplicationService, StrategyMetadata)` pair
  for a given strategy and handing both to one `RecordDecisionUseCase`. For
  the deterministic baseline, this factory function always pairs
  `build_default_application_service()`'s deterministic wiring with
  `memory.versioning.DETERMINISTIC_STRATEGY_METADATA` — the same function
  that assembles the engine also selects the metadata that describes it, so
  there is exactly one place a mismatch could be introduced, and it is the
  one place already responsible for getting the wiring right (mirroring how
  `build_default_application_service()` is already the one place trusted to
  wire matching analysts and chair together, per spec-004).
- **`RecordDecisionUseCase`'s constructor remains open to direct
  construction** for tests and advanced dependency injection (e.g. a future
  hybrid- or LLM-committee wiring that needs its own `StrategyMetadata`) —
  this spec does not add a runtime check preventing a caller from passing a
  mismatched pair directly, because there is no reliable, non-speculative
  way to verify "this `StrategyMetadata` accurately describes this
  `ApplicationService`" from the outside without the reflection this
  section already rejects. The guarantee is therefore a **documented
  contract on the sanctioned factory**, not a runtime invariant enforced by
  `RecordDecisionUseCase.__init__` itself. Any caller bypassing the factory
  to construct `RecordDecisionUseCase` directly is responsible for supplying
  a `StrategyMetadata` that actually matches the `ApplicationService` it
  is paired with.
- **Adapters (`DecisionLedger` implementations) never validate strategy
  identity.** `InMemoryDecisionLedger` and `JsonLinesDecisionLedger` treat
  `record.strategy_metadata` as opaque data to store and return, exactly
  like every other field on `DecisionRecord` — they perform no comparison,
  no cross-check, and no lookup against any "known strategies" registry.
  Storage is not the place strategy-attribution correctness is enforced;
  the composition root is (§17).

### Where the version constants live — an explicit, flagged compromise

The cleanest long-term home for `strategy_version` and
`indicator_engine_version` is inside `committee/__init__.py` and
`indicators/__init__.py` respectively (a `STRATEGY_VERSION` /
`INDICATOR_ENGINE_VERSION` module constant, next to the code it describes).
**This spec does not do that**, because the Epic prompt instructs "do not
modify existing source files" for this pass. Instead, this spec proposes a
new module owned entirely by `memory/`:

```python
# src/investment_committee/memory/versioning.py
from __future__ import annotations

import investment_committee
from .models import StrategyMetadata

DETERMINISTIC_STRATEGY_METADATA = StrategyMetadata(
    strategy_name="deterministic_committee",
    strategy_version="1",
    indicator_engine_version="1",
    application_version=investment_committee.__version__,
)
```

This remains flagged as a **design decision requiring approval** (§26, §28):
when implementation begins and modifying `committee/__init__.py` /
`indicators/__init__.py` is in scope, the version constants should likely
move into those packages directly, with `memory/versioning.py` importing
them rather than hardcoding literals a layer away from the code they
describe. Landing them in `memory/` first is a deliberate, temporary
concession to this spec-only pass's constraints, not a permanent
recommendation. (This is unrelated to, and not resolved by, the
composition-root binding guarantee above — that guarantee concerns *which
`ApplicationService` a `StrategyMetadata` is paired with*, not *where the
`StrategyMetadata` literals are declared*.)

## 14. Ledger port

```python
# src/investment_committee/memory/ledgers/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from investment_committee.domain import Asset

from ..models import DecisionRecord, DecisionRecordId


class DecisionLedger(ABC):
    """Durable, append-only store of DecisionRecords.

    No update or delete operation exists on this interface, anywhere - a
    corrected or improved analysis is recorded as a new DecisionRecord, per
    product principle 2 (docs/architecture.md). See spec-005 SS 11, 19.
    """

    @abstractmethod
    def append(self, record: DecisionRecord) -> None:
        """Durably persist `record`.

        Raises DuplicateDecisionError if a record with the same record_id
        already exists. Raises LedgerWriteError for any other failure to
        persist (e.g. I/O failure). Never partially writes a record - see
        spec-005 S19.
        """
        raise NotImplementedError

    @abstractmethod
    def get(self, record_id: DecisionRecordId) -> DecisionRecord:
        """Return the DecisionRecord with `record_id`.

        Raises DecisionNotFoundError if no such record exists.
        """
        raise NotImplementedError

    @abstractmethod
    def find_by_asset(self, asset: Asset) -> Sequence[DecisionRecord]:
        """Return every DecisionRecord for `asset`, in append order.

        Matching is by Asset equality (`record.asset == asset`) - Asset is
        already a frozen dataclass that normalizes its own ticker in
        __post_init__ (domain/assets.py), so this method performs no
        additional ticker normalization of its own. Returns an empty
        sequence, never raises, if no record matches.
        """
        raise NotImplementedError

    @abstractmethod
    def list_all(self) -> Sequence[DecisionRecord]:
        """Return every DecisionRecord in the ledger, in append order.

        Returns an empty sequence, never raises, if the ledger is empty.
        """
        raise NotImplementedError
```

### Operations (required domain question 10): why these four and no more

`append`, `get`, `find_by_asset`, `list_all` — the smallest set that
supports every named consumer of this Epic and the roadmap Epics that
follow it, with nothing added speculatively:

- No `update`/`delete` — append-only is the whole point (§11, §19).
- **No `exists()`.** Revision 1 of this spec included it because the Epic
  prompt listed it as a candidate operation, but no concrete caller was
  ever named. Architecture review flagged this as inconsistent with the
  same minimalism principle already applied to `count()` and pagination
  (both rejected below for the identical reason). It is removed here: a
  caller that needs "does this id exist" can call `get()` and catch
  `DecisionNotFoundError`; that costs one exception class already defined
  for `get()`'s own contract, not a second method.
- No `count()` — derivable by callers as `len(list_all())` when actually
  needed; not worth a dedicated method until a real caller needs it at a
  scale where materializing the full list is wasteful (not true yet, for a
  personal local system).
- No query DSL / filter object — `find_by_asset` is the one filter this
  Epic's known consumers (Epic 8, Epic 9) actually need (both operate
  per-asset); a generic filter parameter would be speculative given no
  second filter dimension has a concrete use case yet (flagged in §28).
- No pagination parameters on `list_all`/`find_by_asset` — deferred until
  real data volume makes returning everything impractical; adding pagination
  later is an additive, non-breaking change to this same port.

### Return types, ordering, and not-found/duplicate behavior — precise contract

| Method | Return | Ordering | Not-found behavior | Duplicate behavior |
|---|---|---|---|---|
| `append(record)` | `None` | n/a | n/a | Raises `DuplicateDecisionError` if `record.record_id` already exists in the ledger |
| `get(record_id)` | `DecisionRecord` (exact stored instance for in-memory; a reconstructed, revalidated instance for JSON Lines — see §16) | n/a | Raises `DecisionNotFoundError` | n/a |
| `find_by_asset(asset)` | `Sequence[DecisionRecord]` | Append order (oldest first) | Returns `()` (empty tuple), never raises | n/a |
| `list_all()` | `Sequence[DecisionRecord]` | Append order (oldest first) | Returns `()` (empty tuple) if the ledger has never been appended to, never raises | n/a |

### Ledger error hierarchy

```python
# src/investment_committee/memory/errors.py
from __future__ import annotations


class LedgerError(Exception):
    """Base class for every error a DecisionLedger operation can raise."""


class DuplicateDecisionError(LedgerError):
    """Raised by append() when a record with the same record_id already exists.

    Also raised by JsonLinesDecisionLedger's constructor if the ledger file
    itself already contains two lines with the same record_id (S15, S19).
    """


class DecisionNotFoundError(LedgerError):
    """Raised by get() when no record with the given record_id exists."""


class LedgerWriteError(LedgerError):
    """Raised when append() fails for a reason other than duplication (e.g. an I/O failure)."""


class LedgerReadError(LedgerError):
    """Raised when get()/find_by_asset()/list_all() fail for a reason other than not-found."""


class DecisionRecordDeserializationError(LedgerReadError):
    """Raised when a persisted record cannot be reconstructed: malformed JSON, a missing
    or unrecognized required field (including an EvidenceItem's value_type - S16), an
    unrecognized schema_version, or a value that fails a domain dataclass's own
    __post_init__ validation on read. Also raised by JsonLinesDecisionLedger's
    constructor - see S15's construction-time (eager) validation.
    """
```

This mirrors the existing pattern exactly: one base class per layer's
boundary (`MarketDataError`, `IndicatorCalculationError`,
`CommitteeEngineError`, `ApplicationServiceError`), with narrow, named
subclasses for each distinguishable failure mode.

## 15. Persistence adapter

### What storage backend belongs in Epic 7 (required domain question 11)

Four options were evaluated against "the smallest durable design that
supports the roadmap without introducing unnecessary infrastructure":

| Option | Verdict |
|---|---|
| In-memory only | Needed, but not sufficient alone — not durable |
| JSON Lines file | **Chosen as the one durable adapter for this Epic** |
| SQLite | Deliberately deferred — see below |
| Both a port and one durable adapter | **Chosen** — this is the shape, not a fourth option |

**Chosen: an in-memory adapter (for tests and short-lived scripts) plus one
JSON Lines file adapter (the durable adapter), both behind the
`DecisionLedger` port defined in §14.**

```python
# src/investment_committee/memory/ledgers/in_memory.py
from __future__ import annotations

from typing import Sequence

from investment_committee.domain import Asset

from ..errors import DecisionNotFoundError, DuplicateDecisionError
from ..models import DecisionRecord, DecisionRecordId
from .base import DecisionLedger


class InMemoryDecisionLedger(DecisionLedger):
    """Process-local, non-durable DecisionLedger. For tests and short scripts only."""

    def __init__(self) -> None:
        self._records: dict[DecisionRecordId, DecisionRecord] = {}

    def append(self, record: DecisionRecord) -> None:
        if record.record_id in self._records:
            raise DuplicateDecisionError(f"record_id already exists: {record.record_id}")
        self._records[record.record_id] = record

    def get(self, record_id: DecisionRecordId) -> DecisionRecord:
        try:
            return self._records[record_id]
        except KeyError as exc:
            raise DecisionNotFoundError(f"no such record_id: {record_id}") from exc

    def find_by_asset(self, asset: Asset) -> Sequence[DecisionRecord]:
        return tuple(r for r in self._records.values() if r.asset == asset)

    def list_all(self) -> Sequence[DecisionRecord]:
        return tuple(self._records.values())
```

```python
# src/investment_committee/memory/ledgers/jsonl.py
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from investment_committee.domain import Asset

from ..errors import (
    DecisionNotFoundError,
    DecisionRecordDeserializationError,
    DuplicateDecisionError,
    LedgerWriteError,
)
from ..models import DecisionRecord, DecisionRecordId
from ..serialization import from_persisted_dict, to_persisted_dict
from .base import DecisionLedger

DEFAULT_LEDGER_PATH: Path = Path("data") / "ledger" / "decisions.jsonl"
"""Default location for the durable ledger file, relative to the process's
working directory - matches the existing data/raw/, data/processed/,
data/reports/ convention (see spec-005 S21 for the required .gitignore
follow-up). Importing this module performs no I/O; the constant is just a
Path value.
"""


class JsonLinesDecisionLedger(DecisionLedger):
    """Durable DecisionLedger backed by a single append-only JSON Lines file.

    One JSON object per line, one line per DecisionRecord. See spec-005
    S15 ("Construction-time validation") and S15 ("Concurrency and
    durability scope") for the exact guarantees this adapter makes and
    does not make.
    """

    def __init__(self, path: Path = DEFAULT_LEDGER_PATH) -> None:
        """Prepare the ledger at `path`, eagerly validating it if it exists.

        If `path` exists, every non-empty line is read and parsed
        immediately, in order:
          1. `json.loads()` failure on any line -> DecisionRecordDeserializationError.
          2. An unrecognized `schema_version` on any line -> DecisionRecordDeserializationError.
          3. A `record_id` that repeats a `record_id` already seen earlier
             in the same file -> DuplicateDecisionError.
          4. A value that fails a domain dataclass's own __post_init__
             validation once reconstructed (including an EvidenceItem with
             an unrecognized value_type, S16) -> DecisionRecordDeserializationError.
        Any of these raises directly from __init__ - the object is never
        left partially constructed and usable. On success, an in-memory
        record_id index is built from every line, used by append() and
        get() for the remainder of this instance's lifetime.

        If `path` does not exist, construction succeeds immediately with an
        empty index. Neither `path`'s parent directories nor `path` itself
        are created at this point - see "Lazy file creation" below.
        Construction never writes to `path`, and never performs network I/O.
        """
        raise NotImplementedError
```

(The full method bodies — `append`, `get`, `find_by_asset`, `list_all` — are
implementation, not spec; the docstrings above and §14's contract table
specify the behavior they must satisfy.)

### Lazy file creation (required domain question 11 / decision 7)

`path` and any missing parent directories (e.g. `data/ledger/`) are created
**lazily, on the first successful `append()` call** — never during
`__init__`, and never merely from importing `memory.ledgers.jsonl`.
Constructing a `JsonLinesDecisionLedger` for a path that does not yet exist
is a normal, successful, side-effect-free operation (an empty ledger); the
first `append()` is responsible for calling the equivalent of
`path.parent.mkdir(parents=True, exist_ok=True)` before opening `path` for
appending. `path` may be overridden by any caller via the constructor
parameter (dependency injection, matching every other adapter in this
codebase — e.g. `MarketIntelligenceService(provider: MarketDataProvider | None = None)`);
`DEFAULT_LEDGER_PATH` is only the value the sanctioned composition root
(§17) uses when no override is supplied.

### Construction-time (eager) validation — why, and what it means for corruption

Revision 1 of this spec tolerated one truncated trailing line at read time,
reasoning that a crash mid-write could produce one. Architecture review
asked this to be replaced with a stricter, more honest policy, and this
revision does so: **construction validates every non-empty line in the
file, with no special-casing for the last line.** A file left corrupted by
an interrupted write — whether the corruption is on the last line or any
other — causes `JsonLinesDecisionLedger.__init__` to raise
(`DecisionRecordDeserializationError` or `DuplicateDecisionError`, per the
docstring above) rather than silently working around it. Recovering from
that state (e.g. manually deleting or fixing the offending line) is a
manual, out-of-band operation for this Epic — not automated, and not
claimed to be handled gracefully; see "Concurrency and durability scope"
below and §28. This is a deliberate simplification, not an oversight: it
removes an entire category of "was this a real corruption or an in-flight
write" ambiguity from the adapter's logic, at the cost of a hard failure
(rather than a silent skip) on the rare occasion a write is interrupted —
an acceptable trade for a personal, local, single-writer tool where the
user can open and inspect the file directly.

### Why JSON Lines over SQLite, for this Epic specifically

- **Adds no dependency, including no database dependency** — `json` and
  `pathlib` are both standard library. This directly satisfies the Epic
  prompt's explicit "do not add a database dependency" instruction for this
  pass, without pre-empting a SQLite adapter later (see below).
- **Append-only by physical construction, not just by convention.** Opening
  a file in append mode and writing one line per record makes "rewrite an
  existing row" structurally awkward to do by accident — the storage format
  itself reinforces the invariant §11/§19 require, rather than relying
  solely on application-level discipline the way an UPDATE-capable SQL table
  would.
- **Corruption is isolated per line**, which is a concrete, checkable
  advantage over a single JSON array in one file: a corrupted byte in one
  line breaks only that line's `json.loads()` call, not the entire file's
  parse. A single top-level JSON array would need the *entire* file to
  parse successfully before any record could be read back.
- **Matches this project's current scale and constraints**: `CLAUDE.md`
  says "Local Windows development only," this is a personal, single-user
  system, and `docs/architecture.md`'s own `memory/` description already
  anticipates this staging — *"SQLite backs this layer once its schema is
  stable; for now it defines the interfaces/data shapes other layers read
  and write."* This Epic is precisely the one that stabilizes that schema;
  landing SQLite in the same Epic that is still actively deciding the shape
  it would need to store would be sequencing the two together prematurely.

### SQLite is deferred, not rejected

`sqlite3` is standard library and `.gitignore` already anticipates it
(`*.sqlite3`, `*.db` are already ignored) — nothing about this spec argues
SQLite is architecturally wrong for this project. It is deferred *from this
specific Epic* because:

1. `CLAUDE.md`'s own phasing says SQLite is introduced "once the domain
   logic it will store is stable" — this Epic is what stabilizes that
   domain logic (`DecisionRecord`'s shape, §10); a SQLite adapter is
   naturally the *next* small increment once this shape has been exercised,
   not a same-Epic requirement.
2. The `DecisionLedger` port (§14) is designed so a future
   `SqliteDecisionLedger` can implement it with **zero changes** anywhere
   else in the codebase — `RecordDecisionUseCase`, `application/factory.py`
   wiring, and every caller of the port depend only on the abstract
   `DecisionLedger`, never on `JsonLinesDecisionLedger` concretely (mirroring
   how `ApplicationService` depends on `MarketIntelligenceService`, not
   `YFinanceProvider`, directly). Adding SQLite later is additive, not a
   redesign. A future SQLite adapter would also be the natural place to add
   real concurrency guarantees (transactions, `UNIQUE` constraints) that
   this Epic's JSON Lines adapter deliberately does not attempt (see below).
3. Introducing SQL schema, connection handling, and (eventually) a
   migration story in the same Epic that is still finalizing the record
   shape would be the "unnecessary infrastructure" the Epic prompt's
   question 11 explicitly asks this spec to avoid.

### Concurrency and durability scope

Stated precisely, so no reader infers a stronger guarantee than what this
Epic actually provides:

- **`JsonLinesDecisionLedger` guarantees correct behavior only for one
  process, running one long-lived `JsonLinesDecisionLedger` instance, with
  one writer at a time.** This matches the actual usage this Epic supports
  (a CLI/future-Telegram command invoking the composition root once per
  invocation) and is not a claim about safety under concurrent access.
- **No file locking is added in this Epic** (explicit non-goal, §5). If two
  processes (e.g. two CLI invocations run back-to-back but overlapping, or
  two independently-constructed `JsonLinesDecisionLedger` instances in the
  same process) write to the same file concurrently, neither this adapter
  nor the `DecisionLedger` port makes any guarantee about the result —
  interleaved writes, a lost update, or an undetected duplicate `record_id`
  are all possible.
- **Duplicate-`record_id` detection is scoped to one ledger instance's
  lifetime.** The in-memory index built at construction time (or updated by
  a prior `append()` within the same instance) is what `append()` checks —
  it has no way to see a `record_id` written by a *different* instance or
  process after this instance was constructed. §11's duplicate-prevention
  guarantee (case 2) therefore holds within a single `JsonLinesDecisionLedger`
  instance, not across concurrently-constructed instances or processes
  sharing a file.
- **This spec does not claim full crash-proof atomicity.** `append()` is
  specified to perform a single-process, single-writer append of one
  complete line — not a broader "atomic write" guarantee that would need to
  hold under concurrent writers or arbitrary OS-level failure modes. A
  process crash during a write can leave the file in a state
  `JsonLinesDecisionLedger.__init__` refuses to open (see "Construction-time
  validation" above) until a human repairs it — an accepted, documented
  limitation for this Epic, not a guarantee this design meets.
- **Windows-specific flush/durability behavior must be verified during
  implementation, not assumed by this spec.** `CLAUDE.md`'s hard constraint
  is "Local Windows development only." Whether a plain `write()` +
  `flush()` is sufficient to survive a process crash (versus needing an
  explicit `os.fsync()`, and how NTFS buffers interact with Python's
  buffered text-mode file objects on Windows specifically) is an
  implementation-time question this spec flags but does not answer — see
  §28.

## 16. Serialization schema

### Format

One JSON object per line (`.jsonl`), UTF-8, each line terminated by `\n`,
written with `schema_version: 1` at the top level of every record.

### `EvidenceItem` serialization — explicit `value_type` discriminator

`AgentReport.evidence` entries are `EvidenceItem(key: str, value: Decimal |
str, interpretation: str)` (`committee/models.py`, unmodified by this
Epic). Serializing `value` as `str(value)` for `Decimal` and leaving `str`
values unchanged (as revision 1 of this spec did) produces **identical JSON
strings on the wire for both cases** — there is no way to tell, from the
JSON alone, whether an evidence value `"151.02"` was originally a `Decimal`
or a categorical `str` that merely looks numeric. This was flagged by
architecture review as a real round-trip correctness bug (a `Decimal` read
back as `str` breaks arithmetic/comparison in any downstream consumer, with
no exception raised) and is fixed as follows.

**Every serialized `EvidenceItem` carries an explicit `value_type`
discriminator alongside `value`.** The only two supported values for this
Epic are `"decimal"` and `"string"`:

```json
{
  "key": "close_price",
  "value_type": "decimal",
  "value": "151.02",
  "interpretation": "Latest close is 151.02"
}
```

```json
{
  "key": "signal",
  "value_type": "string",
  "value": "golden cross",
  "interpretation": "A golden cross pattern was observed"
}
```

(The Epic prompt's own illustrative examples used a field named `name`;
this spec uses `key`, matching `EvidenceItem`'s actual, unmodified field
name in `committee/models.py`.)

**Encoding rule** (exhaustive — `EvidenceItem.__post_init__` already
guarantees `value` is exactly `Decimal` or `str`, nothing else):

- `isinstance(value, Decimal)` → `value_type = "decimal"`, `value =
  str(value)`.
- `isinstance(value, str)` → `value_type = "string"`, `value` unchanged.

**Decoding rule — never inferred from the shape of `value`:**

- `value_type == "decimal"` → reconstruct `Decimal(value)`.
- `value_type == "string"` → reconstruct `value` as-is, a plain `str` —
  **even when `value` looks numeric** (e.g. a hypothetical categorical
  value of `"3"` reconstructs as the string `"3"`, never as `Decimal("3")`,
  because `value_type` says so, not because of what the string contains).
- **Any other `value_type`** — missing, `null`, or a string other than the
  two supported values — is a deserialization failure: raise
  `DecisionRecordDeserializationError` immediately. The reader never
  attempts `Decimal(value)` as a fallback, never sniffs the value's shape,
  and never guesses.

**Why this doesn't weaken the "unknown fields on read" forward-compatibility
rule below**: that rule tolerates unknown *top-level* keys because ignoring
them still leaves every *known* field correctly typed and fully
reconstructible — the record is complete, just missing something additive.
An unrecognized `value_type`, by contrast, means a field that *is* present
cannot be correctly interpreted at all; there is no safe default type to
fall back to, so tolerating it would silently produce a wrongly-typed value
rather than a merely-incomplete record. The two situations look similar
(both involve "something the reader doesn't recognize") but are not
equivalent, and are handled with deliberately opposite defaults.

### Other encoding rules

| Python type | JSON encoding | Decoding |
|---|---|---|
| `Decimal` (outside `EvidenceItem.value` — e.g. `IndicatorSnapshot` fields, `PriceBar` fields) | `str(value)` (e.g. `"152.34"`) — **never** `float()` | `Decimal(string)` |
| `datetime` | `value.isoformat()` — always timezone-aware, always the original offset preserved | `datetime.fromisoformat(string)` |
| `str, Enum` members (`AssetType`, `AgentStance`, `RiskSeverity`, `InvestmentAction`/`CommitteeRole` where applicable) | `value.value` (plain string) | `EnumType(string)` |
| `None` | JSON `null` | `None` |
| `tuple[...]` (`price_bars`, `evidence`, `agent_reports`, `dissenting_opinions`, `risks`) | JSON array of the above encodings | Reconstructed as a `tuple` — the embedded dataclasses' own `__post_init__` re-normalizes it regardless |
| `DecisionRecordId` | plain string (the UUID4 hex/canonical form) | Wrapped back via `DecisionRecordId(string)` |

Fields outside `EvidenceItem.value` have no `Decimal | str` ambiguity (each
is declared as exactly one type — e.g. `IndicatorSnapshot.rsi_14: Decimal |
None`), so no discriminator is needed for them; the table above's
`Decimal`-as-string rule is unambiguous on its own in every other context.

`Decimal`-as-string is the only choice compatible with the codebase's
existing hard "no floats anywhere" rule (spec-001, spec-003 acceptance
criteria) — encoding a `Decimal` as a JSON number would round-trip through
an IEEE-754 float in every standard JSON parser, silently reintroducing the
exact precision loss this project has avoided everywhere else.

### Schema evolution

- `schema_version: 1` is the only version this Epic defines, and already
  includes the `value_type` discriminator above — there is no earlier,
  ambiguous version of schema 1 to migrate away from, since no data has
  ever been persisted yet (§27).
- `from_persisted_dict` branches on `schema_version`; an unrecognized value
  raises `DecisionRecordDeserializationError` rather than guessing.
- **No automatic migration framework** is introduced (explicit non-goal). If
  a future Epic changes `DecisionRecord`'s shape, it bumps `schema_version`
  and adds a new branch to `from_persisted_dict`; a one-time rewrite script
  for existing `.jsonl` files, if ever needed, is a deliberate, separate
  piece of work at that time — not built speculatively now.

### Unknown fields on read

Top-level JSON keys not recognized by the current `schema_version`'s parser
are **ignored**, not rejected — this keeps a slightly-newer writer's output
readable by a slightly-older reader during a rolling change, at the cost of
that reader silently not using the new field. Required fields that are
**missing** or **malformed** (wrong type, fails the embedded dataclass's own
`__post_init__` validation once reconstructed, or — for `EvidenceItem` —
carries an unrecognized `value_type`) raise
`DecisionRecordDeserializationError` — silently accepting a structurally
broken record would defeat the point of an auditable ledger.

### Example: one serialized `DecisionRecord` (formatted for readability; the
actual file stores this as one unbroken line)

```json
{
  "schema_version": 1,
  "record_id": "b1e1a6b2-4b7b-4a3a-9b8f-7e6a2e4b9c11",
  "recorded_at": "2026-07-22T14:03:11.482193+00:00",
  "strategy_metadata": {
    "strategy_name": "deterministic_committee",
    "strategy_version": "1",
    "indicator_engine_version": "1",
    "application_version": "0.1.0"
  },
  "market_snapshot": {
    "asset": {
      "ticker": "MSFT",
      "name": null,
      "asset_type": "STOCK",
      "exchange": null,
      "currency": null
    },
    "generated_at": "2026-07-22T14:02:58.001112+00:00",
    "price_bars": [
      {
        "timestamp": "2026-07-21T00:00:00+00:00",
        "open": "509.12", "high": "512.40", "low": "507.88",
        "close": "511.02", "volume": 18234500
      }
    ],
    "metrics": {}
  },
  "indicator_snapshot": {
    "generated_at": "2026-07-22T14:03:02.114400+00:00",
    "latest_close": "511.02", "sma_20": "498.30", "sma_50": "487.11",
    "sma_200": "462.75", "return_1d": "0.0041", "return_30d": "0.0532",
    "return_90d": "0.1187", "return_ytd": "0.2201",
    "annualized_volatility": "0.2140", "maximum_drawdown": "-0.0812",
    "rsi_14": "61.4"
  },
  "decision": {
    "action": "BUY",
    "confidence": "0.90",
    "summary": "BUY MSFT: Technical and Risk both SUPPORT, Devil's Advocate SUPPORT.",
    "agent_reports": [
      {
        "role": "TECHNICAL_ANALYST", "stance": "SUPPORT", "confidence": "0.90",
        "summary": "...", "reasoning": "...",
        "evidence": [
          {
            "key": "price_above_sma20",
            "value_type": "decimal",
            "value": "511.02",
            "interpretation": "PASS - price is above its 20-day average"
          },
          {
            "key": "checklist_coverage",
            "value_type": "string",
            "value": "6/7",
            "interpretation": "6 of 7 technical rules were evaluable"
          }
        ],
        "generated_at": "2026-07-22T14:03:11.482193+00:00"
      }
    ],
    "dissenting_opinions": [],
    "risks": [],
    "generated_at": "2026-07-22T14:03:11.482193+00:00"
  }
}
```

(The second evidence item, `checklist_coverage`, is included specifically
to show `value_type: "string"` alongside `value_type: "decimal"` in one
example — `"6/7"` is a `str` evidence value per spec-003, and must
reconstruct as the literal string `"6/7"`, never be parsed as a number.
`market_snapshot.asset`, `indicator_snapshot`, and `decision.asset` are not
independently repeated — `DecisionRecord`'s own `__post_init__` already
guarantees all three share one asset (§10); the top-level `market_snapshot.
asset` is the single source of truth an implementation reads asset identity
from when reconstructing.)

## 17. Application orchestration

### Where persistence orchestration belongs (required domain question 12)

Four options were weighed:

| Option | Verdict |
|---|---|
| `ApplicationService.analyze_asset()` persists automatically | Rejected |
| A new use case analyzes and records | **Chosen** |
| Persistence optional through dependency injection | **Chosen** — this is *how* the new use case is wired, not a separate option |
| A separate service owns the workflow | Same as "new use case" — see naming below |

**Chosen: a new, separate `RecordDecisionUseCase`** in
`application/record_decision.py`, constructed with an `ApplicationService`
and a `DecisionLedger` by dependency injection. `ApplicationService.
analyze_asset()` itself is **not modified in any way** — no new constructor
parameter, no new optional collaborator, no behavior change for any existing
caller or test.

### Two entry points, not one — `record()` and `analyze_and_record()`

Revision 1 of this spec exposed a single `record_decision(symbol)` method
that always ran a fresh analysis internally. Architecture review identified
a real gap: a caller that already has an `AnalysisResult` (e.g. a CLI that
showed it to a user before asking whether to save it) had no way to persist
*that exact result* — calling the only available method again would
re-fetch market data and could silently produce a *different* decision than
the one already shown. This revision splits the use case into two explicit
methods:

- **`record(analysis_result: AnalysisResult) -> RecordDecisionResult`** —
  the primitive operation. Takes an already-computed `AnalysisResult` and
  durably records it. **Never calls `ApplicationService.analyze_asset()`,
  never fetches market data, and performs no analysis of any kind** — it
  only mints identity/timestamp/metadata, constructs a `DecisionRecord`,
  and appends it.
- **`analyze_and_record(symbol: str) -> RecordDecisionResult`** — the
  composed convenience, for the common case of "just analyze this ticker
  and save the result." Calls `self._application_service.analyze_asset(symbol)`
  and then `self.record(analysis_result)` — nothing more. This is a thin
  composition of two already-independently-specified operations, not a
  third, separately-implemented code path.

```python
# src/investment_committee/application/record_decision.py
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from investment_committee.memory import DecisionLedger, DecisionRecord, DecisionRecordId, StrategyMetadata

from .errors import ApplicationServiceError
from .models import AnalysisResult
from .service import ApplicationService

Clock = Callable[[], datetime]
IdFactory = Callable[[], DecisionRecordId]
# Duplicated one-line type aliases rather than importing committee.Clock -
# same rationale as committee/_validation.py duplicating domain/_validation.py
# (spec-002, approved decision D4): this module's Clock is conceptually its
# own, even though its shape happens to match committee's.


class DecisionRecordingError(ApplicationServiceError):
    """Raised when analysis succeeded but the DecisionRecord could not be appended.

    Never raised by record() for any reason related to analysis - record()
    performs no analysis. The completed AnalysisResult is attached as
    `analysis_result` so a caller can inspect or retry recording without
    re-running the analysis. See spec-005 S19.
    """

    def __init__(self, message: str, analysis_result: AnalysisResult) -> None:
        super().__init__(message)
        self.analysis_result = analysis_result


@dataclass(frozen=True)
class RecordDecisionResult:
    """What one record()/analyze_and_record() call produced.

    persisted_at is timezone-aware UTC and is constructed only after
    DecisionLedger.append() has returned without raising - see spec-005 S12.
    """

    record: DecisionRecord
    persisted_at: datetime


class RecordDecisionUseCase:
    """Durably records DecisionRecords, either from an existing AnalysisResult
    (record()) or by running a fresh analysis first (analyze_and_record())."""

    def __init__(
        self,
        application_service: ApplicationService,
        ledger: DecisionLedger,
        strategy_metadata: StrategyMetadata,
        clock: Clock = lambda: datetime.now(timezone.utc),
        id_factory: IdFactory = lambda: DecisionRecordId(str(uuid.uuid4())),
    ) -> None:
        self._application_service = application_service
        self._ledger = ledger
        self._strategy_metadata = strategy_metadata
        self._clock = clock
        self._id_factory = id_factory

    def record(self, analysis_result: AnalysisResult) -> RecordDecisionResult:
        """Durably record `analysis_result` exactly as given.

        Never calls ApplicationService.analyze_asset() and never fetches
        market data - `analysis_result` is used verbatim (S9, "Full
        embedding, not normalization"). Raises DecisionRecordingError,
        with `analysis_result` attached, if DecisionLedger.append() fails.
        """
        raise NotImplementedError

    def analyze_and_record(self, symbol: str) -> RecordDecisionResult:
        """Run one analysis pass for `symbol`, then durably record it.

        Equivalent to `self.record(self._application_service.analyze_asset(symbol))`.
        Raises the same ApplicationServiceError subclasses as
        ApplicationService.analyze_asset() if analysis itself fails - no
        DecisionRecord is constructed and record() is never reached in that
        case. See spec-005 S18 for the full failure-mode table.
        """
        raise NotImplementedError
```

`record()` never calls `analyze_asset()` — this is the concrete guarantee
required by this revision, and it is what makes "persist the exact
`AnalysisResult` that was previously displayed, reviewed, or otherwise
obtained" true rather than aspirational: there is no code path inside
`record()` capable of fetching fresh data or recomputing anything.

### Composition root

`build_default_record_decision_use_case()` is added to the **existing**
`application/factory.py`, alongside `build_default_application_service()` —
**not** a second, new composition-root file. Revision 1 of this spec
proposed a separate `application/record_decision_factory.py` only because
this spec-only pass cannot modify existing files; architecture review asked
this to be reconsidered on its merits rather than carried forward as
permanent structure, and a single composition root is in fact the better
design (§22, §26): it is the smallest structure that keeps "the one place
concrete implementations are wired for production" a single, cohesive file,
matching this codebase's existing convention exactly rather than
introducing a second file whose only job is holding one function.

```python
# Addition to the EXISTING src/investment_committee/application/factory.py
# (shown here as a diff-shaped addition, not a new file; the module's
# existing build_default_application_service() is unchanged)

from investment_committee.memory import (
    DecisionLedger,
    DETERMINISTIC_STRATEGY_METADATA,
    JsonLinesDecisionLedger,
)

from .record_decision import RecordDecisionUseCase


def build_default_record_decision_use_case(
    ledger: DecisionLedger | None = None,
) -> RecordDecisionUseCase:
    """Assemble the production RecordDecisionUseCase for the deterministic strategy.

    Binds together the deterministic ApplicationService (via
    build_default_application_service(), unchanged), the matching
    DETERMINISTIC_STRATEGY_METADATA, and a durable ledger (a
    JsonLinesDecisionLedger at the default path unless `ledger` overrides
    it) - see spec-005 S13, "Production binding guarantee." No network
    access happens during construction - only during a subsequent record()
    or analyze_and_record() call.
    """
    return RecordDecisionUseCase(
        application_service=build_default_application_service(),
        ledger=ledger if ledger is not None else JsonLinesDecisionLedger(),
        strategy_metadata=DETERMINISTIC_STRATEGY_METADATA,
    )
```

Direct construction of `RecordDecisionUseCase(...)` remains available and
supported for tests and dependency injection (e.g. constructing it with
fakes, as `tests/application/_support.py`'s existing pattern already does
for `ApplicationService`) — the sanctioned path for *production* wiring is
this factory function, documented as such, not a runtime-enforced
restriction (§13).

## 18. Error model

| Failure | Raised by | Propagates as | Cause preserved? |
|---|---|---|---|
| Analysis itself fails inside `analyze_and_record()` (any of the four existing `ApplicationServiceError` subclasses) | `ApplicationService.analyze_asset` (unchanged, spec-004) | Unchanged — propagates directly from `analyze_and_record()`; `record()` is never reached, so no `DecisionRecord` is ever constructed | Unchanged |
| `ledger.append()` raises `DuplicateDecisionError` (reached via either `record()` directly or `analyze_and_record()`) | `DecisionLedger` implementation | `DecisionRecordingError`, with `.analysis_result` attached | `from` the original `DuplicateDecisionError` |
| `ledger.append()` raises `LedgerWriteError` (reached via either `record()` directly or `analyze_and_record()`) | `DecisionLedger` implementation | `DecisionRecordingError`, with `.analysis_result` attached | `from` the original `LedgerWriteError` |
| An unexpected exception type from any collaborator | n/a | Propagates unmodified — `RecordDecisionUseCase` does not catch bare `Exception` | n/a |

Note that `record()` can only ever raise `DecisionRecordingError` (or an
unexpected exception) — it has no analysis step, so none of the four
`ApplicationServiceError` subclasses `analyze_asset()` can raise are
reachable through `record()`. Only `analyze_and_record()` can surface both
failure families, distinguished by which step failed. This follows the
exact translation convention already established by `ApplicationService`
(§6): catch only the named typed exception(s) from the layer below, wrap
with `raise ... from ...`, let anything else propagate.
`DecisionRecordingError` subclasses `ApplicationServiceError` so a caller
(e.g. a future CLI `record` subcommand) can catch one exception type for
either failure mode, exactly like the CLI already does for `analyze_asset()`
today.

## 19. Transaction and failure behavior

Required domain question 13, answered directly:

- **Exception behavior**: see §18. `DecisionRecordingError` is always raised
  with the completed `AnalysisResult` attached — a persistence failure never
  silently discards a valid, already-computed decision.
- **Is the `AnalysisResult` returned?** Not as a normal return value on
  failure (the method raises), but it is recoverable via
  `exc.analysis_result` — the caller can log it, display it to the user
  despite the recording failure, or retry by calling `record(exc.analysis_result)`
  again — which, because `record()` never re-fetches market data, retries
  the *exact same* decision rather than risking a different one.
- **Retry semantics**: **no automatic retry** is built into
  `RecordDecisionUseCase` or either adapter — this Epic does not introduce
  retry/backoff machinery (would be speculative resilience engineering for
  a personal local tool with no evidence yet that transient failures are a
  real problem). A caller may retry by calling `record()` again with the
  same `AnalysisResult`; because a fresh `DecisionRecordId` is minted per
  call, a caller-level retry produces a **new** record with a new identity
  rather than attempting to re-use the failed attempt's id — this is a
  known, accepted limitation (a failed-then-retried recording can, in
  principle, leave two records for what was conceptually "one" recording
  attempt if the first attempt actually wrote but the confirmation was
  lost) rather than solved with exactly-once semantics, which would need
  real transactional storage (the future SQLite adapter, §15) to
  guarantee. Flagged in §28.
- **Are partial writes possible?** Yes, in principle, if a process crashes
  mid-write — see §15's "Concurrency and durability scope." This revision
  no longer claims to gracefully tolerate that case at read time (revision
  1's "tolerate a truncated trailing line" behavior is removed — §15,
  "Construction-time validation"): a file left in that state causes the
  *next* `JsonLinesDecisionLedger` construction to raise
  `DecisionRecordDeserializationError`, requiring manual repair. This is an
  explicit, accepted limitation, not a guarantee this design meets.
- **Idempotency expectations**: identity-based only, as specified in §11 —
  re-appending the same `record_id` is rejected (`DuplicateDecisionError`),
  scoped to one ledger instance's lifetime (§15). There is no exactly-once
  guarantee across a crash between "write succeeded" and "caller learned it
  succeeded," and no guarantee across concurrently-constructed ledger
  instances or processes. This is the practical ceiling of a single-file,
  non-transactional, single-writer adapter and is accepted for this Epic
  (§28).

## 20. Query capabilities

See §14 for the full operation list and rationale (`append`, `get`,
`find_by_asset`, `list_all` — `exists()` removed, see §14). No additional
query capability (date-range filtering, strategy filtering, pagination) is
defined in this Epic — each is additive to the same port later, once a
concrete consumer (Epic 8, Epic 9, Epic 13) needs it. Adding a method to an
`ABC` port is a non-breaking change for existing adapters only if a default
implementation is provided or all adapters are updated together; this spec
does not commit to either approach now, since no such method is being added
yet.

## 21. Security and auditability

Required domain question 15, addressed point by point:

- **Append-only historical evidence**: enforced at two independent levels —
  the `DecisionLedger` port exposes no `update`/`delete` method at all
  (§14), and the chosen durable adapter's storage format (an append-mode
  file, one immutable line per record) makes in-place rewriting physically
  awkward even for code that bypassed the port entirely. Two layers of
  enforcement, not one.
- **Accidental deletion**: no code path in this Epic ever deletes a
  `DecisionRecord` or the ledger file. The ledger file itself is a single
  point of failure on local disk with no built-in backup or replication in
  this Epic — acceptable for a personal, local, single-user system per
  `CLAUDE.md`, but explicitly out of scope to solve here (§28). The file
  lives at `data/ledger/decisions.jsonl` by default (§15), following the
  existing `data/raw/`, `data/processed/`, `data/reports/` pattern already
  in `.gitignore` — adding a corresponding `data/ledger/*` entry (with a
  `.gitkeep`) to `.gitignore` is a one-line implementation-time follow-up,
  not performed now since it would modify an existing file (Non-goals).
- **Corruption detection**: each line is an independently parseable JSON
  object (§15) — a corrupted line is detected at ledger-construction time
  (eager validation, §15) via a `json.loads()` failure local to that one
  line, with no special exception for a trailing line (§15, §19).
  Reconstructing each nested dataclass from its decoded dict also re-runs
  that dataclass's own `__post_init__` validation (§16) — a structurally
  valid but semantically corrupted record (e.g. a confidence value outside
  `[0, 1]`, or an `EvidenceItem` with an unrecognized `value_type`) is
  caught the same way it would be caught on construction, not silently
  accepted because it came from disk.
- **Secrets are never stored**: `DecisionRecord` and every object it embeds
  (`MarketSnapshot`, `IndicatorSnapshot`, `InvestmentDecision`) are drawn
  entirely from existing domain/indicator/committee types, none of which
  have ever had a field for credentials, API keys, or tokens (verified by
  inspection of every dataclass definition read for this spec, §6). No
  field proposed by this spec introduces one either.
- **No broker credentials or tokens in records**: consistent with the
  project's hard constraint that no broker integration exists at all yet
  (`CLAUDE.md`) — there is structurally nothing in this Epic's model that
  *could* hold such a value.
- **Separation between recommendation history and trade execution**:
  `memory/` (this Epic) only ever *records* recommendations; it has no
  dependency on, and is never depended on by, any execution concept.
  `portfolio/`/`simulation/` (Epic 8, future) will *read from* this ledger
  to simulate hypothetical positions but never write into it — matching the
  intended dependency direction (§6: `portfolio`, `simulation` → `memory`,
  never the reverse) — and real broker execution (Epic 14, optional,
  deferred, gated on Epic 13's comparison results per `CLAUDE.md`) is an
  entirely separate, not-yet-designed layer with no code path anywhere near
  this Epic.

## 22. Package/module structure

```
src/investment_committee/memory/
    __init__.py                 # public exports (S23)
    _validation.py                # duplicated require_timezone_aware (spec-002 D4 precedent)
    models.py                      # DecisionRecord, DecisionRecordId, StrategyMetadata
    errors.py                        # LedgerError hierarchy
    serialization.py                   # to_persisted_dict / from_persisted_dict (schema v1, incl. EvidenceItem.value_type)
    versioning.py                        # DETERMINISTIC_STRATEGY_METADATA (S13 - flagged, temporary home)
    ledgers/
        __init__.py                        # re-exports DecisionLedger, InMemoryDecisionLedger, JsonLinesDecisionLedger, DEFAULT_LEDGER_PATH
        base.py                              # DecisionLedger (ABC port)
        in_memory.py                           # InMemoryDecisionLedger
        jsonl.py                                 # JsonLinesDecisionLedger, DEFAULT_LEDGER_PATH

src/investment_committee/application/
    record_decision.py                # RecordDecisionUseCase, RecordDecisionResult, DecisionRecordingError
    factory.py                          # EXISTING file - gains build_default_record_decision_use_case()
```

This is one file fewer than revision 1 proposed: `application/factory.py`
(existing) now owns *both* composition-root functions rather than a second,
new `record_decision_factory.py` (§17, §26). `memory/ledgers/` keeps its own
`base.py`/`in_memory.py`/`jsonl.py` split, unchanged from revision 1 — that
structure mirrors the already-precedented `market_intelligence/providers/
{base,yfinance_provider}.py` port + adapter shape and was not challenged by
architecture review.

## 23. Public API

### `memory/__init__.py`

```python
__all__ = [
    "DecisionRecord",
    "DecisionRecordId",
    "StrategyMetadata",
    "DecisionLedger",
    "InMemoryDecisionLedger",
    "JsonLinesDecisionLedger",
    "DEFAULT_LEDGER_PATH",
    "LedgerError",
    "DuplicateDecisionError",
    "DecisionNotFoundError",
    "LedgerWriteError",
    "LedgerReadError",
    "DecisionRecordDeserializationError",
    "DETERMINISTIC_STRATEGY_METADATA",
]
```

(`DEFAULT_LEDGER_PATH` is newly exported here — a `Path` constant, not a
function; re-exporting it lets a caller reference or compare against the
production default without importing `memory.ledgers.jsonl` directly.)

### Proposed additions to `application/__init__.py`

```python
__all__ = [
    # ... existing names, unchanged ...
    "RecordDecisionUseCase",
    "RecordDecisionResult",
    "DecisionRecordingError",
    "build_default_record_decision_use_case",
]
```

`build_default_record_decision_use_case` is exported from `application/
__init__.py` exactly like `build_default_application_service` already is,
since both now live in the same `factory.py` (§17, §22). `record()` and
`analyze_and_record()` are methods on the already-exported
`RecordDecisionUseCase` class, not separate top-level exports.

(Left as a proposal, not applied — modifying `application/__init__.py` is
out of scope for this spec-only pass, same as every other existing file.)

## 24. Testing strategy

Sketched for the implementation Epic, following this codebase's existing
test conventions exactly (duck-typed fakes in a non-collected `_support.py`,
static import checks, frozen/validation tests per dataclass):

- **`tests/memory/test_models.py`** — `DecisionRecord`/`StrategyMetadata`
  frozen and self-validating (mismatched asset across the three embedded
  objects rejected, blank `record_id` rejected, timezone-naive
  `recorded_at` rejected, blank `StrategyMetadata` field rejected); `asset`
  is a computed property, not a stored field (mirrors
  `tests/application/test_models.py`'s existing pattern for
  `AnalysisResult`).
- **`tests/memory/ledgers/test_ledger_conformance.py`** — a single,
  parametrized test suite run against **both** `InMemoryDecisionLedger` and
  `JsonLinesDecisionLedger` (the latter using `tmp_path`), asserting
  identical behavior for every port operation: `append` then `get` returns
  the same record; `append`ing a duplicate `record_id` raises
  `DuplicateDecisionError`; `get` on a missing id raises
  `DecisionNotFoundError`; `find_by_asset` filters by `Asset` equality
  (constructing two `Asset`s that differ only in whitespace/case and
  asserting they still match, since `Asset.__post_init__` — not the ledger
  — normalizes that) and returns `()` for no match, never raising;
  `list_all` returns `()` on an empty ledger and every record in append
  order otherwise. This is the standard way to guarantee a port's adapters
  are behaviorally interchangeable, and directly protects the "swap in
  SQLite later with zero caller changes" claim in §15.
- **`tests/memory/ledgers/test_jsonl.py`** — adapter-specific behavior the
  conformance suite doesn't cover:
  - constructing against a path that does not exist succeeds with an empty
    ledger and creates nothing on disk;
  - the file and its parent directory are created only on the first
    successful `append()`, not at construction;
  - constructing against an existing, valid file rebuilds the record-id
    index correctly (a subsequent duplicate `append()` for an id already in
    the file raises `DuplicateDecisionError`);
  - constructing against a file containing a malformed JSON line — in any
    position, including the last line — raises
    `DecisionRecordDeserializationError` from `__init__` (no
    trailing-line tolerance, per §15's revised policy);
  - constructing against a file containing two lines with the same
    `record_id` raises `DuplicateDecisionError` from `__init__`;
  - constructing against a file with an unrecognized `schema_version`
    raises `DecisionRecordDeserializationError` from `__init__`;
  - construction never writes to the file (assert file mtime/content
    unchanged after a successful construction against an existing file);
  - each `append()` performs exactly one `write()` (no read-modify-write of
    the whole file);
  - `DEFAULT_LEDGER_PATH` equals `Path("data") / "ledger" / "decisions.jsonl"`.
- **`tests/memory/test_serialization.py`** — round-trip
  `to_persisted_dict`/`from_persisted_dict` for a `DecisionRecord`
  containing every field type (`Decimal`, enum, nested tuple, `None`
  indicator values, and both `EvidenceItem.value_type` variants); in
  particular:
  - an `EvidenceItem` with a `Decimal` value round-trips as `Decimal`
    (assert `isinstance(..., Decimal)` and exact value equality, never
    equality against a `float` literal);
  - an `EvidenceItem` with a `str` value that looks numeric (e.g. `"42"`)
    round-trips as the literal string `"42"`, never as `Decimal("42")` —
    this is the test that directly proves the architecture-review-flagged
    ambiguity is fixed;
  - an `EvidenceItem` dict with `value_type` missing, `null`, or set to an
    unrecognized string (e.g. `"float"`) raises
    `DecisionRecordDeserializationError`, and the reader never falls back
    to inferring the type from `value`'s contents;
  - an unrecognized `schema_version` raises
    `DecisionRecordDeserializationError`; unknown top-level keys are
    ignored without error; a missing required field raises
    `DecisionRecordDeserializationError`.
- **`tests/application/test_record_decision.py`** — using the same
  duck-typed-fake pattern as `tests/application/_support.py`'s existing
  `FakeMarketIntelligenceService`/`FakeIndicatorEngine`/`FakeCommitteeEngine`:
  a fake `DecisionLedger` records what it was `append()`-ed;
  - `record(analysis_result)`: happy path returns `RecordDecisionResult`
    wrapping the constructed `DecisionRecord`; **the fake
    `ApplicationService` is never called** (assert zero calls) — proving
    `record()` performs no analysis; a fake ledger that raises
    `DuplicateDecisionError`/`LedgerWriteError` causes
    `DecisionRecordingError` with `exc.analysis_result` set to the exact
    `analysis_result` passed in and `exc.__cause__` set to the original
    ledger exception;
  - `analyze_and_record(symbol)`: happy path calls the fake
    `ApplicationService.analyze_asset(symbol)` exactly once, then behaves
    identically to `record()` on the resulting `AnalysisResult`; an
    analysis failure (any `ApplicationServiceError` subclass from the fake
    service) propagates unchanged and the fake ledger's `append()` is never
    called;
  - `record_id`/`recorded_at`/`persisted_at` come from the injected
    `id_factory`/`clock`, not a hardcoded value (mirrors the existing
    `Clock`-injection test pattern in `tests/committee/test_engine.py`);
    `persisted_at` is asserted to be constructed strictly after the fake
    ledger's `append()` call is observed to have completed;
  - `RecordDecisionResult` is a frozen dataclass with exactly the fields
    `record`, `persisted_at` (mirrors `tests/application/test_models.py`'s
    existing frozen-dataclass assertions for `AnalysisResult`).
- **`tests/application/test_factory.py`** (additions) — `build_default_record_decision_use_case()`
  returns a `RecordDecisionUseCase` wired with a `JsonLinesDecisionLedger`
  at `DEFAULT_LEDGER_PATH` by default, and with the caller-supplied `ledger`
  when one is passed; its `strategy_metadata` is `DETERMINISTIC_STRATEGY_METADATA`;
  no network call happens during construction (mirrors the existing
  `build_default_application_service()` test).
- **`tests/memory/test_no_forbidden_imports.py`** — a static check, mirroring
  the existing `tests/application/test_no_forbidden_imports.py` exactly:
  nothing under `memory/` imports `application`, `portfolio`, `simulation`,
  `evaluation`, `interfaces`, `integrations`, or the root `cli.py` (§6's
  intended dependency direction, made a checkable regression test rather
  than only a documented rule); `memory/` importing `domain`, `indicators`,
  and `committee` is asserted to be *allowed* (a positive check, not just
  the absence of a forbidden-import failure).
- **`tests/memory/test_init.py`** — every name in `memory.__all__` is
  importable, mirroring `tests/application/test_init.py`'s existing exact
  export-set assertion.

## 25. Acceptance criteria

(For the implementation Epic this spec precedes — not claimed as satisfied
by this document, which contains no code.)

- `src/investment_committee/memory/{__init__,models,errors,serialization,versioning,_validation}.py`
  and `memory/ledgers/{__init__,base,in_memory,jsonl}.py` exist and
  implement exactly the public API in this spec.
- `src/investment_committee/application/record_decision.py` exists and
  implements exactly the API in §17; `build_default_record_decision_use_case()`
  is added to the **existing** `src/investment_committee/application/factory.py`
  (no new `record_decision_factory.py` file).
- `DecisionRecord`, `StrategyMetadata`, `RecordDecisionResult` are frozen,
  self-validating dataclasses following this codebase's existing
  conventions exactly (§6, §17).
- `DecisionLedger` exposes exactly `append`, `get`, `find_by_asset`,
  `list_all` — no `update`, `delete`, or `exists` method, anywhere, on the
  ABC or either adapter.
- Every serialized `EvidenceItem` includes an explicit `value_type`
  (`"decimal"` or `"string"`); deserialization reconstructs `Decimal` or
  `str` accordingly, without ever inferring the type from `value`'s
  contents; a missing or unrecognized `value_type` raises
  `DecisionRecordDeserializationError`.
- `RecordDecisionUseCase.record(analysis_result)` never calls
  `ApplicationService.analyze_asset()` and never performs network or file
  I/O to fetch market data. `RecordDecisionUseCase.analyze_and_record(symbol)`
  calls `analyze_asset(symbol)` exactly once, then `record()` exactly once,
  with no other collaborator calls.
- `JsonLinesDecisionLedger()`'s default path is `Path("data") / "ledger" /
  "decisions.jsonl"` (`DEFAULT_LEDGER_PATH`); missing parent directories and
  the file itself are created only on the first successful `append()`, never
  at construction or at import time.
- `JsonLinesDecisionLedger.__init__` eagerly validates every non-empty line
  of an existing file (malformed JSON, unrecognized `schema_version`,
  duplicate `record_id` within the file, or a value failing domain
  validation each raise immediately from `__init__`, with no exception for
  a trailing line) and never writes to the file during construction.
- `ApplicationService.analyze_asset()`'s existing signature, return type,
  and behavior are byte-for-byte unchanged; no existing test in
  `tests/application/` requires modification because of this Epic.
- `Decimal` values round-trip through serialization exactly (never via
  `float`); enums serialize as their `.value`; datetimes serialize as
  timezone-aware ISO 8601 strings.
- No new external dependency is added; standard library only.
- No import of `application`, `portfolio`, `simulation`, `evaluation`,
  `interfaces`, `integrations`, or the root `cli.py` anywhere in `memory/`.
- `pytest` passes, including every new test package listed in §24, with no
  regressions in any existing suite.
- `docs/roadmap.md` is left untouched by this Epic's implementation PR (any
  "Recommendation Ledger" → "Decision Ledger" rename is a separate,
  deliberate follow-up — §7). **`docs/architecture.md`'s "Dependency
  direction, summarized" section is corrected to add `memory → committee,
  indicators` explicitly, as part of this Epic's implementation PR, before
  it merges** (§6) — the one exception to "leave the architecture docs
  untouched," and a hard prerequisite for merge, not an optional follow-up.

## 26. Alternatives considered

- **`DecisionRecord` wraps `AnalysisResult` directly.** Rejected outright —
  not a trade-off but a structural impossibility given the intended
  dependency direction (`application → memory`); see §6, §9.
- **Content-derived (hash-based) `DecisionRecordId`.** Rejected: fragile
  against schema evolution, and conflates "same content" with "same
  identity" when §11 explicitly wants those treated as different questions.
- **SQLite as the durable adapter for this Epic.** Rejected *for this Epic
  specifically* (not rejected long-term) — see §15 for the full reasoning;
  the port is designed so this remains a pure addition later.
- **A single top-level JSON array file instead of JSON Lines.** Rejected:
  one corrupted byte anywhere in the file breaks the entire parse, versus
  JSON Lines' per-line isolation (§15, §21) — a concrete auditability
  regression for no offsetting benefit at this project's scale.
- **Normalizing/trimming the embedded `MarketSnapshot`/`IndicatorSnapshot`/
  `InvestmentDecision`.** Rejected for this Epic — see §9 "Full embedding,
  not normalization." The storage-overlap cost of repeated price history
  across nearby-in-time records for the same asset is accepted as a known
  trade-off (§28), not solved by premature normalization before real usage
  data shows it matters.
- **Auto-persisting inside `ApplicationService.analyze_asset()`.** Rejected:
  violates the Epic prompt's explicit instruction not to silently change
  `analyze_asset()`'s existing public behavior, and would make persistence
  mandatory rather than optional dependency injection, removing a caller's
  ability to analyze without recording.
- **Full semantic versioning or git-SHA-derived version strings for
  `StrategyMetadata`.** Rejected as premature machinery with no existing
  versioning convention in `committee/`/`indicators/` to build on — see
  §13.
- **Inferring `EvidenceItem.value`'s type from the shape of the serialized
  string** (e.g. "try `Decimal(value)`, fall back to `str` on failure").
  Rejected: this is exactly the implicit-inference behavior architecture
  review flagged as unsound (a categorical string that happens to look
  numeric, e.g. `"3"`, would be silently misclassified as `Decimal`); an
  explicit `value_type` discriminator (§16) is the only approach that
  cannot misclassify.
- **Tolerating a truncated trailing line at `JsonLinesDecisionLedger`
  read/construction time** (revision 1's original design). Rejected in this
  revision: it required distinguishing "an interrupted write" from "real
  corruption" purely by line position, which is a fragile heuristic that
  also let revision 1 imply a stronger "atomic write" guarantee than the
  design actually provides. Replaced with uniform, strict fail-fast
  validation of every line (§15) plus honest, narrower language about what
  durability this Epic actually guarantees (§15, "Concurrency and
  durability scope").
- **A second composition-root file, `application/record_decision_factory.py`**
  (revision 1's original design, adopted only because this spec-only pass
  could not edit existing files). Rejected on reconsideration: it split one
  logical composition root into two files for a single function, which is
  more structure than the codebase's existing "one composition root" (§6)
  convention calls for. Folded into the existing `application/factory.py`
  instead (§17, §22).
- **`find_by_asset(ticker: str)`, re-normalizing the ticker inside the
  ledger** (revision 1's original design). Rejected in this revision in
  favor of `find_by_asset(asset: Asset)`: `Asset` already normalizes its
  own ticker in `__post_init__` (`domain/assets.py`) and has value
  equality, so accepting `Asset` and comparing by equality avoids adding a
  *fourth* independent ticker-normalization implementation to a codebase
  that spec-004 already flagged as having three (Unresolved Question 5) —
  and it is the smaller, more direct interface for a method whose whole
  point is "records for this asset."
- **`exists(record_id)` as a fifth port operation.** Rejected in this
  revision — see §14 for the full reasoning (no named concrete consumer;
  redundant with catching `DecisionNotFoundError` from `get()`).

## 27. Migration and compatibility

- **No existing persisted data exists yet** — this is the Epic that
  introduces persistence for the first time, so there is no legacy format
  to migrate from, and no earlier, ambiguous variant of `schema_version: 1`
  to migrate away from: the `EvidenceItem.value_type` discriminator (§16)
  is simply part of what `schema_version: 1` has always meant, by the time
  any implementation actually ships.
- **Forward compatibility within schema_version 1** is handled by the
  "ignore unknown top-level keys" read rule (§16) — a future minor addition
  within the same conceptual shape (e.g. an optional new field with a
  sensible default) does not require a version bump if older readers can
  safely ignore it. This leniency does **not** extend to `EvidenceItem.
  value_type` (§16 explains why the two cases are handled with opposite
  defaults).
- **Breaking changes to `DecisionRecord`'s shape** require a
  `schema_version` bump and a new branch in `from_persisted_dict`;
  `DecisionRecordDeserializationError` is the explicit signal that a reader
  has encountered a version it does not know how to handle, rather than a
  silent misparse.
- **No ORM, no automatic migration framework** — any future one-time
  rewrite of existing `.jsonl` records to a new schema version is a
  deliberate, reviewed script, written when that need is real, not built
  speculatively now (explicit non-goal, §5).
- **Adapter migration (JSON Lines → SQLite)**, when it happens, is
  transparent to every caller of the `DecisionLedger` port by construction
  (§15) — no `DecisionRecord`, `RecordDecisionUseCase`, or downstream
  consumer code should need to change, only the composition root's choice
  of concrete adapter.

## 28. Unresolved questions

Carried forward for architecture review, not resolved by this spec:

1. **Retry-after-failure can, in principle, double-record.** §19/§11: a
   caller retrying `record()` after a `DecisionRecordingError` mints a new
   `record_id`, so if the original `append()` actually succeeded but the
   caller never learned that (e.g. a crash between write and return), a
   retry produces two records for one real event. Solving this properly
   needs transactional storage (the future SQLite adapter) or a
   client-supplied idempotency key pattern; neither is introduced in this
   Epic.
2. **Storage overlap from full embedding.** §9/§26: repeated analyses of the
   same asset close in time each store their own full copy of overlapping
   `PriceBar` history. Acceptable at this project's current scale; revisit
   if/when real usage data shows it is not.
3. **Where `strategy_version`/`indicator_engine_version` constants should
   ultimately live** (`committee/`/`indicators/` themselves, vs. `memory/`
   as proposed here) — §13 flags this as a temporary concession to this
   spec-only pass's "don't modify existing files" constraint, to be revisited
   once implementation is in scope. (Distinct from, and not resolved by,
   this revision's "production binding guarantee," §13, which concerns
   *pairing* `StrategyMetadata` with the right `ApplicationService`, not
   where the constants are declared.)
4. **Whether `find_by_asset` needs a second filter dimension** (e.g. by
   `StrategyMetadata.strategy_name`, relevant once Epic 12's hybrid
   committees coexist with the deterministic baseline) before Epic 13
   actually needs it, or whether that is better added to the port only when
   a concrete consumer requires it (§20).
5. **The "Recommendation Ledger" → "Decision Ledger" rename** in
   `docs/architecture.md`/`docs/roadmap.md` (§7) — this spec adopts the new
   name for its own vocabulary but does not perform that rename; confirming
   it (or reverting to "Recommendation Ledger" for consistency with
   already-written docs) is an explicit decision for architecture review.
   **Distinct from, and not to be conflated with, the dependency-direction
   correction in §6**, which is a required, narrower `docs/architecture.md`
   edit (adding `memory → committee, indicators` to the summarized list)
   that must land before this Epic's implementation PR merges, independent
   of whether this naming rename ever happens.
6. **No pagination or date-range query exists yet** (§20) — deferred until
   Epic 8/9's actual consumption patterns are known, flagged in case
   architecture review wants it decided now instead of additively later.
7. **Windows append/flush/durability behavior is unverified.** §15,
   "Concurrency and durability scope": whether a plain `write()` +
   `flush()` is sufficient for `JsonLinesDecisionLedger.append()` to
   survive a process crash on Windows (`CLAUDE.md`'s target platform), or
   whether an explicit `os.fsync()` (or another platform-specific step) is
   needed, must be verified during implementation — this spec deliberately
   does not assume an answer.
8. **Manual recovery from a corrupted JSONL file is undefined.** §15,
   "Construction-time validation": this revision's stricter fail-fast
   policy means a corrupted file blocks `JsonLinesDecisionLedger`
   construction entirely until a human repairs it, but no repair
   tool/procedure is specified — accepted as out of scope for this Epic
   (a personal, local, single-user tool where the file is human-readable
   and human-editable), but worth a documented recovery note (e.g. in a
   README) whenever implementation lands.
