# Spec 002 - Investment Committee Domain

## Objective

Define the deterministic domain contracts for the Investment Committee layer:
the immutable value types that represent a single analyst's opinion
(`AgentReport`) and the committee's final output (`InvestmentDecision`), plus
the supporting enums and structured evidence/risk types they are built from.

These types describe *shapes and invariants only*. They carry no behavior
beyond validating themselves at construction time, matching the pattern
already established by `domain/` (`Asset`, `PriceBar`, `MarketSnapshot`,
`Recommendation`) and `indicators/models.py` (`IndicatorSnapshot`).

```
                   IndicatorSnapshot
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
        ▼                  ▼                  ▼
Technical Analyst    Risk Manager     Devil's Advocate
        │                  │                  │
        └──────────────┬───┴──────────────────┘
                       ▼
             Investment Committee Chair
                       ▼
                 InvestmentDecision
```

`AgentReport` is what each of the three analyst roles produces from an
`IndicatorSnapshot`. `InvestmentDecision` is what the chair produces from the
three `AgentReport`s. Nothing in this Epic computes either — that is Phase 3
(committee orchestration) and out of scope here.

## Non-goals

This Epic defines data, not behavior. Explicitly out of scope:

- No LLM integration, prompts, or prompt text.
- No agent orchestration, no `CommitteeChair` / analyst service classes.
- No network access, no calls to `market_intelligence` or `integrations`.
- No recommendation-generation logic (nothing decides BUY vs HOLD vs SELL).
- No persistence — `memory/` (Phase 2) is a separate epic.
- No changes to `domain/`, `indicators/`, or any existing package.
- No CLI changes.

## Architecture boundaries

Per `docs/architecture.md`, `committee/` sits above `indicators/` and
`domain/` and depends on them; nothing in `indicators/` or `domain/` may
depend on `committee/`. Concretely for this Epic:

- `committee/models.py` may import from `investment_committee.domain` and
  `investment_committee.indicators` (for type references such as `Asset` and
  `IndicatorSnapshot` in docstrings/signatures) but introduces no reverse
  dependency.
- `committee/models.py` has no dependency on `market_intelligence`,
  `memory`, `evaluation`, `interfaces`, or `integrations`.
- Standard library only: `dataclasses`, `datetime`, `decimal`, `enum`,
  `typing`. No pandas, no numpy, no LLM SDKs.

## Package layout

```
src/investment_committee/committee/
    __init__.py
    models.py
    _validation.py
```

`_validation.py` is a package-local copy of the same one-function helper
`domain/_validation.py` already defines (`require_timezone_aware`), kept
deliberately separate rather than imported from `domain`. This Epic does not
refactor shared validation across packages — see "Approved decisions" (D4).

## Public API

`investment_committee.committee` re-exports:

```python
__all__ = [
    "CommitteeRole",
    "ANALYST_ROLES",
    "AgentStance",
    "InvestmentAction",
    "EvidenceItem",
    "AgentReport",
    "RiskSeverity",
    "RiskItem",
    "InvestmentDecision",
]
```

### Exact field lists (summary)

These are restated in full, with types and validation, in "Dataclass
definitions" below. Listed here up front so the two report/decision shapes
are unambiguous at a glance:

- **`AgentReport`** — `asset`, `role`, `stance`, `confidence`, `summary`,
  `reasoning`, `evidence`, `generated_at`. Eight fields, all required.
- **`InvestmentDecision`** — `asset`, `action`, `confidence`, `summary`,
  `agent_reports`, `dissenting_opinions`, `risks`, `generated_at`. Eight
  fields, all required.

## Enum definitions

### `CommitteeRole(str, Enum)`

```python
class CommitteeRole(str, Enum):
    TECHNICAL_ANALYST = "TECHNICAL_ANALYST"
    RISK_MANAGER = "RISK_MANAGER"
    DEVILS_ADVOCATE = "DEVILS_ADVOCATE"
    COMMITTEE_CHAIR = "COMMITTEE_CHAIR"
```

