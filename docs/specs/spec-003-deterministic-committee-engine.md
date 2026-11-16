# Spec 003 - Deterministic Committee Engine

## Objective

Define deterministic analyst components and a committee engine that consume
one `IndicatorSnapshot` and produce one fully traceable `InvestmentDecision`,
using only the domain contracts already defined in
`docs/specs/spec-002-investment-committee-domain.md`
(`committee/models.py`) and the indicators already defined in
`docs/specs/spec-001-indicator-engine.md` (`indicators/models.py`).

This is the first *behavior* added to `committee/` — spec-002 defined shapes
only. Everything in this spec is deterministic Python: no LLM, no prompt, no
network access, no persistence, no portfolio execution.

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

## Product objective — this is a baseline, not a strategy

The deterministic committee is **not** intended to maximize returns and is
**not** the project's final investment strategy. Its purpose is to be a
**transparent, reproducible, explainable baseline**: every value it reads,
every rule it checks, and every decision it reaches must be traceable by a
human without reverse-engineering a formula.

That baseline is what makes the next Epics possible. Once recommendations
are recorded (Phase 2) and evaluated against real market outcomes (Phase 5),
the project will run the **same historical market data** through:

- this Deterministic Committee,
- a future LLM Committee, and
- a future Hybrid Committee,

and compare them. A comparison like that is only meaningful if the
deterministic baseline's *reasoning*, not just its output, can be fully
audited — if a `BUY` cannot be explained by pointing at exactly which rules
passed and why, there is nothing concrete to compare an LLM agent's
reasoning against. Every design choice in this spec is made in service of
that auditability, explicitly ahead of numeric sophistication or return
performance.

## Non-goals

- No LLM integration, prompts, or prompt text — every rule below is plain
  Python control flow over `Decimal` values and fixed evidence-key strings.
- No network access, no calls to `market_intelligence` or `integrations`.
- No persistence, no recommendation ledger, no `memory/` writes.
- No paper portfolio, no simulation, no broker integration, no Telegram.
- No changes to `domain/`, `indicators/`, or `committee/models.py`. If a
  blocking model gap is found, it is recorded under "Unresolved questions,"
  not silently patched.
- No new external dependencies. Standard library only (`dataclasses`,
  `datetime`, `decimal`, `enum`, `typing`, `abc`). No pandas, no numpy, no
  floats anywhere.
- **No weighted-average scoring, no continuous composite scores, no
  coverage/strength blend formulas.** Every stance, confidence, and action
  in this spec is produced by an explicit rule checklist or lookup table,
  never by summing or averaging numeric votes. This is a deliberate
  reversal of an earlier draft of this spec — see "Revision history."
- No calibration claim: confidence values are a **deterministic rule
  strength score**, not a statistically calibrated probability. This
  wording is used throughout instead of "probability" or "likelihood."

## Revision history

This spec was revised before implementation began (no code exists yet for
this Epic). The original draft scored analysts using averaged ±1/0/-1 votes
and combined them at the chair with a confidence-weighted composite score.
That approach was rejected: it produced numeric precision (e.g. a
`composite_score` of `0.34`) that could not be justified rule-by-rule and
made "why did the committee choose BUY" require reverse-engineering an
equation instead of reading a table. This revision replaces every scoring
formula with an explicit rule checklist (analysts) and an explicit decision
matrix (chair) — see sections below. The overall package structure,
`Analyst` ABC, injected clock, stateless analysts, dependency injection, and
`DeterministicCommitteeEngine` design are unchanged from the original draft.

## Architecture boundaries

Per `docs/architecture.md`, `committee/` depends on `domain/` and
`indicators/`, never the reverse. Concretely for this Epic:

- `committee/analysts/*.py`, `committee/chair.py`, and `committee/engine.py`
  import from `investment_committee.domain`, `investment_committee.indicators`,
  and `investment_committee.committee` (the existing `models.py` /
  `_validation.py`) only.
- No import of `market_intelligence`, `memory`, `evaluation`, `interfaces`,
  or `integrations` anywhere in this Epic's code.
- `indicators/` and `domain/` gain no new dependency on `committee/`.

## Package structure

```
src/investment_committee/committee/
    __init__.py          # updated: re-exports the new public names below
    models.py             # unchanged (spec-002)
    _validation.py         # unchanged (spec-002)
    errors.py              # NEW — CommitteeEngineError
    analysts/
        __init__.py         # re-exports Analyst and the three Deterministic* analysts
        base.py              # Analyst ABC
        technical.py         # DeterministicTechnicalAnalyst
        risk.py              # DeterministicRiskManager
        devils_advocate.py   # DeterministicDevilsAdvocate
    chair.py                # DeterministicCommitteeChair + decision matrix + risk registry
    engine.py               # DeterministicCommitteeEngine + Clock type alias
```

One addition to the layout proposed in the Epic prompt: **`committee/errors.py`**.
`chair.py` and `engine.py` both raise the same orchestration-level exception,
and `engine.py` already depends on `chair.py`, so the exception cannot live
in either file without a cycle. See D8.

### Public API — `committee/__init__.py`

```python
__all__ = [
    # existing (spec-002, unchanged)
    "CommitteeRole", "ANALYST_ROLES", "AgentStance", "InvestmentAction",
    "EvidenceItem", "AgentReport", "RiskSeverity", "RiskItem", "InvestmentDecision",
    # new (this spec)
    "Analyst", "DeterministicTechnicalAnalyst", "DeterministicRiskManager",
    "DeterministicDevilsAdvocate", "DeterministicCommitteeChair",
    "DeterministicCommitteeEngine", "CommitteeEngineError", "Clock",
]
```

The public class names are unchanged from the original draft — this
revision changes what happens *inside* `analyze()` and `decide()`, not the
package layout or public surface.

## 1. Analyst interface

### Choice: `abc.ABC`, not `typing.Protocol` — unchanged

Matches the existing `MarketDataProvider(ABC)` precedent in
`market_intelligence/providers/base.py`. `ABC` gives the engine a real
`isinstance` check and an inspectable `role` class attribute, which its
wiring validation depends on (section 7). See D1.

### Public API — `committee/analysts/base.py`

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import ClassVar

from investment_committee.committee import AgentReport, CommitteeRole
from investment_committee.indicators import IndicatorSnapshot


class Analyst(ABC):
    """One committee seat that turns an IndicatorSnapshot into an AgentReport."""

    role: ClassVar[CommitteeRole]

    @abstractmethod
    def analyze(self, snapshot: IndicatorSnapshot, generated_at: datetime) -> AgentReport:
        """Return this analyst's AgentReport for `snapshot`.

        `generated_at` is supplied by the caller (the engine) — see "Time
        handling." The returned AgentReport.role must equal this analyst's
        `role` class attribute and AgentReport.asset must equal
        `snapshot.asset`; the engine verifies both after calling this method.
        """
        raise NotImplementedError
