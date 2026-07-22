# ADR-001: Deterministic Committee as the Initial Baseline

## Status

Accepted

## Context

Investment Committee is designed to eventually support three kinds of
investment committees: deterministic analysts, LLM-based analysts, and
hybrid committees that mix the two. All three must produce the same
`InvestmentDecision` shape and be swappable behind the same `Analyst`
interface, so the system can compare them fairly later.

That comparison is the problem this ADR addresses. An LLM-based committee
cannot be judged in isolation — "the LLM recommended BUY and the stock went
up" proves nothing without something to compare it against. Without a
stable baseline, there is no way to tell whether an LLM analyst adds
measurable value over simple, explicit rules, or whether its apparent edge
is noise, overfitting, or an artifact of the test period.

Before any strategy — deterministic, LLM, or hybrid — is trusted with
paper trading, let alone real capital, the system must prioritize:

- **Reproducibility** — the same input must always produce the same output.
- **Explainability** — a human must be able to trace a decision back to the
  evidence that produced it, without reverse-engineering a model.
- **Testability** — decision logic must be exercisable with fixed inputs
  and asserted against exact expected outputs.
- **Historical evaluation** — recorded decisions must be scoreable against
  real market outcomes after the fact.
- **Avoidance of look-ahead bias** — evaluation must only use market data
  from after a decision was made, never data that would leak future
  information into it.
- **Strategy comparison before real-money execution** — no strategy earns
  broker access until it has been measured against alternatives on
  identical historical data.

A first strategy that is not reproducible or explainable would make every
one of these goals unreachable, regardless of which analyst implementation
comes next.

## Decision

The first complete Investment Committee implementation is deterministic.

The deterministic committee:

- consumes one `IndicatorSnapshot`;
- applies explicit rule checklists (per-rule PASS/FAIL/N-A, never a
  weighted or averaged score) in each analyst;
- produces structured `AgentReport` objects with evidence tied to specific
  rules and indicator values;
- aggregates those reports through a deterministic Committee Chair, using
  an explicit decision matrix and fixed threshold rules rather than a
  learned or weighted model;
- returns one immutable `InvestmentDecision`;
- does not use LLMs, prompts, network calls, persistence, portfolio
  execution, or broker integrations (see
  `docs/specs/spec-003-deterministic-committee-engine.md`).

This implementation is the reference baseline every future strategy —
LLM-based or hybrid — is measured against, not a placeholder to be
discarded once a "real" strategy exists.

## Rationale

A deterministic rule checklist, rather than a model of any kind, is what
makes the properties in "Context" achievable on day one:

- **Stable outputs for identical inputs** — the same `IndicatorSnapshot`
  always produces the same `InvestmentDecision`; there is no sampling
  temperature, prompt drift, or model-version dependency to control for.
- **Auditable decisions** — every stance and every action traces to a named
  rule, a specific indicator value, and a specific row in a documented
  table, not to an opaque weight or an LLM's stated reasoning.
- **Reliable regression tests** — rule outcomes, including every boundary
  condition, can be asserted exactly, with no flakiness.
- **A benchmark for LLM and hybrid committees** — an LLM analyst's stance
  and confidence can be compared, decision by decision, against what an
  explicit rule would have concluded from the same data.
- **A safer foundation for paper trading and performance evaluation** —
  before any strategy risks even simulated capital, its behavior must
  already be fully understood and predictable.

Deterministic confidence values are **rule-strength scores**, not
calibrated probabilities: `0.75` means "one applicable rule disagreed with
the rest," not "there is a 75% chance this is correct." No statistical
calibration is claimed anywhere in this Epic, and none should be inferred
until a future Epic explicitly calibrates these scores against recorded
outcomes.

## Consequences

**Positive:**

- Recommendations are reproducible across runs, machines, and time.
- Every decision can be traced to explicit evidence, satisfying the
  auditability this baseline exists to provide.
- Future strategy changes — new rules, new thresholds, an LLM analyst — can
  be measured objectively against this fixed reference point.
- LLM components can be added later behind the same `Analyst` interface
  with no change to `DeterministicCommitteeEngine`, the Chair, or any
  domain model.
- Historical comparisons between strategies remain meaningful, because the
  baseline they are compared against does not silently change underneath
  them.

**Trade-offs:**

- The initial strategy is simplistic by design — a handful of checklist
  rules per analyst, not a sophisticated model of market behavior.
- Thresholds (e.g. RSI bands, volatility bands, confidence buckets) are
  documented but not yet calibrated against real historical outcomes.
- Deterministic rules may fail, or say nothing useful, in market regimes
  the chosen indicators do not represent well.
- This baseline is not expected to be the final or best-performing
  strategy — it is expected to be the strategy everything else has to beat.

## Future Evolution

Later Epics are expected to introduce LLM Analyst adapters, hybrid
committees that mix deterministic and LLM analysts, a Recommendation
Ledger for immutable historical storage, paper portfolio simulation,
performance evaluation, a historical strategy comparison framework, and —
only after that validation — optional, explicitly gated broker integration.

None of these may silently replace or mutate the deterministic baseline
defined here. Every future strategy must be versioned and evaluated
against this baseline using the same historical inputs and the same future
outcome windows, so that any claim of improvement is measured, not assumed.