Module-level constant:

```python
ANALYST_ROLES: frozenset[CommitteeRole] = frozenset(
    {CommitteeRole.TECHNICAL_ANALYST, CommitteeRole.RISK_MANAGER, CommitteeRole.DEVILS_ADVOCATE}
)
```

`ANALYST_ROLES` is the single source of truth for "which roles may author an
`AgentReport` or a `RiskItem`," used by `AgentReport.__post_init__`,
`RiskItem.__post_init__`, and `InvestmentDecision.__post_init__` so the rule
cannot drift between checks.

### `AgentStance(str, Enum)`

```python
class AgentStance(str, Enum):
    SUPPORT = "SUPPORT"
    NEUTRAL = "NEUTRAL"
    OPPOSE = "OPPOSE"
```

`AgentStance` is deliberately distinct from `InvestmentAction`: a stance is
one analyst's opinion of the thesis under discussion (do they support it,
oppose it, or take no strong position), not a trade instruction. Only the
chair's `InvestmentDecision.action` is an `InvestmentAction`. This mirrors
the diagram: three `AgentStance`-bearing reports feed one
`InvestmentAction`-bearing decision.

### `InvestmentAction` — **approved: alias of `domain.RecommendationAction`**

```python
from investment_committee.domain import RecommendationAction as InvestmentAction
```

`domain/recommendations.py` already defines `RecommendationAction` with
exactly these three values (`BUY`, `HOLD`, `SELL`) for exactly this purpose —
the final action a committee-style output advises. `committee/` re-exports it
under the name `InvestmentAction` (matching the vocabulary of
`InvestmentDecision`) instead of defining a second, parallel BUY/HOLD/SELL
enum. This is an approved decision — see "Approved decisions" (D1). No
changes to `domain/recommendations.py` are made.

### `RiskSeverity(str, Enum)`

```python
class RiskSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
```

## Dataclass definitions

### `EvidenceItem` (frozen)

```python
@dataclass(frozen=True)
class EvidenceItem:
    key: str
    value: Decimal | str
    interpretation: str
```

- `key`: machine-readable metric name, e.g. `"rsi_14"`, `"sma_20_cross"`,
  `"max_drawdown"`. Not blank.
- `value`: the observed value. Restricted to `Decimal | str` — `Decimal` for
  numeric metrics read straight from an `IndicatorSnapshot` (no floats, per
  project convention), `str` for categorical/observational evidence that
  has no numeric form (e.g. `"golden cross"`, `"earnings in 3 days"`). No
  `bool`, `int`, or `Any` — a bare `bool` would be ambiguous evidence, and
  `int` volumes/counts should be represented as `Decimal` for consistency
  with the rest of the domain's numeric fields.
- `interpretation`: concise human-readable reading of the value, e.g.
  `"RSI above 70, overbought"`. Not blank.

**Validation** (`__post_init__`):
- `key.strip()` must be non-empty.
- `interpretation.strip()` must be non-empty.
- if `value` is a `str`, `value.strip()` must be non-empty.
- if `value` is neither `Decimal` nor `str`, raise `ValueError` (defends the
  restricted union at runtime, since Python does not enforce `Union` types).

`EvidenceItem` is used only for `AgentReport.evidence`. It is **not** reused
for risks — see `RiskItem` below and "Approved corrections" (B).

### `AgentReport` (frozen)

```python
@dataclass(frozen=True)
class AgentReport:
    asset: Asset
    role: CommitteeRole
    stance: AgentStance
    confidence: Decimal
    summary: str
    reasoning: str
    evidence: Sequence[EvidenceItem]
    generated_at: datetime
```

All eight fields are required; there are no defaults.