```

Each concrete analyst is a **stateless, no-argument-constructor** class.
There is nothing to inject at construction time: every rule threshold below
is a module-level `Decimal` constant, not runtime configuration.

## Shared analyst conventions

All three analysts follow the same two-part pattern, described once here:

1. Evaluate a small, fixed **rule checklist** against the `IndicatorSnapshot`.
   Each rule independently resolves to exactly one of **PASS**, **FAIL**, or
   **N/A** (required indicator(s) missing — insufficient history).
2. Look up **stance** and **confidence** from the checklist's PASS/FAIL/N-A
   counts using an explicit table — never a weighted sum, never a division
   used as a "score." Confidence is always one of a small fixed set of
   `Decimal` values (never a computed intermediate value).

### The Rule Strength Table (shared by Technical Analyst and Risk Manager)

Both analysts' rules share one property that makes one shared confidence
table valid for both: **PASS always means the same fixed thing regardless
of the eventual stance** (a rule's condition either held or it didn't — its
meaning does not depend on which way the overall stance ends up leaning).
`DeterministicDevilsAdvocate` does not share this property (its "PASS" means
"no concern," which is deliberately not framed as bullish/bearish) and gets
its own table in section 4.

Let `N` be an analyst's fixed rule count, `passed`/`failed`/`na` the counts
of each outcome among those `N` rules (`passed + failed + na == N`), and
`applicable = passed + failed`.

Evaluated top to bottom; the **first** matching row applies:

| # | Condition | Confidence | Reading |
|---|---|---|---|
| 1 | `applicable == 0` | `Decimal("0.00")` | No rule could be evaluated — no basis for any conclusion |
| 2 | `failed == 0 and na == 0` | `Decimal("0.90")` | Every rule was evaluated and every one passed |
| 3 | `failed == 0 and na > 0` | `Decimal("0.75")` | Every evaluable rule passed, but coverage was partial |
| 4 | `failed == 1` | `Decimal("0.75")` | Exactly one rule failed |
| 5 | `failed == 2` | `Decimal("0.60")` | Exactly two rules failed |
| 6 | `failed >= 3 and passed >= failed` | `Decimal("0.50")` | Several rules failed, but at least as many passed — genuinely mixed |
| 7 | `0 < passed < failed` | `Decimal("0.35")` | More rules failed than passed — weak evidence for the stance taken |
| 8 | `passed == 0` (and `applicable > 0`) | `Decimal("0.20")` | Every evaluable rule failed |

Rows 1–8 are exhaustive over every possible `(passed, failed, na)` combination
for any `N` — this is verified in the test plan (section "Test plan") by
exercising every row directly, independent of `N`. Confidence is a single
lookup, never an equation.

### Decimal precision

Confidence values are always one of the eight literal constants above (or a
value from the tables in sections 3 and 6.6) — there is nothing to round,
because nothing here is computed by division or multiplication. This is a
deliberate property of the design: no `Decimal.quantize` call appears
anywhere in `committee/analysts/` or `committee/chair.py`.

### Evidence keys are a closed vocabulary, not free text

Every `EvidenceItem.key` emitted by any rule is one literal string,
enumerated in that rule's row below. No key is built by interpolating a
runtime value. This is what makes the chair's risk-extraction step (section
6.4) a dictionary lookup instead of string parsing.

### Missing-data behavior (shared)

- No analyst ever raises an exception because an individual indicator is
  `None`. A rule with `None` required input resolves to **N/A**, is excluded
  from `passed`/`failed`/`applicable`, and emits one evidence item with key
  `"<rule_key>_unavailable"` and `value="insufficient_history"` instead of
  its normal evidence item.
- Every analyst always emits at least one evidence item, even when every
  rule is N/A.
- When every rule is N/A (`applicable == 0`), Rule Strength Table row 1
  applies: `confidence == Decimal("0.00")`. Combined with each analyst's
  stance table below, this always resolves to `NEUTRAL` — no special case
  needed, it falls out of the same tables used for every other input.
- Analysis always proceeds with partial data; there is no threshold below
  which an analyst refuses to produce an `AgentReport`.

## 2. `DeterministicTechnicalAnalyst`

`role = CommitteeRole.TECHNICAL_ANALYST`. Consumes only `IndicatorSnapshot`.
`N = 7` rules, grouped exactly as illustrated in the Epic revision request:

| Rule | Group | Required field(s) | PASS when | FAIL when |
|---|---|---|---|---|
| **T1** — Price above SMA20 | Trend | `latest_close`, `sma_20` | `latest_close > sma_20` | `latest_close <= sma_20` |
| **T2** — SMA20 above SMA50 | Trend | `sma_20`, `sma_50` | `sma_20 > sma_50` | `sma_20 <= sma_50` |
| **T3** — SMA50 above SMA200 | Trend | `sma_50`, `sma_200` | `sma_50 > sma_200` | `sma_50 <= sma_200` |
| **M1** — RSI14 in healthy bullish band | Momentum | `rsi_14` | `Decimal("45") <= rsi_14 <= Decimal("70")` | `rsi_14 < Decimal("45")` or `rsi_14 > Decimal("70")` |
| **P1** — Return30 positive | Performance | `return_30d` | `return_30d > 0` | `return_30d <= 0` |
| **P2** — Return90 positive | Performance | `return_90d` | `return_90d > 0` | `return_90d <= 0` |
| **P3** — ReturnYTD positive | Performance | `return_ytd` | `return_ytd > 0` | `return_ytd <= 0` |

Any rule whose required field(s) are `None` is **N/A**, not FAIL —
`sma_200`/`return_90d`/`return_ytd` are the ones realistically absent on
short histories. `M1` deliberately does not distinguish "overbought" from
"oversold" the way `DeterministicDevilsAdvocate` does — for the Technical
Analyst it is a single healthy-momentum checkbox; overextension nuance is
`DeterministicDevilsAdvocate`'s job (section 4), not duplicated here.

### Stance table

| Condition | Stance |
|---|---|
| `applicable == 0` | `NEUTRAL` |
| `failed == 0` (every applicable rule passed) | `SUPPORT` |
| `failed == 1 and applicable >= 4` | `SUPPORT` — one dissenting rule, out of a meaningfully sized checklist |
| `passed == 0` (every applicable rule failed) | `OPPOSE` |
| `passed == 1 and applicable >= 4` | `OPPOSE` — one dissenting rule |
| otherwise | `NEUTRAL` |

`SUPPORT`/`OPPOSE` require near-unanimous agreement (at most one dissenting
rule among at least 4 evaluated) — a deliberate, higher bar than "more
passed than failed," so a bare 5-passed/2-failed split lands `NEUTRAL`
rather than `SUPPORT`. This directly replaces what the rejected
weighted-average design tried to achieve with a `0.4` score threshold, now
expressed as a rule count instead of an averaged number.

`confidence` uses the Rule Strength Table with `N = 7`.

### Evidence

Each applicable rule emits one `EvidenceItem`: `key` = the rule's id in
lowercase (`price_above_sma20`, `sma20_above_sma50`, `sma50_above_sma200`,
`rsi14_healthy_band`, `return_30d_positive`, `return_90d_positive`,
`return_ytd_positive`), `value` = the relevant `Decimal` reading, and
`interpretation` states PASS/FAIL in plain language (e.g. `"PASS — price is
above its 20-day average"`). Each N/A rule emits `"<key>_unavailable"` /
`value="insufficient_history"`. One additional evidence item, key
`"checklist_coverage"`, `value` = the **string** `f"{applicable}/7"` (not a
computed ratio — see "Decimal precision"), `interpretation` = e.g.
`"5 of 7 technical rules were evaluable"`.

