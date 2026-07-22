# Roadmap

This roadmap is organized as a sequence of Epics rather than phases — the
numbering matches what has actually been used in the project's git history
and `docs/specs/` so far. Each Epic should leave the project in a working,
tested state before the next one starts. Order can shift, but later Epics
should not be started before earlier ones are functional. See
`docs/architecture.md`'s "Product pipeline" for how these Epics map onto
the system's runtime data flow.

## Epic 1 — Core Domain (COMPLETED)

- Framework-independent `domain/` package: `Asset`, `PriceBar`,
  `MarketSnapshot`, `Recommendation`.
- Frozen dataclasses and enums, standard library only, no persistence.
- Focused unit tests for validation rules and invariants.

## Epic 2 — Market Intelligence (COMPLETED)

- `MarketIntelligenceService` and `MarketDataProvider` interface.
- `YFinanceProvider`: fetches one year of daily price history for a single
  ticker from a free data source.
- Basic data validation (missing bars, obvious bad values).
- Tests against recorded/fixture data, not live network calls.

## Epic 3 — Indicator Engine (COMPLETED)

- Deterministic `IndicatorEngine` in `indicators/` that turns a
  `MarketSnapshot` into an `IndicatorSnapshot`: SMA 20/50/200, 1d/30d/90d/YTD
  returns, annualized volatility, maximum drawdown, and RSI 14.
- Standard-library-only calculations (`decimal`, `statistics`) — no pandas,
  numpy, TA-Lib, external indicator libraries, network access, or LLMs.
- `IndicatorCalculationError` raised for structurally invalid input;
  indicators that merely lack enough history return `None`.
- Full unit test coverage in `tests/indicators/`. See
  `docs/specs/spec-001-indicator-engine.md`.

## Epic 4 — Investment Committee Domain (COMPLETED)

- Frozen-dataclass domain contracts in `committee/models.py`:
  `CommitteeRole`, `AgentStance`, `EvidenceItem`, `AgentReport`,
  `RiskSeverity`, `RiskItem`, `InvestmentDecision`.
- Shapes and invariants only — no computation, no orchestration.
- See `docs/specs/spec-002-investment-committee-domain.md`.

## Epic 5 — Deterministic Committee Engine (next)

- `Analyst` interface plus `DeterministicTechnicalAnalyst`,
  `DeterministicRiskManager`, `DeterministicDevilsAdvocate`, and a
  `DeterministicCommitteeChair` that combines their `AgentReport`s into one
  `InvestmentDecision` via an explicit, auditable rule checklist and
  decision matrix — no weighted scoring, no LLM.
- `DeterministicCommitteeEngine` orchestrates the three analysts and the
  chair from one `IndicatorSnapshot`, with dependency-injected analysts,
  chair, and clock.
- This is the project's **baseline** strategy (`docs/architecture.md`,
  product principle 1) — every future strategy is compared against it.
- See `docs/specs/spec-003-deterministic-committee-engine.md`.

## Epic 6 — Application Service

- New `application/` package: a single entry point (e.g. `analyze_asset(ticker)`)
  that composes `market_intelligence` → `indicators` → `committee` into one
  `InvestmentDecision`, matching `docs/architecture.md`'s Application
  Service layer.
- Every future user interface calls this layer; it never orchestrates
  `market_intelligence`, `indicators`, or `committee` directly (product
  principle 4).
- A minimal internal CLI ships alongside this Epic as a developer/debugging
  tool for exercising `analyze_asset()` — it is not itself a product-facing
  interface Epic; Telegram (Epic 10) is still the first one. This satisfies
  `CLAUDE.md`'s "CLI first" as an engineering convenience without making
  the CLI the primary product surface.

## Epic 7 — Recommendation Ledger

- SQLite schema for analysis runs, `AgentReport`s, and `InvestmentDecision`s
  in `memory/`.
- Every recorded `InvestmentDecision` is written once and never updated —
  product principle 2 ("immutable historical truth"). Corrections or
  improved rules produce a new decision on a new run, never an edit to a
  past one.
- A thin storage interface the Application Service and Evaluation Engine
  use, so the schema can evolve without leaking into `committee/` or
  `application/` code.

## Epic 8 — Paper Portfolio Engine

- `portfolio/` and `simulation/`: given a recorded `InvestmentDecision`,
  simulate a hypothetical position (entry, sizing, exit rules) and track
  simulated capital allocation and portfolio evolution over time — no
  broker involved.
- Store simulation results alongside the recommendation they came from, in
  the Epic 7 ledger.

## Epic 9 — Performance Evaluation

- `evaluation/`: benchmark comparison and simulated return metrics
  (drawdown, volatility, Sharpe, Sortino) over stored simulations.
- Recommendation accuracy and confidence calibration once enough
  recommendations exist to measure.
- Every score is computed only from market sessions after the
  recommendation's `generated_at` — no look-ahead bias (product
  principle 3).

## Epic 10 — Telegram Interface (first real user-facing interface)

- `interfaces/`: a Telegram bot that calls `application.analyze_asset()`
  (and, once available, the Evaluation Engine) — no direct calls into
  `committee/` or `market_intelligence/` from this layer.
- Beginner-oriented explanations, using `education/`.

## Epic 11 — LLM Analyst Adapters

- LLM-backed implementations of the `Analyst` interface (e.g. an
  `LLMTechnicalAnalyst`), living alongside the deterministic analysts in
  `committee/analysts/`.
- The LLM API call itself sits behind a new adapter in `integrations/`,
  never called directly from `committee/` (same adapter rule as
  `market_intelligence/providers/`).
- Returns the same `AgentReport` dataclass as every deterministic analyst
  (product principle 5) — no parallel LLM-specific report type.

## Epic 12 — Hybrid Committee

- A committee wiring that mixes deterministic and LLM-backed analysts
  (e.g. deterministic Risk Manager + LLM Technical Analyst), assembled via
  `DeterministicCommitteeEngine`'s existing dependency injection — no
  change to `engine.py` or `chair.py` required (product principle 6).

## Epic 13 — Historical Comparison Framework

- Runs the Deterministic Committee, the LLM Committee, and Hybrid
  Committees over identical historical market data and compares their
  recorded outcomes via the Epic 9 Evaluation Engine.
- Answers the project's central open question: does an AI-assisted
  committee actually outperform the deterministic baseline (product
  principle 1), and by how much.

## Epic 14 — Broker Integration (optional, deferred)

- Only attempted after Epic 13 shows a strategy worth trusting with real
  capital. No real-money execution until this Epic is explicitly started,
  per `CLAUDE.md`'s hard constraint.

## Later / explicitly deferred

- Dashboard (beyond Telegram).
- Paid LLM API usage.
- Graphify — revisit once the repository has enough real code to justify
  it.
- FastAPI, Redis, Celery, Docker, PostgreSQL, frontend frameworks —
  introduce only when a concrete, current need requires them.