| Field | Type | Validation |
|---|---|---|
| `asset` | `Asset` | none beyond `Asset`'s own `__post_init__` |
| `role` | `CommitteeRole` | must be in `ANALYST_ROLES` (rejects `COMMITTEE_CHAIR`) |
| `stance` | `AgentStance` | any member is valid; no additional constraint |
| `confidence` | `Decimal` | `Decimal("0") <= confidence <= Decimal("1")` |
| `summary` | `str` | `summary.strip()` non-empty |
| `reasoning` | `str` | `reasoning.strip()` non-empty |
| `evidence` | `Sequence[EvidenceItem]` | normalized to `tuple[EvidenceItem, ...]`; may be empty |
| `generated_at` | `datetime` | must be timezone-aware |

**Validation** (`__post_init__`), in the order checks are performed:
1. `role` must be a member of `ANALYST_ROLES`; otherwise raise `ValueError`
   ("AgentReport.role must be an analyst role, not COMMITTEE_CHAIR"). This is
   the mechanism that stops the chair from producing a normal analyst
   report — approved, see "Approved decisions" (D2).
2. `confidence` must satisfy `Decimal("0") <= confidence <= Decimal("1")`,
   same rule and same type as `Recommendation.confidence`. `Decimal("0")`
   and `Decimal("1")` themselves are valid (boundary-inclusive).
3. `summary.strip()` must be non-empty.
4. `reasoning.strip()` must be non-empty.
5. `generated_at` must be timezone-aware (`require_timezone_aware`).
6. `evidence` is converted to a `tuple[EvidenceItem, ...]` and stored via
   `object.__setattr__`, matching how `MarketSnapshot.price_bars` is frozen.
   This conversion happens regardless of whether the caller passed a `list`,
   a `tuple`, or any other `Sequence` — the stored value is always a fresh
   `tuple`, never the caller's original object. An empty tuple is allowed —
   a report can have zero structured evidence items (it must still have
   non-blank `reasoning`).

Note there is no `stance`-driven or `confidence`-driven cross-field rule in
this Epic (e.g. `stance == OPPOSE` does not constrain `confidence`) — each
field is validated independently.

### `RiskItem` (frozen)

```python
@dataclass(frozen=True)
class RiskItem:
    key: str
    severity: RiskSeverity
    description: str
    source_role: CommitteeRole
```

- `key`: machine-readable risk identifier, e.g. `"concentration"`,
  `"earnings_gap"`, `"liquidity"`. Not blank. Combined with `source_role`,
  this is the identity used to detect duplicate risks in an
  `InvestmentDecision` (see below) — the same `key` from two different
  analyst roles is not a duplicate, since it represents two independent
  observations of a similarly-named risk.
- `severity`: `RiskSeverity.LOW | MEDIUM | HIGH`.
- `description`: human-readable explanation of the risk. Not blank.
- `source_role`: which analyst role raised this risk. Must be one of
  `ANALYST_ROLES` — a risk must be traceable to the specific analyst who
  identified it, and the chair does not author risks directly in this model.

