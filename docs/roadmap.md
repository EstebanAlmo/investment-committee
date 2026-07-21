# Roadmap

This roadmap covers the MVP only. Each phase should leave the project in a
working, tested state before the next one starts. Order can shift, but later
phases should not be started before earlier ones are functional.

## Phase 0 — Foundation (complete)

- Project structure, `pyproject.toml`, `.gitignore`, `.env.example`.
- `README.md`, `CLAUDE.md`, architecture and roadmap docs.
- Empty packages for every layer, one importability test.
- No data fetching, no agents, no database.

## Epic 1 — Domain model (in progress)

- Framework-independent `domain/` package: `Asset`, `PriceBar`,
  `MarketSnapshot`, `Recommendation`.
- Frozen dataclasses and enums, standard library only, no persistence.
- Focused unit tests for validation rules and invariants.

## Phase 1 — Market Intelligence (single asset, single source)

- Fetch price history for one stock ticker from one free data source.
  **(done — `YFinanceProvider`)**
- Basic data validation (missing bars, obvious bad values).
- A handful of core technical indicators computed in Python. **(done —
  see Epic 3 below)**
- Tests against recorded/fixture data, not live network calls.

## Epic 3 — Indicator Engine (complete)

- Deterministic `IndicatorEngine` in `indicators/` that turns a
  `MarketSnapshot` into an `IndicatorSnapshot`: SMA 20/50/200, 1d/30d/90d/YTD
  returns, annualized volatility, maximum drawdown, and RSI 14.
- Standard-library-only calculations (`decimal`, `statistics`) — no pandas,
  numpy, TA-Lib, external indicator libraries, network access, or LLMs.
- `IndicatorCalculationError` raised for structurally invalid input (no
  price bars, unordered price bars, invalid closing prices); indicators that
  merely lack enough history return `None` instead.
- Full unit test coverage in `tests/indicators/`, including insufficient
  history, flat/rising prices, unordered bars, and a no-network-access
  check.

## Phase 2 — Memory and Storage (SQLite introduced here)

- SQLite schema for analysis runs, agent reports, and recommendations.
- A thin storage interface the Committee and Evaluation layers use, so the
  schema can evolve without leaking into agent code.

## Phase 3 — First Committee agents

- Technical analyst agent, using local calculations plus an LLM call to
  interpret them.
- Portfolio manager agent that turns analyst output into a single
  recommendation.
- Recommendations persisted through the Phase 2 storage interface.

## Phase 4 — Simulation

- Given a stored recommendation, simulate a hypothetical position
  (entry, sizing, exit rules) without any broker involved.
- Store simulation results alongside the recommendation they came from.

## Phase 5 — Evaluation Engine

- Benchmark comparison and simulated return metrics (drawdown, volatility,
  Sharpe, Sortino) over stored simulations.
- Recommendation accuracy and confidence calibration once enough
  recommendations exist to measure.

## Phase 6 — Remaining Committee agents

- Fundamental analyst, ETF analyst, news analyst, macro analyst.
- Bull researcher, bear researcher, and risk manager, feeding into the
  portfolio manager's decision.

## Phase 7 — Education layer

- Teacher/explainer agent that turns a recommendation and its supporting
  reports into a beginner-friendly explanation.
- Beginner-oriented CLI report output.

## Phase 8 — CLI hardening

- A stable CLI command surface (run analysis, view recommendation,
  view simulation, view evaluation report).
- This is the primary user experience until Telegram/dashboard exist.

## Later / explicitly deferred

- Real portfolio monitoring (`portfolio/` beyond simulated positions).
- TradingAgents adapter in `integrations/tradingagents/`.
- Telegram interface.
- Dashboard.
- Paid LLM API usage.
- Broker integration and real-money execution.
- Graphify — revisit once the repository has enough real code to justify it.
- FastAPI, Redis, Celery, Docker, PostgreSQL, frontend frameworks — introduce
  only when a concrete, current need requires them.