### Reasoning (deterministic template)

`reasoning` is built from a fixed template that lists every rule and its
outcome, then states the exact stance rule that matched, e.g.:

```
"Technical rules: T1 Price>SMA20 PASS, T2 SMA20>SMA50 PASS, T3 SMA50>SMA200 FAIL, "
"M1 RSI healthy band PASS, P1 Return30 PASS, P2 Return90 PASS, P3 ReturnYTD N/A "
"(insufficient history). 6 of 7 rules evaluable; 5 passed, 1 failed. "
"Stance is SUPPORT because at most one applicable rule failed."
```

This is string formatting over the already-computed PASS/FAIL/N-A list, not
free text generation — every sentence is a fixed template filled with
already-known values, fully reproducible from the same `IndicatorSnapshot`.

## 3. `DeterministicRiskManager`

`role = CommitteeRole.RISK_MANAGER`. Consumes only `IndicatorSnapshot`.
Stance semantics: `SUPPORT` = the risk profile supports taking a position,
`OPPOSE` = it does not, `NEUTRAL` = mixed. `N = 4` rules.

Each rule has **two independent classifications**, and the distinction
matters throughout this spec:

- a **checklist gate** (PASS/FAIL/N-A) — a single boolean threshold at the
  boundary between "acceptable" and "the worst band," used for this
  analyst's own stance/confidence exactly like the Technical Analyst's
  rules;
- an **evidence band** (e.g. low/moderate/high) — a finer three-way
  classification of the same measurement, always recorded as a distinct
  evidence key regardless of the checklist gate's PASS/FAIL outcome, and
  used later by the chair's risk-extraction registry (section 6.4) to
  produce `RiskItem`s (including `MEDIUM`-severity risks that did not fail
  the checklist gate at all).

This keeps the checklist itself simple and auditable (four yes/no
questions) while still preserving the finer risk-severity detail the chair
needs — the two classifications are computed from the same comparison, not
two separate lookups.

| Rule | Required field | PASS (gate) | FAIL (gate) | Evidence bands (always recorded) |
|---|---|---|---|---|
| **R1** — Volatility contained | `annualized_volatility` | `< Decimal("0.40")` | `>= Decimal("0.40")` | `annualized_volatility_low` (`< 0.20`) / `_moderate` (`0.20`–`0.40`) / `_high` (`>= 0.40`) |
| **R2** — Drawdown contained | `maximum_drawdown` | `> Decimal("-0.30")` | `<= Decimal("-0.30")` | `maximum_drawdown_shallow` (`> -0.10`) / `_moderate` (`-0.30`–`-0.10`) / `_severe` (`<= -0.30`) |
| **R3** — RSI stable | `rsi_14` | `Decimal("30") <= rsi_14 <= Decimal("70")` | otherwise | `rsi_14_stable` / `rsi_14_extreme` (binary — no third band) |
| **R4** — Short-term move contained | `return_30d` | `abs(return_30d) <= Decimal("0.25")` | `abs(return_30d) > Decimal("0.25")` | `return_30d_contained` (`<= 0.10`) / `_elevated` (`0.10`–`0.25`) / `_extreme` (`> 0.25`) |

"Return behavior" (Epic requirement) is interpreted as `return_30d`
magnitude — a short-term realized-move proxy distinct from
`annualized_volatility`'s longer trailing window. See D6.

### Stance table — identical shape to the Technical Analyst's

| Condition | Stance |
|---|---|
| `applicable == 0` | `NEUTRAL` |
| `failed == 0` | `SUPPORT` |
| `failed == 1 and applicable >= 4` | `SUPPORT` |
| `passed == 0` | `OPPOSE` |
| `passed == 1 and applicable >= 4` | `OPPOSE` |
| otherwise | `NEUTRAL` |

With `N = 4`, the "one dissenting rule still SUPPORT/OPPOSE" row only
applies at full coverage (`applicable == 4`) — appropriately stricter than
the Technical Analyst's `N = 7` under partial data.

`confidence` uses the same Rule Strength Table, `N = 4`.

### Evidence

Each rule emits **one evidence item per band** it belongs to (e.g. R1 at
`annualized_volatility = Decimal("0.25")` emits key
`"annualized_volatility_moderate"`, `value = Decimal("0.25")`,
`interpretation = "moderate annualized volatility"`), regardless of whether
the checklist gate PASSed. N/A rules emit `"<rule>_unavailable"` as before
(e.g. `"annualized_volatility_unavailable"`). Plus `"checklist_coverage"`
(`value = f"{applicable}/4"`) as a string, same convention as section 2.

`reasoning` uses the same template style as the Technical Analyst,
additionally naming the evidence band alongside each rule's PASS/FAIL gate,
e.g. `"R1 Volatility contained: PASS (moderate band, 0.25)"`.

## 4. `DeterministicDevilsAdvocate`

`role = CommitteeRole.DEVILS_ADVOCATE`. **Consumes only `IndicatorSnapshot`
— never the Technical Analyst's or Risk Manager's `AgentReport`.** This
decision is unchanged from the original draft; the rationale is restated
and expanded here per the Epic revision request.

### Why indicator-only, expanded