**Validation** (`__post_init__`):
1. `key.strip()` must be non-empty.
2. `description.strip()` must be non-empty.
3. `source_role` must be a member of `ANALYST_ROLES`; otherwise raise
   `ValueError` ("RiskItem.source_role must be an analyst role, not
   COMMITTEE_CHAIR"). Mirrors `AgentReport`'s chair rejection for the same
   reason: nothing else in this Epic would otherwise stop a chair-attributed
   risk from being accepted.

`RiskItem` replaces the earlier draft's reuse of `EvidenceItem` for risks —
see "Approved corrections" (B). A risk is not an observed value with an
interpretation; it is a severity-rated concern attributed to a specific
analyst, which `EvidenceItem` cannot express.

### `InvestmentDecision` (frozen)

```python
@dataclass(frozen=True)
class InvestmentDecision:
    asset: Asset
    action: InvestmentAction
    confidence: Decimal
    summary: str
    agent_reports: Sequence[AgentReport]
    dissenting_opinions: Sequence[AgentReport]
    risks: Sequence[RiskItem]
    generated_at: datetime
```

All eight fields are required; there are no defaults.

| Field | Type | Validation |
|---|---|---|
| `asset` | `Asset` | none beyond `Asset`'s own `__post_init__` |
| `action` | `InvestmentAction` | any member is valid |
| `confidence` | `Decimal` | `Decimal("0") <= confidence <= Decimal("1")` |
| `summary` | `str` | `summary.strip()` non-empty |
| `agent_reports` | `Sequence[AgentReport]` | normalized to `tuple[AgentReport, ...]`; exactly the three `ANALYST_ROLES`, no duplicates, each `report.asset == self.asset` |
| `dissenting_opinions` | `Sequence[AgentReport]` | normalized to `tuple[AgentReport, ...]`; every element must be one of `agent_reports`; no duplicate roles; may be empty |
| `risks` | `Sequence[RiskItem]` | normalized to `tuple[RiskItem, ...]`; no duplicate `(key, source_role)` pairs; may be empty |
| `generated_at` | `datetime` | must be timezone-aware |

**Validation** (`__post_init__`), in the order checks are performed:
1. `agent_reports` is converted to a `tuple[AgentReport, ...]`.
2. `agent_reports` must contain exactly the roles in `ANALYST_ROLES` — no
   duplicates, none missing, none extra. Implementation check:
   `{report.role for report in agent_reports} == ANALYST_ROLES` combined
   with `len(agent_reports) == len(ANALYST_ROLES)` (the length check catches
   a duplicate role paired with a missing one, which the set-equality check
   alone would not — e.g. two `TECHNICAL_ANALYST` reports and no
   `RISK_MANAGER` report would satisfy neither check alone in all cases, so
   both are required together).
3. `CommitteeRole.COMMITTEE_CHAIR` can never appear here — already excluded
   transitively, since `AgentReport.__post_init__` rejects it, but this is
   also implied by the exact-role-set check above.
4. Every `report.asset` in `agent_reports` must equal `self.asset`
   (`Asset` is a frozen dataclass with value equality, so `==` is
   sufficient; no need for identity comparison).
5. `dissenting_opinions` is converted to a `tuple[AgentReport, ...]`.
   - Every element must already be present in the converted `agent_reports`
     tuple — this is the traceability requirement: a dissenting opinion is
     not freeform text, it is a pointer to one of the three reports actually
     considered. Raise `ValueError` if any element is not found.
   - `dissenting_opinions` must not contain duplicates. Since
     `agent_reports` is already guaranteed to have one report per role,
     duplication is detected by role: if the same role appears more than
     once in `dissenting_opinions`, raise `ValueError`
     ("dissenting_opinions must not contain duplicate reports"). This is
     the chair's explicit, duplicate-free subset of `agent_reports` —
     nothing here infers dissent from `AgentStance` (approved, see
     "Approved decisions" D3).
   - `dissenting_opinions` may be empty (unanimous decision).
6. `risks` is converted to a `tuple[RiskItem, ...]`.
   - No two risks may share the same `(key, source_role)` pair; raise
     `ValueError` ("risks must not contain duplicate (key, source_role)
     entries") if they do. Two different analyst roles independently
     flagging a risk with the same `key` is allowed (they are two distinct
     observations); the same role flagging the same `key` twice is not.
   - `risks` may be empty.
7. `confidence` must satisfy `Decimal("0") <= confidence <= Decimal("1")`.
8. `summary.strip()` must be non-empty.
9. `generated_at` must be timezone-aware.

## Immutability requirements

- All four dataclasses (`EvidenceItem`, `AgentReport`, `RiskItem`,
  `InvestmentDecision`) are `@dataclass(frozen=True)`.
- **Every** sequence-typed field on every dataclass in this module is
  defensively normalized to a `tuple` inside `__post_init__` via
  `object.__setattr__`, exactly like `MarketSnapshot.price_bars` already
  does in `domain/market.py`. This applies to:
  - `AgentReport.evidence` → `tuple[EvidenceItem, ...]`
  - `InvestmentDecision.agent_reports` → `tuple[AgentReport, ...]`
  - `InvestmentDecision.dissenting_opinions` → `tuple[AgentReport, ...]`
  - `InvestmentDecision.risks` → `tuple[RiskItem, ...]`

  The declared field type is `Sequence[...]` (so callers may pass a `list`,
  `tuple`, or any other `Sequence`), but the value actually stored on the
  instance is always a newly-constructed `tuple`. The object never retains
  the caller's original mutable list — mutating a `list` the caller passed
  in *after* construction must have no effect on the constructed instance,
  and the test plan below covers this directly for every sequence field.
- No dataclass in this module declares a mutable default (no `list`/`dict`
  default values); where a default is needed, follow `MarketSnapshot`'s
  `field(default_factory=dict)` pattern — though note none of the fields
  defined here currently have defaults; all eight fields on both
  `AgentReport` and `InvestmentDecision` are required.

## Traceability requirements

- `InvestmentDecision.agent_reports` preserves the actual three `AgentReport`
  objects the chair used, not summaries of them — anything downstream
  (evaluation, memory, explanations) can inspect exactly what each analyst
  said, including `stance`, `confidence`, and `evidence`.
- `InvestmentDecision.dissenting_opinions` is an explicit, duplicate-free
  subset of `agent_reports`, chosen by the chair and enforced in
  `__post_init__` — not inferred from `AgentStance`. A dissenting opinion can
  always be traced back to a specific role's full report; it is never a
  disconnected string, and it can never appear twice.
- `RiskItem.source_role` traces every risk back to the analyst role that
  raised it, so `InvestmentDecision.risks` is auditable per-analyst even
  though risks are not required to reference a specific `AgentReport`
  instance the way `dissenting_opinions` does.

## Error behavior

All validation raises plain `ValueError`, matching the existing convention
in `domain/` (`Asset`, `PriceBar`, `MarketSnapshot`, `Recommendation`) and
`indicators/models.py` (`IndicatorSnapshot`). No new exception type (e.g.
`CommitteeDomainError`) is introduced.

Rationale: every existing frozen dataclass in this codebase validates its
own invariants at construction time with `ValueError`, and reserves a
dedicated exception class (`IndicatorCalculationError`) only for an
*operation* that can fail for reasons unrelated to a single object's shape —
`IndicatorEngine.calculate()` inspects an entire `MarketSnapshot`'s bars for
structural problems (empty, unordered, invalid closes) before any
`IndicatorSnapshot` is constructed. This Epic adds no such operation — there
is no `CommitteeEngine` yet, only value types — so there is nothing for a
dedicated exception class to distinguish itself from. Introducing one now
would be speculative; add it in the epic that introduces committee
orchestration, if by then it's clear multiple distinct failure modes need to
be told apart programmatically.

## Test plan

New `tests/committee/` package, mirroring `tests/domain/` and
`tests/indicators/`.

`tests/committee/test_models.py` must cover:

**`EvidenceItem`**
- valid construction with `Decimal` value;
- valid construction with `str` value;
- blank `key` rejected;
- blank `interpretation` rejected;
- blank `str` value rejected;
- non-`Decimal`/`str` value (e.g. `int`, `bool`, `float`) rejected.

**`AgentReport`**
- valid construction for each of the three analyst roles
  (`TECHNICAL_ANALYST`, `RISK_MANAGER`, `DEVILS_ADVOCATE`);
- `role=COMMITTEE_CHAIR` rejected;
- confidence below `Decimal("0")` rejected;
- confidence above `Decimal("1")` rejected;
- confidence exactly `Decimal("0")` accepted;
- confidence exactly `Decimal("1")` accepted;
- blank (empty and whitespace-only) `summary` rejected;
- blank (empty and whitespace-only) `reasoning` rejected;
- timezone-naive `generated_at` rejected;
- `evidence` supplied as a `list` is stored as a `tuple`;
- mutating the original `list` passed as `evidence` after construction does
  not change the constructed `AgentReport`'s `evidence`;
- empty `evidence` tuple accepted.

**`RiskSeverity` / `RiskItem`**
- valid construction for each `RiskSeverity` member;
- valid construction for each of the three analyst `source_role` values;
- `source_role=COMMITTEE_CHAIR` rejected;
- blank `key` rejected;
- blank `description` rejected.

**`InvestmentDecision`**
- valid construction with all three required analyst roles present exactly
  once each;
- duplicate role in `agent_reports` (e.g. two `TECHNICAL_ANALYST` reports)
  rejected;
- missing role in `agent_reports` (only two of three roles present)
  rejected;
- unexpected/extra role — not applicable as a distinct case beyond
  duplicate/missing, since any four-role scenario necessarily either
  duplicates a role or cannot occur (`CommitteeRole` has only four members
  and `COMMITTEE_CHAIR` is already rejected by `AgentReport` itself);
- a report in `agent_reports` for a different `Asset` than
  `InvestmentDecision.asset` rejected;
- a `dissenting_opinions` entry not present in `agent_reports` rejected;
- a `dissenting_opinions` entry that is present in `agent_reports` accepted;
- duplicate entries in `dissenting_opinions` (same report/role listed twice)
  rejected;
- empty `dissenting_opinions` accepted (unanimous case);
- `agent_reports` supplied as a `list` is stored as a `tuple`;
- `dissenting_opinions` supplied as a `list` is stored as a `tuple`;
- mutating the original `list`s passed as `agent_reports` and
  `dissenting_opinions` after construction does not change the constructed
  `InvestmentDecision`;
- two `RiskItem`s with the same `(key, source_role)` pair rejected;
- two `RiskItem`s with the same `key` but different `source_role` accepted;
- `risks` supplied as a `list` is stored as a `tuple`;
- mutating the original `list` passed as `risks` after construction does not
  change the constructed `InvestmentDecision`;
- empty `risks` accepted;
- confidence below `Decimal("0")` rejected;
- confidence above `Decimal("1")` rejected;
- confidence exactly `Decimal("0")` and exactly `Decimal("1")` accepted;
- blank `summary` rejected;
- timezone-naive `generated_at` rejected.

**`InvestmentAction`**
- `InvestmentAction is RecommendationAction` (confirms the re-export, not a
  parallel enum).

`tests/committee/test_package_imports.py` (or a `test___init__.py`): every
name in `committee.__all__` (`CommitteeRole`, `ANALYST_ROLES`, `AgentStance`,
`InvestmentAction`, `EvidenceItem`, `AgentReport`, `RiskSeverity`,
`RiskItem`, `InvestmentDecision`) is importable from
`investment_committee.committee`.

## Acceptance criteria

- `src/investment_committee/committee/models.py` defines `CommitteeRole`,
  `ANALYST_ROLES`, `AgentStance`, `InvestmentAction`, `EvidenceItem`,
  `AgentReport`, `RiskSeverity`, `RiskItem`, `InvestmentDecision` exactly as
  specified above, with `AgentReport` and `InvestmentDecision` each having
  all eight fields listed in "Exact field lists."
- `src/investment_committee/committee/__init__.py` re-exports all nine names
  above via `__all__`.
- `src/investment_committee/committee/_validation.py` exists as a local
  helper and is not imported from `domain/_validation.py`.
- All four dataclasses are frozen; every sequence-typed field
  (`AgentReport.evidence`, `InvestmentDecision.agent_reports`,
  `InvestmentDecision.dissenting_opinions`, `InvestmentDecision.risks`) is a
  `tuple` after construction regardless of the input type, and no
  constructed instance retains a reference to a caller-supplied mutable
  `list`.
- The following validation rules are enforced and raise `ValueError` with a
  message that names the failing field/rule:
  - `AgentReport.role` rejects `COMMITTEE_CHAIR`.
  - `AgentReport.confidence` and `InvestmentDecision.confidence` reject
    values outside `[0, 1]` and accept the boundary values `Decimal("0")`
    and `Decimal("1")`.
  - `AgentReport.summary` and `AgentReport.reasoning` reject blank strings.
  - `InvestmentDecision.summary` rejects blank strings.
  - `AgentReport.generated_at` and `InvestmentDecision.generated_at` reject
    timezone-naive datetimes.
  - `InvestmentDecision.agent_reports` rejects duplicate roles, missing
    roles, and reports whose `asset` differs from `InvestmentDecision.asset`.
  - `InvestmentDecision.dissenting_opinions` rejects entries absent from
    `agent_reports` and rejects duplicate entries.
  - `RiskItem.source_role` rejects `COMMITTEE_CHAIR`; `RiskItem.key` and
    `RiskItem.description` reject blank strings.
  - `InvestmentDecision.risks` rejects duplicate `(key, source_role)` pairs.
- `pytest` passes, including all new tests under `tests/committee/`.
- No import of `market_intelligence`, `memory`, `evaluation`, `interfaces`,
  or `integrations` anywhere in `committee/`.
- No pandas, numpy, network calls, LLM SDKs, or floats anywhere in
  `committee/`.
- `docs/architecture.md` and `docs/roadmap.md` are left untouched by this
  Epic (any updates to reflect `committee/` landing are a separate,
  deliberate follow-up, not part of this spec).

## Approved decisions

The following were proposed in the prior revision of this spec and are now
approved for implementation:

- **D1 — `InvestmentAction` is a re-export of `domain.RecommendationAction`**,
  not a second BUY/HOLD/SELL enum. `committee/models.py` imports it as
  `from investment_committee.domain import RecommendationAction as
  InvestmentAction`. No changes to `domain/recommendations.py`.
- **D2 — `AgentReport` hard-rejects `CommitteeRole.COMMITTEE_CHAIR`.**
  `AgentReport.__post_init__` raises `ValueError` if `role` is not in
  `ANALYST_ROLES`. If a future epic needs the chair to produce its own
  structured deliberation record, that should be a distinct type (e.g.
  `ChairDeliberation`), not a relaxed `AgentReport`.
- **D3 — `dissenting_opinions` is explicitly selected by the chair, never
  inferred from `AgentStance`.** `InvestmentDecision.__post_init__` only
  enforces that whatever is selected (a) is present in `agent_reports`
  (traceability) and (b) contains no duplicate entries (each analyst role
  appears at most once). No field of `AgentReport` (including `stance`)
  triggers automatic inclusion in or exclusion from
  `dissenting_opinions`.
- **D4 — `committee/_validation.py` duplicates the four-line
  `require_timezone_aware` helper** from `domain/_validation.py` rather than
  importing it. This Epic does not refactor shared validation across
  packages; `domain/_validation.py`'s leading underscore signals it is
  private to `domain`, and every other cross-package reference in this
  codebase goes through a package's public `__init__.py`.

## Unresolved questions

Not required for this Epic's implementation, but noted for future epics:

1. **Whether `Recommendation` (domain) and `InvestmentDecision` (committee)
   are both meant to exist long-term, and if so what distinguishes them.**
   The field overlap (asset, action/confidence/summary, timestamp) is large.
   D1 avoids making this worse by not duplicating the action enum, but does
   not resolve it.
2. **No cross-timestamp ordering is enforced** between
   `InvestmentDecision.generated_at` and its `agent_reports`' individual
   `generated_at` values (e.g. the decision is not required to be generated
   after all of its reports). Deferred as a possible future invariant once
   there is a real orchestration flow to reason about; enforcing it now
   would be speculative.
