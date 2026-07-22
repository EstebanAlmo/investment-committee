# Architecture

Investment Committee is an explainable investment analysis platform: it
produces deterministic and, later, AI-assisted investment recommendations,
records every recommendation immutably, evaluates them against future
market data, and measures whether each strategy actually outperforms
simpler baselines — all before any real capital is ever involved. It is not
an automated trading bot; nothing in this codebase places an order.

The codebase is organized into eight layers. Lower layers know nothing
about higher layers; higher layers depend on lower ones, never the reverse.
Everything external (data providers, LLM providers, brokers, TradingAgents)
sits behind an adapter so it can be replaced without touching domain code.

## Product pipeline

The layers below implement one runtime pipeline. This is the shape of a
single analysis, end to end — distinct from the layer diagram further down,
which describes code dependencies rather than data flow:

```
Market Data
    │
    ▼
Indicator Engine
    │
    ▼
Investment Committee
    │
    ▼
Investment Decision
    │
    ▼
Recommendation Ledger
    │
    ▼
Paper Portfolio
    │
    ▼
Performance Evaluation
    │
    ▼
Strategy Comparison
    │
    ▼
Future Broker Integration
```

| Pipeline stage | Owning package(s) | Status |
|---|---|---|
| Market Data | `market_intelligence/` | Complete (Epic 2) |
| Indicator Engine | `indicators/` | Complete (Epic 3) |
| Investment Committee | `committee/` | Domain complete (Epic 4); deterministic engine in progress (Epic 5, `docs/specs/spec-003-deterministic-committee-engine.md`) |
| Investment Decision | `committee/models.py` (`InvestmentDecision`) | Complete (Epic 4) |
| Recommendation Ledger | `memory/` | Planned (Epic 7) |
| Paper Portfolio | `portfolio/`, `simulation/` | Planned (Epic 8) |
| Performance Evaluation | `evaluation/` | Planned (Epic 9) |
| Strategy Comparison | `evaluation/` | Planned (Epic 13) |
| Future Broker Integration | `integrations/` (adapter) + `portfolio/` | Deferred (Epic 14), optional, only after validation |

`application/` (Epic 6) is not itself a pipeline stage — it is the single
entry point (e.g. `analyze_asset(ticker)`) that drives one full pass through
this pipeline on behalf of every user interface. See "Application Service"
below.

## Product principles

These hold across every future Epic, not just the one currently in
progress. They are the reason several of the layer descriptions below read
differently from earlier drafts of this document.

