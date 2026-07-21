# Investment Committee

A personal, local, multi-agent investment analysis system for stocks and ETFs.

Investment Committee collects market data, runs it through a set of specialized
analysis agents, stores their recommendations, simulates hypothetical
investments, and evaluates how those recommendations would have performed —
all explained in beginner-friendly language. It is a research and learning
tool, not a trading system: it does not place real orders and does not touch
real money.

## Status

Early foundation stage. The project structure, conventions, and documentation
exist; no data fetching, no agents, and no database are implemented yet. See
[`docs/roadmap.md`](docs/roadmap.md) for the planned build order.

## Current constraints

- Local Windows development only, no VM, no cloud server.
- Python 3.11.
- No paid LLM API initially; no broker integration; no real-money execution.
- No Telegram bot yet; CLI first.
- SQLite will be introduced later, once the domain logic it stores is stable.

## Project layout

```
src/investment_committee/
├── market_intelligence/   # prices, history, fundamentals, news, indicators
├── committee/              # analyst, researcher, risk, and PM agents
├── memory/                  # analysis runs, reports, recommendations, audit history
├── evaluation/               # benchmarking, returns, risk metrics, calibration
├── portfolio/                 # portfolio state and (later) real portfolio monitoring
├── simulation/                 # hypothetical investment simulation
├── education/                   # beginner explanations of results
├── interfaces/                   # CLI first, Telegram/dashboard later
└── integrations/
    └── tradingagents/             # adapter boundary for the TradingAgents project
```

See [`docs/architecture.md`](docs/architecture.md) for layer responsibilities
and the dependency direction between them.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and fill in any values as later layers require
them. `.env` is git-ignored and must never be committed.

## Running tests

```powershell
pytest
```

## Design principles

- Python performs calculations; LLMs interpret, debate, and explain.
- Domain logic never depends directly on TradingAgents — only on the adapter
  in `integrations/tradingagents`.
- External providers (data, LLM, broker) sit behind interfaces/adapters so
  they can be swapped without touching domain code.
- Simple and auditable over clever: no FastAPI, Redis, Celery, Docker,
  PostgreSQL, or frontend frameworks until the project actually needs them.