- **Interface uniformity.** `Analyst.analyze(snapshot, generated_at)` is one
  signature for all three seats. Giving Devil's Advocate a second, richer
  input type would mean either a different method signature for one analyst
  (breaking the interface's uniformity) or an optional parameter the other
  two silently ignore (a worse interface — it would compile but lie about
  what the class actually needs).
- **Parallel execution.** With a uniform interface and no inter-analyst
  input, the engine can call all three analysts in any order — including
  concurrently, if that is ever useful — without Devil's Advocate depending
  on the other two having already run. A peer-report-consuming design would
  force a strict two-phase execution order (Technical + Risk first, then
  Devil's Advocate), which the engine's orchestration would need to encode
  and every test would need to respect.
- **No cycle, structurally, not just by convention.** Restricting every
  analyst to `IndicatorSnapshot`-only input makes an analyst-to-analyst
  dependency cycle impossible to introduce by accident — there is no
  `AgentReport` parameter anywhere in the `Analyst` interface for a future
  change to wire backwards.
- **The signal is already there.** Every counterargument Devil's Advocate
  needs to raise — conflicting signals, overextension, weak trend
  confirmation, missing data — is derivable directly from the same
  `IndicatorSnapshot` the other two analysts read. It does not need the
  Technical Analyst's *conclusion* that price is above its 200-day average
  to notice that fact itself; the raw indicator is right there.
- **Reconciliation is the Chair's job, not Devil's Advocate's.** The Chair
  already receives all three reports and is explicitly responsible for
  reconciling disagreement between them (section 6). Having Devil's
  Advocate also compare itself against the other two would duplicate that
  responsibility in a second place with a second, less complete set of
  rules.

Its stance is **not** "always OPPOSE" — `SUPPORT` means no credible
counterargument was found under these rules; `OPPOSE` means multiple
credible ones were.

### Checklist (5 checks, each independently applicable or not)

| Check | Applicable when | CLEAR (no concern) when | CONCERN when | Evidence keys |
|---|---|---|---|---|
| **D1** — No overextension | `rsi_14` not `None` | `Decimal("25") <= rsi_14 <= Decimal("75")` | `rsi_14 > 75` (`overextension_overbought`) or `rsi_14 < 25` (`overextension_oversold`) | `overextension_overbought` / `overextension_oversold` / (clear:) `rsi_14_within_range` |
| **D2** — No severe drawdown | `maximum_drawdown` not `None` | `maximum_drawdown >= Decimal("-0.30")` | `maximum_drawdown < Decimal("-0.30")` | `drawdown_concern` / (clear:) `drawdown_contained` |
| **D3** — Trend/momentum aligned | `latest_close`, `sma_200`, `return_30d` all not `None` | neither conflict condition holds | `(latest_close > sma_200 and return_30d < 0)` or `(latest_close < sma_200 and return_30d > 0)` | `trend_momentum_conflict` / (clear:) `trend_momentum_aligned` |
| **D4** — Trend confirmed by moving averages | `sma_20`, `sma_50`, `sma_200` all not `None` | SMAs cleanly ordered ascending or descending | SMAs not cleanly ordered either way | `weak_trend_confirmation` / (clear:) `trend_confirmed` |
| **D5** — Sufficient history | always applicable | fewer than 3 of the 8 named indicators are `None` | 3 or more are `None` | `insufficient_history` / (clear:) `sufficient_history` |

`overextension_overbought`/`_oversold` are mutually exclusive outcomes of
the same D1 check, so D1 always contributes at most one CONCERN.

### Stance table — its own shape, not the shared Rule Strength Table

Devil's Advocate's "PASS" (CLEAR) means "no concern found," which is not a
directional bullish/bearish signal the way Technical's/Risk's PASS is — so
it gets its own small table, keyed directly on `concern_count` (0–5, though
D5's always-applicable nature means `applicable_count` is always `>= 1`):

| `concern_count` | Stance |
|---|---|
| `0` | `SUPPORT` — no credible counterargument found |
| `1` | `NEUTRAL` — one caution flag, not blocking |
| `>= 2` | `OPPOSE` — multiple credible counterarguments |

### Confidence table — also its own, not the shared Rule Strength Table

| Condition | Confidence | Reading |
|---|---|---|
| `concern_count == 0 and applicable_count == 5` | `Decimal("0.90")` | Clean bill of health, full data |
| `concern_count == 0 and applicable_count < 5` | `Decimal("0.75")` | No concerns found, but only partial data was assessable |
| `concern_count == 1` | `Decimal("0.60")` | One credible caution flag |
| `concern_count == 2` | `Decimal("0.50")` | Two caution flags — genuine uncertainty |
| `concern_count >= 3` | `Decimal("0.75")` | Multiple concerns — confidently a real problem |

The jump from `0.50` (two concerns) back up to `0.75` (three or more) is
**deliberate, not a typo**: confidence here measures how strong the
evidence is *for the stance actually taken*, not how good the outlook is.
Two concerns is genuine ambiguity (is this a real problem or noise?);
three or more is a clear, corroborated pattern — the analyst is confident
there is a real issue, which is a different and stronger claim than "the
picture is mixed." This mirrors why a devastated `IndicatorSnapshot` with
almost no history still produces a confident `OPPOSE` (D5 alone firing, at
`concern_count == 1`... in the sparsest possible case only D5 is
applicable, giving `applicable_count == 1`, `concern_count == 1`, so
`Decimal("0.60")` — a fair, moderate confidence in the narrow claim "there
is not enough history to assess this," not a claim about market direction.

### Evidence and reasoning

One evidence item per applicable check, using the CONCERN key when
triggered or the CLEAR key otherwise (`value` = the relevant `Decimal`
reading, or the count of missing indicators for D5). N/A checks emit
`"<check>_unavailable"` (e.g. `"trend_momentum_conflict_unavailable"`).
`reasoning` follows the same template convention as the other two analysts:
list every check and its outcome, then the stance rule that matched.

## 5. `DeterministicCommitteeChair`

`role = CommitteeRole.COMMITTEE_CHAIR`. Consumes exactly the three analyst
`AgentReport`s plus the shared `asset` and `generated_at`. The chair's
decision is produced by **four ordered, independently explainable steps**
— never a weighted sum. Each step is a small lookup or a single threshold
check; a human can point at exactly which step, and which row within it,
produced the final `InvestmentDecision`.

### Public API — `committee/chair.py`

```python
from __future__ import annotations

from datetime import datetime
from typing import Mapping, Sequence

from investment_committee.committee import (
    ANALYST_ROLES, AgentReport, AgentStance, Asset, CommitteeRole,
    InvestmentAction, InvestmentDecision, RiskItem, RiskSeverity,
)

RISK_EVIDENCE_SEVERITY: Mapping[str, RiskSeverity] = { ... }  # section 5.4


class DeterministicCommitteeChair:
    role = CommitteeRole.COMMITTEE_CHAIR

    def decide(
        self,
        asset: Asset,
        reports: Sequence[AgentReport],
        generated_at: datetime,
    ) -> InvestmentDecision:
        raise NotImplementedError
```

### 5.0 Input validation

Before anything else, `decide` checks `{r.role for r in reports} == ANALYST_ROLES`
and `len(reports) == 3`, raising `CommitteeEngineError` otherwise — needed
because every step below indexes reports by role. This does not duplicate
`InvestmentDecision.__post_init__`'s identical check (spec-002); it exists
so the chair fails clearly before its own role-indexed lookups run, rather
than raising a `KeyError` from malformed input.

### 5.1 Step 1 — Decision matrix (stance triple → baseline action)

Let `T`, `R`, `D` be the `AgentStance` of the Technical Analyst, Risk
Manager, and Devil's Advocate reports respectively, each one of `SUPPORT`
(`S`), `NEUTRAL` (`N`), `OPPOSE` (`O`). The baseline action is produced by
exactly two rules, evaluated in order:

```
if T == SUPPORT and R == SUPPORT and D != OPPOSE:
    baseline = BUY
elif T == OPPOSE and R != SUPPORT and D != SUPPORT:
    baseline = SELL
else:
    baseline = HOLD
```

`BUY` requires the Technical Analyst and Risk Manager to **both actively
support** — mere non-objection is not enough — and the Devil's Advocate to
not actively oppose. `SELL` is held to an intentionally lower bar (`R` and
`D` merely must not *contradict* by supporting the position, rather than
both needing to actively oppose) — reducing or avoiding exposure is already
the conservative direction, consistent with `HOLD` being the default under
uncertainty (see D3, revised). This asymmetry is a deliberate design
decision — flagged for approval, see "Design decisions requiring approval."

This procedure is a **complete** deterministic matrix over all 27
`(T, R, D)` combinations — the full table below is generated mechanically
from the two rules above (`S`/`N`/`O` = `SUPPORT`/`NEUTRAL`/`OPPOSE`):

| T | R | D | Baseline | T | R | D | Baseline | T | R | D | Baseline |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S | S | S | **BUY** | N | S | S | HOLD | O | S | S | HOLD |
| S | S | N | **BUY** | N | S | N | HOLD | O | S | N | HOLD |
| S | S | O | HOLD | N | S | O | HOLD | O | S | O | HOLD |
| S | N | S | HOLD | N | N | S | HOLD | O | N | S | HOLD |
| S | N | N | HOLD | N | N | N | HOLD | O | N | N | **SELL** |
| S | N | O | HOLD | N | N | O | HOLD | O | N | O | **SELL** |
| S | O | S | HOLD | N | O | S | HOLD | O | O | S | HOLD |
| S | O | N | HOLD | N | O | N | HOLD | O | O | N | **SELL** |
| S | O | O | HOLD | N | O | O | HOLD | O | O | O | **SELL** |

27 rows: 2 `BUY`, 4 `SELL`, 21 `HOLD`. `BUY` is deliberately the hardest
action to reach (only reached when nobody's stance actively works against
it and both Technical and Risk actively agree); `SELL` is somewhat easier
to reach (4 rows) because it is itself the cautious/conservative action.
This asymmetry is intentional, not an oversight — see D3.

Note the example the Epic revision request gave, "Technical OPPOSE → SELL,"
is **not** unconditional under this table: e.g. `(O, S, *)` is `HOLD`, not
`SELL` — a Risk Manager that actively supports the position pulls the
committee back from selling even against a bearish Technical read. This is
a deliberate interpretation of that example as illustrative shorthand
rather than literal, flagged for approval in "Design decisions requiring
approval."

### 5.2 Step 2 — High-risk downgrade (one level, no upgrade path)

Applied only when the Step 1 baseline is `BUY`:

```
if baseline == BUY and any(risk.severity == RiskSeverity.HIGH for risk in extracted_risks):
    action = HOLD   # downgraded one level: BUY -> HOLD
else:
    action = baseline
```

`extracted_risks` is computed by section 5.4 from all three reports'
evidence. This replaces the earlier draft's separate risk-manager-veto and
devil's-advocate-downgrade rules with **one** evidence-driven rule, matching
the Epic revision request's "high risk should downgrade the recommendation
by one level" framing directly.

There is **no downgrade below `HOLD`** — `SELL` is not further downgraded
by a `HIGH` risk (it is already the risk-reducing action; there is no safer
action below it in this model) — and **no upgrade path exists anywhere in
this spec**: a low- or no-risk reading never turns a `HOLD` into `BUY`, and
never turns a `SELL` into `BUY`, matching the Epic revision request's
explicit instruction.

### 5.3 Step 3 — Confidence floor (safety net, independent of the matrix)

```
if action in (BUY, SELL) and min(t.confidence, r.confidence, d.confidence) < Decimal("0.50"):
    action = HOLD
```

Even when the stance pattern qualifies for `BUY`/`SELL` under the matrix,
one analyst's evidence being too thin (confidence below `0.50` — the Rule
Strength Table's "weak evidence" band or worse) forces `HOLD`. This
preserves, in the new discrete-confidence world, the original design intent
that a minimum confidence is required for `BUY`/`SELL` (previously
expressed as a `Decimal("0.4")` gate on a continuous weighted-average
confidence; here it is a single fixed threshold on the same discrete
per-analyst confidence values already defined in sections 2–4). This step
can, in principle, also turn a `SELL` into `HOLD` — a small addition beyond
the literal Epic revision request, flagged for approval below.

### 5.4 Risk extraction (unchanged approach, restated against the new evidence keys)

`RiskItem` entries are built by scanning every `EvidenceItem` in all three
reports against a fixed registry — never by parsing `summary`/`reasoning`
text:

```python
RISK_EVIDENCE_SEVERITY: Mapping[str, RiskSeverity] = {
    "annualized_volatility_moderate": RiskSeverity.MEDIUM,
    "annualized_volatility_high": RiskSeverity.HIGH,
    "maximum_drawdown_moderate": RiskSeverity.MEDIUM,
    "maximum_drawdown_severe": RiskSeverity.HIGH,
    "rsi_14_extreme": RiskSeverity.MEDIUM,
    "return_30d_extreme": RiskSeverity.MEDIUM,
    "overextension_overbought": RiskSeverity.MEDIUM,
    "overextension_oversold": RiskSeverity.MEDIUM,
    "drawdown_concern": RiskSeverity.HIGH,
    "trend_momentum_conflict": RiskSeverity.MEDIUM,
    "weak_trend_confirmation": RiskSeverity.LOW,
    "insufficient_history": RiskSeverity.LOW,
}
```

Plus one general **suffix rule**: any `EvidenceItem.key` ending in
`"_unavailable"` maps to `RiskSeverity.LOW`, regardless of which analyst or
rule emitted it. This is still a fixed, spec'd, narrow contract (one
literal suffix, agreed between every analyst and the chair), not the
"arbitrary string parsing" the Epic rules out — that phrase refers to
extracting meaning from `summary`/`reasoning` prose, which nothing here
does. `DeterministicTechnicalAnalyst`'s rule-outcome keys (e.g.
`price_above_sma20`) are **not** in the registry and produce no `RiskItem`
— a bearish technical read is a directional opinion, not a risk, in this
model's vocabulary.

Two different analysts flagging the same evidence key independently (e.g.
Risk Manager's `maximum_drawdown_severe` and Devil's Advocate's
`drawdown_concern`, both from the same underlying drawdown value) are kept
as two separate `RiskItem`s — `InvestmentDecision`'s `(key, source_role)`
de-duplication (spec-002) already guarantees no collision, and two analysts
independently corroborating the same concern is meaningful, not noise.

### 5.5 Dissenting opinions

Computed explicitly from the **final** `action` (after steps 1–3), then
passed as `InvestmentDecision.dissenting_opinions` — never inferred inside
`AgentReport` (preserves spec-002 D3):

| Final action | A report dissents when |
|---|---|
| `BUY` | `report.stance == OPPOSE` |
| `SELL` | `report.stance == SUPPORT` |
| `HOLD` | `report.stance != NEUTRAL` |

### 5.6 Decision confidence — discrete, count-based

Let `dissenting_count` be the count from section 5.5 (0–3).

```
if a high-risk downgrade fired in Step 2:
    decision_confidence = Decimal("0.60")
elif a confidence-floor override fired in Step 3:
    decision_confidence = min(t.confidence, r.confidence, d.confidence)
else:
    decision_confidence = {0: Decimal("0.90"), 1: Decimal("0.70"),
                            2: Decimal("0.50"), 3: Decimal("0.30")}[dissenting_count]
```

By construction of the decision matrix (section 5.1), every `BUY` and
`SELL` row has `dissenting_count == 0` — `BUY` requires `T == R == SUPPORT`
and `D != OPPOSE`, so nothing can dissent from a `BUY`; `SELL` requires
`T == OPPOSE`, `R != SUPPORT`, `D != SUPPORT`, so nothing can dissent from
a `SELL` either. This means the `dissenting_count` table's only real work
is distinguishing *flavors of `HOLD`*: a `HOLD` reached because all three
analysts shrugged `NEUTRAL` together (`dissenting_count == 0`, confidence
`0.90` — a clear, unanimous "nothing to act on") is reported as more
confident than a `HOLD` reached because analysts actively disagreed
(`dissenting_count == 2` or `3`, confidence `0.50`/`0.30` — a genuinely
contested call the chair is resolving conservatively, not confidently).
This is a deliberate, explainable ordering — flagged for approval below,
since it is a new judgment call this revision introduces.

### 5.7 Summary

Fixed string template naming the final action, each analyst's stance, and —
when applicable — which step (5.2 downgrade or 5.3 floor) changed the
outcome from the Step 1 baseline, e.g.:

```
"HOLD AAPL: downgraded from BUY (Step 1 baseline) due to a HIGH-severity risk "
"(maximum_drawdown_severe, from Risk Manager). Technical: SUPPORT, Risk: NEUTRAL, "
"Devil's Advocate: SUPPORT. Decision confidence: 0.60."
```

`generated_at` is the value passed in — the chair never calls a clock
itself. The chair never recalculates any indicator; every number it uses
comes from the three `AgentReport`s it was given.

## 6. `DeterministicCommitteeEngine` — unchanged from the original draft

### Public API — `committee/engine.py`

```python
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from investment_committee.committee import InvestmentDecision
from investment_committee.committee.analysts.base import Analyst
from investment_committee.committee.chair import DeterministicCommitteeChair
from investment_committee.indicators import IndicatorSnapshot

Clock = Callable[[], datetime]


class DeterministicCommitteeEngine:
    def __init__(
        self,
        technical_analyst: Analyst,
        risk_manager: Analyst,
        devils_advocate: Analyst,
        chair: DeterministicCommitteeChair,
        clock: Clock = lambda: datetime.now(timezone.utc),
    ) -> None:
        raise NotImplementedError

    def run(self, snapshot: IndicatorSnapshot) -> InvestmentDecision:
        raise NotImplementedError
```

### 6.1 Three named parameters, not a collection

`ANALYST_ROLES` is a fixed, closed set of exactly three roles — not an
open-ended plugin list — so named constructor parameters
(`technical_analyst`, `risk_manager`, `devils_advocate`) are used instead of
a generic `Sequence[Analyst]`. This lets construction validate each slot
against one specific expected role and matches the existing
`MarketIntelligenceService.__init__(self, provider: ... = None)` pattern of
explicit named collaborators.

### 6.2 Construction-time validation (`__init__`)

For each `(parameter, expected_role)` pair: `isinstance(parameter, Analyst)`
and `parameter.role == expected_role`, else `CommitteeEngineError`. This
rejects analysts claiming `COMMITTEE_CHAIR` (never equals any expected
role), duplicate roles (the second slot given the same role fails its own
check), and — since all three parameters are required with no defaults —
makes "missing role" structurally impossible to construct. `chair` is
validated via `isinstance(chair, DeterministicCommitteeChair)`.

### 6.3 Run-time validation (`run`)

```
generated_at = self._clock()          # called exactly once per run()
reports = [analyst.analyze(snapshot, generated_at) for analyst in (technical_analyst, risk_manager, devils_advocate)]
```

For each `(analyst, report)` pair: `report.role == analyst.role` and
`report.asset == snapshot.asset`, else `CommitteeEngineError`. "Malformed or
incomplete outputs" is covered by `AgentReport.__post_init__` itself
(spec-002) — an invalid `AgentReport` raises `ValueError` before the engine
ever sees a return value to check.

```
return self._chair.decide(asset=snapshot.asset, reports=reports, generated_at=generated_at)
```

### 6.4 `committee/errors.py`

```python
class CommitteeEngineError(Exception):
    """Raised when committee orchestration input is structurally invalid.

    Covers analyst/chair wiring problems the engine's constructor and
    run() detect, and the chair's own pre-aggregation role-coverage check.
    Distinct from the plain ValueError every committee/models.py dataclass
    raises for its own shape invariants (spec-002).
    """
```

## 7. Time handling — unchanged

`Clock = Callable[[], datetime]`, default `lambda: datetime.now(timezone.utc)`.
The clock is called **exactly once per `run()`**, and the resulting
timestamp is reused for all three `AgentReport`s and the `InvestmentDecision`.
The clock lives only on the engine — analysts and the chair receive
`generated_at` as a plain parameter. Rationale (unchanged): all four
outputs describe one synchronous, in-memory analysis of one
`IndicatorSnapshot`; a fake clock injected once lets a test assert every
output's timestamp with a single fixture; analysts/chair stay pure
functions of their explicit arguments.

## Error behavior — summary

| Failure | Exception |
|---|---|
| A `committee/models.py` dataclass's own field invariant (spec-002) | `ValueError` (unchanged) |
| Engine constructed with a non-`Analyst` or wrong-role analyst in a slot | `CommitteeEngineError` |
| An analyst's returned report has the wrong `role` or `asset` | `CommitteeEngineError` |
| Chair called with reports that do not cover exactly the three analyst roles | `CommitteeEngineError` |
| `IndicatorSnapshot`/`MarketSnapshot` structurally invalid | `IndicatorCalculationError` (unchanged, spec-001) |

## Dependency injection strategy — unchanged

The engine's four collaborators (`technical_analyst`, `risk_manager`,
`devils_advocate`, `chair`) and its `clock` are all constructor parameters,
no service-locator or global default lookup. Tests construct the engine
with fakes/stubs implementing `Analyst` and a fixed `Clock`. `chair` is
injected too, so a test can substitute a chair stub when testing the
engine's own validation in isolation. Each `Deterministic*` analyst and
`DeterministicCommitteeChair` take no constructor arguments — every
threshold is a fixed module-level constant.

## Test plan

New `tests/committee/analysts/` package plus additions to `tests/committee/`.

**`tests/committee/analysts/test_technical.py`**
- each of the 7 rules individually: PASS, FAIL, and N/A, on a snapshot
  crafted so only that rule's required field(s) are relevant;
- `"<rule>_unavailable"` evidence emitted on N/A;
- the shared Rule Strength Table: all 8 rows exercised directly by
  constructing `(passed, failed, na)` combinations via crafted snapshots,
  independent of which specific rules are involved;
- stance table boundaries: `failed == 1` at `applicable == 4` (SUPPORT) vs.
  `applicable == 3` (falls through to NEUTRAL); mirrored for OPPOSE;
- `applicable == 0` → `NEUTRAL`, `confidence == Decimal("0.00")`;
- `checklist_coverage` evidence item's string value is exactly `f"{applicable}/7"`;
- `role`/`asset`/`generated_at` propagate from the call's inputs.

**`tests/committee/analysts/test_risk.py`** — mirrors the technical test
file for the 4 risk rules, plus: each rule's checklist gate boundary
(exactly `Decimal("0.40")` volatility lands FAIL, not PASS; exactly
`Decimal("-0.30")` drawdown lands FAIL) and, separately, each rule's
evidence-band boundary (exactly `Decimal("0.20")` volatility lands in the
moderate band) — asserting the checklist gate and the evidence band are
independently correct at the same boundary value.

**`tests/committee/analysts/test_devils_advocate.py`**
- each of the 5 checks: applicable-and-clear, applicable-and-concern,
  not-applicable;
- the dedicated stance table at `concern_count` `0`/`1`/`2`+;
- the dedicated confidence table at every row, including the `0.50` → `0.75`
  non-monotonic jump from `concern_count == 2` to `concern_count == 3`;
- confirms `analyze` only reads `IndicatorSnapshot` — no second `AgentReport`
  parameter exists on the method.

**`tests/committee/test_chair.py`**
- **all 27 rows** of the Step 1 decision matrix, asserting the exact
  baseline action for each `(T, R, D)` combination — a direct,
  table-driven test mirroring the spec's own table;
- Step 2: a `HIGH`-severity extracted risk downgrades a matrix `BUY` to
  `HOLD`; a `MEDIUM` or `LOW` risk does not; the downgrade never applies to
  a matrix `HOLD` or `SELL`;
- Step 3: `min(confidence) < 0.50` forces `HOLD` from a matrix `BUY` or
  `SELL`; at exactly `0.50` it does not;
- Step 3 applied to a matrix `SELL` also produces `HOLD` (the explicitly
  flagged extra case — see "Design decisions requiring approval");
- risk extraction: exact-match registry keys produce the right severities;
  the `_unavailable` suffix rule; two different roles flagging the same key
  both survive as distinct `RiskItem`s; a Technical Analyst evidence key
  produces no `RiskItem`;
- dissenting-opinions selection for `BUY`/`HOLD`/`SELL`, including empty
  (unanimous) case;
- decision-confidence table: all four `dissenting_count` values, the Step 2
  downgrade override (`0.60` regardless of `dissenting_count`), and the
  Step 3 floor override (`== min(confidence)`);
- confirms no `BUY`/`SELL` row from the 27-row matrix test ever has
  `dissenting_count > 0` (an assertion over the matrix test's own results,
  proving the section 5.6 claim by construction rather than by narrative);
- reports missing a role, or covering only two roles → `CommitteeEngineError`;
- `InvestmentDecision.generated_at` equals the `generated_at` passed in.

**`tests/committee/test_engine.py`** — unchanged from the original draft's
plan: happy path with fixed clock and all four `generated_at` values equal;
each construction-time rejection case (wrong role per slot, `COMMITTEE_CHAIR`
in an analyst slot, duplicate roles); run-time rejection (mismatched role or
asset on a returned report); clock invoked exactly once per `run()`; no
network access check.

`tests/committee/analysts/__init__.py` added, matching every other test
package's `__init__.py`.

## Acceptance criteria

- `src/investment_committee/committee/analysts/{base,technical,risk,devils_advocate}.py`,
  `chair.py`, `engine.py`, and `errors.py` exist and implement exactly the
  public API in this spec.
- `committee/__init__.py` re-exports the nine new names via `__all__`.
- `Analyst` is an `ABC`; each `Deterministic*` analyst declares its fixed
  `role` `ClassVar` and implements `analyze(snapshot, generated_at) -> AgentReport`.
- **No analyst or chair computation anywhere sums, averages, or divides a
  `Decimal` to derive a stance or a confidence value.** Every stance and
  confidence value is produced by a table lookup or an explicit
  count-comparison (`==`, `<`, `>=`) against a fixed table row — this is
  directly checkable by inspecting `committee/analysts/*.py` and
  `committee/chair.py` for the absence of `/` or `*` between two `Decimal`
  operands outside of the fixed evidence values read straight from the
  `IndicatorSnapshot`.
- Every rule table in this spec (analyst checklists, stance tables, the
  shared Rule Strength Table, Devil's Advocate's own tables, the 27-row
  decision matrix, the downgrade/floor steps, the decision-confidence
  table, the risk registry) is implemented exactly as specified, with the
  exact `Decimal` boundary values given.
- No floats anywhere in `committee/analysts/`, `committee/chair.py`, or
  `committee/engine.py` — `Decimal` only.
- No `EvidenceItem.key` is built by string interpolation of a runtime
  value; every key is one literal from the closed vocabulary in this spec.
- Every `AgentReport.reasoning` names every applicable rule/check and its
  PASS/FAIL/CONCERN/CLEAR outcome, plus which stance-table row matched —
  auditable without reading `committee/analysts/` source.
- `DeterministicCommitteeEngine` rejects every malformed-wiring case (wrong
  role per slot, `COMMITTEE_CHAIR` claimed by an analyst, duplicate roles,
  mismatched report role/asset), raising `CommitteeEngineError`.
- `run()` calls the injected clock exactly once and reuses that single
  `datetime` for all three `AgentReport`s and the `InvestmentDecision`.
- No indicator is ever recalculated by any analyst or the chair.
- `pytest` passes, including all new tests under `tests/committee/analysts/`
  and the additions to `tests/committee/test_chair.py` / `test_engine.py`.
- No import of `market_intelligence`, `memory`, `evaluation`, `interfaces`,
  or `integrations` anywhere in the new code.
- No pandas, numpy, network calls, LLM SDKs, or floats anywhere in the new
  code.
- `committee/models.py` and `committee/_validation.py` are unmodified.
- `docs/architecture.md` and `docs/roadmap.md` are left untouched by this
  Epic.

## Explicit design decisions

- **D1 — `Analyst` is an `ABC`, not a `Protocol`.** Unchanged from the
  original draft. See section 1.
- **D2 — `DeterministicDevilsAdvocate` consumes only `IndicatorSnapshot`,
  never the other two analysts' reports.** Unchanged, rationale expanded
  per the Epic revision request. See section 4.
- **D3 (revised) — The decision matrix's `BUY` and `SELL` conditions are
  intentionally asymmetric**: `BUY` requires both `T == SUPPORT` and
  `R == SUPPORT`; `SELL` only requires `T == OPPOSE` and `R != SUPPORT`
  (not `R == OPPOSE`). Reducing/avoiding exposure is held to a lower bar
  than adding it, consistent with `HOLD`-as-default-under-uncertainty. This
  supersedes the original draft's D3 (confidence-weighted veto rules),
  which no longer exist now that the chair uses no weighted arithmetic.
- **D4 (revised) — the `BUY`/`SELL` minimum-confidence gate (Step 3,
  section 5.3) uses a fixed `Decimal("0.50")` threshold against each
  analyst's discrete confidence value**, replacing the original draft's
  `Decimal("0.4")` gate on a continuous weighted-average confidence. Same
  intent (one uninformative analyst is enough to force `HOLD`), now
  expressed against the new discrete confidence scale.
- **D5 — `analyze(snapshot, generated_at)`, not the literal
  `analyze(snapshot)`.** Unchanged. See section 1 and section 7.
- **D6 — Risk Manager's "return behavior" rule is `return_30d` magnitude.**
  Unchanged. See section 3.
- **D7 — RSI-14 threshold bands intentionally differ across the three
  analysts.** Unchanged (Technical: 45/70 for healthy bullish momentum;
  Risk Manager: 30/70 for stability; Devil's Advocate: 25/75 for
  overextension) — three different interpretive lenses on the same field.
- **D8 — `CommitteeEngineError` lives in `committee/errors.py`.** Unchanged.
- **D9 (new) — Risk Manager's rules each carry a checklist gate (PASS/FAIL,
  for stance/confidence) separate from a finer evidence band (for the risk
  registry).** See section 3. This is new in this revision — the original
  draft's continuous 3-band vote (+1/0/-1) collapsed both purposes into one
  number; splitting them was necessary once the checklist gate had to be a
  clean boolean.