1. **The deterministic committee is the baseline.** Every future strategy
   — an LLM committee, a hybrid committee, a different rule set — is
   measured against it, using the same historical market data
   (`docs/specs/spec-003-deterministic-committee-engine.md`, "Product
   objective"). Nothing is allowed to claim it "works better" without that
   comparison.
2. **`InvestmentDecision` is immutable historical truth.** Once recorded in
   the Recommendation Ledger (`memory/`), a decision is never recalculated
   using newer market data. Improving indicators or committee rules
   produces a *new* decision on a *new* run; it never mutates a past one.
   This is why `InvestmentDecision` and `AgentReport` are frozen dataclasses
   with no update path (spec-002) — that was already true before this
   principle was written down explicitly.
3. **Evaluation avoids look-ahead bias.** A recorded recommendation is only
   ever scored against market sessions that occurred *after* its
   `generated_at`. The Evaluation Engine (`evaluation/`) must not have
   access to, or accidentally condition on, data from the future relative
   to the recommendation being scored.
4. **User interfaces call the Application Service, never analysts
   directly.** CLI, Telegram, and any future web interface all call
   `application/`'s entry points (e.g. `analyze_asset`); none of them
   construct a `DeterministicCommitteeEngine`, call `market_intelligence`,
   or read `IndicatorSnapshot`s themselves. This is what lets the
   orchestration logic (fetch → compute indicators → run committee →
   record → simulate) live in exactly one place.
5. **LLMs are implementations of analyst interfaces; they never replace
   domain models.** A future LLM-backed analyst is a class implementing
   `committee.analysts.base.Analyst` (spec-003), exactly like
   `DeterministicTechnicalAnalyst`. It returns the same `AgentReport`
   dataclass (spec-002) — an LLM never gets its own parallel report type,
   and no domain model is ever redefined "for the LLM case."
6. **The architecture supports deterministic, LLM, and hybrid analysts
   without changing the surrounding architecture.** `DeterministicCommitteeEngine`
   already takes its three analysts and its chair by dependency injection
   (spec-003, "Dependency injection strategy"); swapping in an LLM-backed
   `Analyst` implementation, or mixing deterministic and LLM analysts in
   one committee (Epic 12), requires no change to `engine.py`, `chair.py`,
   or any domain model — only a new class in `committee/analysts/` (or a
   sibling package) and a new adapter in `integrations/` for the LLM
   provider itself.

## Layers

```
┌─────────────────────────────────────────────────────────────┐
│ 7. User Experience        (interfaces/)                       │
│    Telegram first · dashboard later · reports                 │
│    (a minimal internal CLI ships with 6 for dev/debug use     │
│    only — see "User Experience" below)                        │
└───────────────▲─────────────────────────────────────────────┘
                │ depends on
┌───────────────┴─────────────────────────────────────────────┐
│ 6. Application Service     (application/)                     │
│    single entry point (e.g. analyze_asset()) that drives one  │
│    full pass through the product pipeline above               │
└───────┬─────────────────┬─────────────────┬───────────────────┘
        │ depends on      │ depends on      │ depends on
┌───────▼──────┐  ┌────────▼────────┐  ┌─────▼──────────┐  ┌──────────────┐
│ 3. Investment│  │ 4. Memory and    │  │ 5. Evaluation  │  │ education/   │
│    Committee │  │    Storage       │  │    Engine      │  │ portfolio/   │
│ (committee/) │  │    (memory/)     │  │  (evaluation/) │  │ simulation/  │
└──────┬───────┘  └──────────────────┘  └────────────────┘  └──────────────┘
       │ depends on
┌──────▼───────────────────────────────────────────────────────┐
│ 2. Indicators   (indicators/)                                  │
└──────┬────────────────────────────────────────────────────────┘
       │ depends on
┌──────▼───────────────────────────────────────────────────────┐
│ 1. Market Intelligence   (market_intelligence/)                │
└──────┬────────────────────────────────────────────────────────┘
       │ depends on
┌──────▼───────────────────────────────────────────────────────┐
│ 8. Integrations   (integrations/)                              │
│    TradingAgents adapter · future LLM/broker/data adapters     │
└─────────────────────────────────────────────────────────────┘

Every layer above may also depend on:
┌─────────────────────────────────────────────────────────────┐
│ 0. Domain   (domain/)                                          │
│    Asset · PriceBar · MarketSnapshot · Recommendation          │
└─────────────────────────────────────────────────────────────┘
```

`indicators/` sits directly between `market_intelligence/` and
`committee/`: `market_intelligence` produces a `MarketSnapshot`,
`indicators` turns it into an `IndicatorSnapshot`, and `committee` consumes
that `IndicatorSnapshot` rather than recomputing anything itself.

### 0. Domain — `domain/`

Unchanged. The framework-independent core types every other layer shares:
`Asset`, `PriceBar`, `MarketSnapshot`, and `Recommendation`. Frozen
dataclasses, standard library only, no persistence, no external
dependency.

### 1. Market Intelligence — `market_intelligence/`

Unchanged. Collects, validates, and exposes raw price data as a
`MarketSnapshot`. Has no opinion about what to do with the data; technical
and risk indicator calculation is `indicators/`'s job, not this layer's.

### 2. Indicators — `indicators/`

Unchanged. `IndicatorEngine` performs deterministic, standard-library-only
calculations over a `MarketSnapshot` — moving averages, returns, annualized
volatility, maximum drawdown, RSI — and produces an `IndicatorSnapshot`.
**LLMs must never calculate indicators**: all numeric computation happens
here, so any agent that later reads an `IndicatorSnapshot` only interprets
already-computed numbers.

### 3. Investment Committee — `committee/`

The three analyst roles defined by `CommitteeRole`
(`TECHNICAL_ANALYST`, `RISK_MANAGER`, `DEVILS_ADVOCATE`), each implementing
the `Analyst` interface, reconciled by a deterministic `Chair` into one
`InvestmentDecision` (spec-002, spec-003). `committee/` performs no I/O: it
reads one `IndicatorSnapshot` and returns one `InvestmentDecision`: it does
not write to `memory/`, does not know about `portfolio/` or `simulation/`,
and does not fetch anything itself. Persisting the resulting decision is
the Application Service's job (layer 6), not this layer's — this is a
correction from an earlier draft of this document, which had `committee/`
writing to Memory and Storage directly.

Additional analyst roles (fundamental, macro, news analysts; a bull/bear
researcher pair) and LLM-backed or hybrid analysts (Epics 11–12) extend
this layer by adding new `CommitteeRole` members and new `Analyst`
implementations — not by redesigning it. Per product principle 5, an
LLM-backed analyst is still just a class implementing `Analyst.analyze()`
and returning an `AgentReport`; the LLM API call itself lives behind an
adapter in `integrations/` (see layer 8), the same way `YFinanceProvider`
sits behind `MarketDataProvider` today.

### 4. Memory and Storage — `memory/`

The system of record, centered on the **Recommendation Ledger**: every
`InvestmentDecision` (with its `AgentReport`s) is written here once and
never modified — see product principle 2. Also stores portfolio snapshots,
simulations, prompt versions, model versions, and audit history. SQLite
backs this layer once its schema is stable; for now it defines the
interfaces/data shapes other layers read and write.

### 5. Evaluation Engine — `evaluation/`

Judges recorded decisions after the fact, using only market sessions that
occurred after each decision's `generated_at` (product principle 3):
benchmark comparison, simulated returns, drawdown, volatility, Sharpe and
Sortino ratios, recommendation accuracy, and confidence calibration. Also
owns **Strategy Comparison** (Epic 13): comparing the deterministic
committee against LLM and hybrid committees run over identical historical
data, per product principle 1. Reads from `memory/` and never mutates it.

### 6. Application Service — `application/`

*(planned, Epic 6 — not yet implemented; described here so later layers'
dependency rules are unambiguous before it lands.)*

The single entry point every user interface calls — e.g.
`analyze_asset(ticker) -> InvestmentDecision`. It composes, in order:
`market_intelligence` (fetch), `indicators` (compute), `committee`
(decide), `memory` (record, once Epic 7 lands), and `portfolio`/`simulation`
(once Epic 8 lands). No interface is allowed to skip this layer and call
`committee` or `market_intelligence` directly (product principle 4) — this
is what keeps the pipeline's order enforced in exactly one place instead of
once per interface.

### 7. User Experience — `interfaces/`

Telegram (Epic 10) is the first real, user-facing interface; a dashboard
may follow later. A minimal CLI ships alongside the Application Service
(Epic 6) as a developer/debugging tool for exercising `analyze_asset()`
during development — it is not counted as a product-facing epic in its own
right, and is not the "first interface" in the product sense, but it does
mean a command-line entry point exists before Telegram does. This layer
depends only on `application/` (layer 6) and contains no domain logic or
orchestration of its own.

### 8. Integrations — `integrations/`

Adapters to everything external: `integrations/tradingagents/` isolates
TradingAgents behind a stable interface, plus future adapters for LLM
providers and brokers. A future LLM-backed `Analyst` (layer 3) calls an LLM
adapter here, never a provider SDK directly — mirroring how
`market_intelligence/providers/yfinance_provider.py` is the only place that
imports `yfinance`. Swapping or removing an integration should never
require changes outside its adapter.

### Supporting packages — `portfolio/`, `simulation/`, `education/`

Cohesive sub-domains supporting the pipeline's later stages:

- `portfolio/` and `simulation/` together implement the **Paper Portfolio**
  pipeline stage (Epic 8): simulating capital allocation and hypothetical
  position entry/exit against recorded decisions, with no broker involved.
- `education/` turns a decision and its supporting reports into a
  beginner-friendly explanation (teacher/explainer agent, beginner CLI/
  Telegram report text). Not yet assigned a specific Epic number in the
  current sequence (see `docs/roadmap.md`); it remains a real, described
  capability of the platform, to be scheduled once the Telegram interface
  (Epic 10) needs it.

All three depend on `market_intelligence` and `memory`, and are depended on
by `application/` only — not by `committee/` or `interfaces/` directly.
This is the other correction from the earlier draft of this document, which
had `committee/` and `interfaces/` each depending on these packages
directly; the Application Service now owns that composition.

## Dependency direction, summarized

- `interfaces` → `application` — nothing else.
- `application` → `committee`, `market_intelligence`, `indicators`,
  `memory`, `evaluation`, `portfolio`, `simulation`, `education`.
- `committee` → `market_intelligence`, `indicators`.
- `indicators` → `market_intelligence`.
- `evaluation` → `memory`.
- `portfolio`, `simulation`, `education` → `market_intelligence`, `memory`.
- `market_intelligence`, `memory` → `integrations` (via adapters only).
- A future LLM-backed `Analyst` in `committee/` → `integrations` (via
  adapters only), same rule.
- Every layer → `domain` (for `Asset`, `PriceBar`, `MarketSnapshot`,
  `Recommendation`).
- Nothing depends on `interfaces`. `integrations` and `domain` depend on
  nothing else in this project.

## Why this shape

- **Auditability**: every recommendation and simulation is stored, so any
  result can be traced back to the data and agent reports that produced
  it — and, per product principle 2, that trace never changes retroactively.
- **Replaceability**: TradingAgents, LLM providers, data providers, and
  brokers are all adapters, not foundations — the domain layers do not know
  which concrete provider is behind them.
- **Simplicity first**: no web framework, task queue, or external database
  until a concrete need forces the introduction of one.
- **Deterministic calculation**: `IndicatorEngine` computes every technical
  and risk metric in plain Python, never an LLM, so indicator values are
  reproducible and independently checkable before any agent interprets
  them.
- **One orchestration path**: the Application Service (layer 6) is the only
  place `market_intelligence` → `indicators` → `committee` → `memory` →
  `portfolio` is wired together, so every interface — CLI, Telegram,
  future web — gets identical behavior for free, and the wiring is tested
  once instead of once per interface.
