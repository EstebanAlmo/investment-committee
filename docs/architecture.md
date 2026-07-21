# Architecture

Investment Committee is organized into seven layers. Lower layers know nothing
about higher layers; higher layers depend on lower ones, never the reverse.
Everything external (data providers, LLM providers, brokers, TradingAgents)
sits behind an adapter so it can be replaced without touching domain code.

```
┌─────────────────────────────────────────────────────────────┐
│ 6. User Experience        (interfaces/)                       │
│    CLI first · Telegram later · dashboard later · reports     │
└───────────────▲─────────────────────────────────────────────┘
                │ depends on
┌───────────────┴─────────────────────────────────────────────┐
│ 3. Investment Committee    (committee/)                       │
│    analysts · researchers · risk manager · portfolio manager  │
│    · teacher/explainer agent                                  │
└───────┬─────────────────┬─────────────────┬───────────────────┘
        │ depends on      │ depends on      │ depends on
┌───────▼──────┐  ┌────────▼────────┐  ┌─────▼──────────┐  ┌──────────────┐
│ 2. Indicators│  │ 4. Memory and    │  │ 5. Evaluation  │  │ education/   │
│ (indicators/)│  │    Storage       │  │    Engine      │  │ portfolio/   │
│              │  │                  │  │                │  │ simulation/  │
└──────┬───────┘  └──────────────────┘  └────────────────┘  └──────────────┘
       │ depends on
┌──────▼───────────────────────────────────────────────────────┐
│ 1. Market Intelligence   (market_intelligence/)                │
│    prices · history · fundamentals · news · ETF metadata       │
└──────┬────────────────────────────────────────────────────────┘
       │ depends on
┌──────▼───────────────────────────────────────────────────────┐
│ 7. Integrations   (integrations/)                              │
│    TradingAgents adapter · future LLM/broker/data adapters     │
└─────────────────────────────────────────────────────────────┘

Every layer above may also depend on:
┌─────────────────────────────────────────────────────────────┐
│ 0. Domain   (domain/)                                          │
│    Asset · PriceBar · MarketSnapshot · Recommendation          │
└─────────────────────────────────────────────────────────────┘
```

The Indicators layer sits directly between Market Intelligence and the
future Committee/recommendation layers: `market_intelligence` produces a
`MarketSnapshot`, `indicators` turns it into an `IndicatorSnapshot`, and
`committee` (once built) consumes that `IndicatorSnapshot` rather than
recomputing anything itself.

## Layers

### 0. Domain — `domain/`

The framework-independent core types every other layer shares: `Asset`,
`PriceBar`, `MarketSnapshot`, and `Recommendation`. These are frozen
dataclasses with no dependency on external providers, storage, or
TradingAgents — just the standard library. They exist below Market
Intelligence in the dependency order: every layer may import from `domain/`,
but `domain/` imports from nothing else in this project. `domain/` does not
compute indicators or persist anything; it only defines the shapes and their
invariants (e.g. a `PriceBar`'s high cannot be lower than its open, low, or
close; a ticker is normalized and cannot be blank).

### 1. Market Intelligence — `market_intelligence/`

Responsible for everything that describes the state of the market: prices,
historical data, fundamentals, news, ETF metadata, and validation of that
data before anything downstream consumes it. This layer has no opinion about
what to do with the data — it only collects, validates, and exposes it as a
`MarketSnapshot` containing raw OHLCV price bars. Technical and risk
indicator calculation is not this layer's job; that lives one layer up, in
`indicators/`.

### 2. Indicators — `indicators/`

Turns raw market data into derived technical and risk metrics. `MarketSnapshot`
(defined in `domain/`) holds only raw price data; the `IndicatorEngine` in
this layer performs deterministic, standard-library-only calculations over
that data — moving averages, returns, annualized volatility, maximum
drawdown, RSI — and produces an `IndicatorSnapshot`, the derived metrics
downstream layers read. This layer does not fetch data, call external
services, or generate recommendations, and **LLMs must never calculate
indicators**: all numeric computation happens here in plain Python, so any
agent that later reads an `IndicatorSnapshot` only interprets and explains
numbers that have already been computed.

### 3. Investment Committee — `committee/`

The agents that interpret market intelligence: technical analyst,
fundamental analyst, ETF analyst, news analyst, macro analyst, bull
researcher, bear researcher, risk manager, portfolio manager, and a
teacher/explainer agent. Agents read from Market Intelligence and Indicators
and write to Memory and Storage. All indicator math is computed by
`IndicatorEngine` in `indicators/`, not by agents or LLMs; the LLM's role is
to interpret, debate, and explain already-computed metrics, not to compute
them.

### 4. Memory and Storage — `memory/`

The system of record: analysis runs, agent reports, recommendations,
portfolio snapshots, simulations, prompt versions, model versions, and audit
history. SQLite will back this layer once its schema is stable; for now it
defines the interfaces/data shapes other layers write to and read from.

### 5. Evaluation Engine — `evaluation/`

Judges the committee's output after the fact: benchmark comparison,
simulated returns, drawdown, volatility, Sharpe and Sortino ratios,
recommendation accuracy, confidence calibration, and strategy comparison. It
reads from Memory and Storage and never mutates it.

### 6. User Experience — `interfaces/`

How a human interacts with the system: CLI first, Telegram and a dashboard
later, plus beginner-oriented reports. This layer depends on the layers
below it and contains no domain logic of its own.

### 7. Integrations — `integrations/`

Adapters to everything external: `integrations/tradingagents/` isolates the
TradingAgents project behind a stable interface so domain code never imports
it directly, plus future adapters for LLM providers, brokers, and data
providers. Swapping or removing an integration should never require changes
outside its adapter.

### Supporting packages

`portfolio/`, `simulation/`, and `education/` are cohesive sub-domains that
support the Investment Committee and Evaluation layers: tracking
(eventually real) portfolio state, running hypothetical investment
simulations, and turning analysis into beginner-friendly explanations,
respectively. They depend on Market Intelligence and Memory and Storage, and
are depended on by `committee/` and `interfaces/`.

## Dependency direction, summarized

- `interfaces` → `committee`, `evaluation`, `education`, `portfolio`, `simulation`
- `committee` → `market_intelligence`, `indicators`, `memory`, `portfolio`, `simulation`, `education`
- `indicators` → `market_intelligence`
- `evaluation` → `memory`
- `portfolio`, `simulation`, `education` → `market_intelligence`, `memory`
- `market_intelligence`, `memory` → `integrations` (via adapters only)
- Every layer → `domain` (for `Asset`, `PriceBar`, `MarketSnapshot`,
  `Recommendation`)
- Nothing depends on `interfaces`. `integrations` and `domain` depend on
  nothing else in this project.

## Why this shape

- **Auditability**: every recommendation and simulation is stored, so any
  result can be traced back to the data and agent reports that produced it.
- **Replaceability**: TradingAgents, LLM providers, data providers, and
  brokers are all adapters, not foundations — the domain layers do not know
  which concrete provider is behind them.
- **Simplicity first**: no web framework, task queue, or external database
  until a concrete need forces the introduction of one.
- **Deterministic calculation**: `IndicatorEngine` computes every technical
  and risk metric in plain Python, never an LLM, so indicator values are
  reproducible and independently checkable before any agent interprets them.