- **D10 (new) — Decision confidence (section 5.6) is a 4-row table keyed on
  `dissenting_count`, with fixed overrides for the Step 2 and Step 3
  paths.** By construction, this table's non-trivial rows only ever apply
  to `HOLD` (every `BUY`/`SELL` row of the matrix has `dissenting_count == 0`)
  — flagged for approval since this emergent property, and the choice to
  rate a unanimous-`NEUTRAL` `HOLD` as *more* confident than a
  contested `HOLD`, are new judgment calls this revision introduces.

## Design decisions requiring approval

These are called out again here, gathered in one place, because they
involve a genuine judgment call this revision made without an unambiguous
instruction to resolve it:

1. **The `SELL` matrix condition (`T == OPPOSE and R != SUPPORT and D != SUPPORT`)
   reads the Epic revision request's "Technical OPPOSE → SELL" example as
   illustrative shorthand, not literal.** Under the approved table, a
   Risk-Manager-`SUPPORT` blocks `SELL` even when Technical is `OPPOSE`
   (falls to `HOLD` instead), mirroring how the request's own third example
   ("Technical SUPPORT, Risk OPPOSE → HOLD") shows Risk capping Technical.
   If "Technical OPPOSE → SELL" was meant unconditionally, the matrix needs
   a different `SELL` rule (e.g. `T == OPPOSE` alone, ignoring `R`/`D`
   entirely) — a materially different, less risk-managed committee.
2. **Step 3's confidence floor can turn a matrix `SELL` into `HOLD`**, which
   was not explicitly requested (the request only discussed the `BUY` case
   and stated `SELL` should never become `BUY`). This is a small, logical
   extension to preserve the original minimum-confidence safety intent
   symmetrically; it could instead be scoped to `BUY` only if a `SELL`
   backed by thin evidence is considered acceptable to keep as-is.
3. **The exact numeric buckets in the Rule Strength Table, the Devil's
   Advocate tables, and the decision-confidence table** (`0.90`/`0.75`/
   `0.60`/`0.50`/`0.35`/`0.20`/`0.00`, `0.70`/`0.30` for dissent) are
   documented-but-illustrative, following the shape of the Epic revision
   request's own example numbers, not derived from any historical data —
   there is no evaluation data yet to calibrate against (Phase 5 is where
   that would eventually happen).
4. **Whether `AgentReport` should eventually carry a structured
   risk-flag field**, rather than the chair inferring `RiskItem`s from a
   closed evidence-key vocabulary agreed by convention (section 5.4). Same
   open question as the original draft — noted again since it is unchanged
   by this revision.

## Unresolved questions

1. Items 2 and 4 in "Design decisions requiring approval" above are the
   two live open questions from this revision.
2. **Whether `DeterministicDevilsAdvocate` should eventually be allowed to
   consume peer reports (reversing D2)**, if evaluation data later shows
   the indicator-only design misses conflicts only visible by directly
   comparing the Technical Analyst's and Risk Manager's conclusions.
   Unchanged from the original draft.
3. **No ordering is enforced, or currently meaningful, between the shared
   `generated_at` and anything else** — mirrors spec-002's own unresolved
   question 2. Unchanged from the original draft.
