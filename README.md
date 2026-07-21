# Investment Committee

A personal, local, multi-agent investment analysis system for stocks and ETFs.

Investment Committee collects market data, runs it through a set of specialized
analysis agents, stores their recommendations, simulates hypothetical
investments, and evaluates how those recommendations would have performed —
all explained in beginner-friendly language. It is a research and learning
tool, not a trading system: it does not place real orders and does not touch
real money.

## Status

Early foundation stage. The domain model, a first market data provider
(daily history via `yfinance`), and a deterministic indicator engine
(`indicators/`) are implemented; no agents and no database yet. See
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
├── market_intelligence/   # prices, history, fundamentals, news
├── indicators/             # deterministic technical and risk indicator engine
├── committee/                # analyst, researcher, risk, and PM agents
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

## Usage example

Fetch one year of daily price history for a ticker as a `MarketSnapshot`.
The rest of the application only ever depends on `MarketIntelligenceService`
and `MarketDataProvider` — never on the concrete `yfinance` provider:

```python
from investment_committee.market_intelligence import MarketIntelligenceService

service = MarketIntelligenceService()  # defaults to the yfinance provider
snapshot = service.get_daily_history("AAPL")

print(snapshot.asset.ticker)          # "AAPL"
print(len(snapshot.price_bars))       # ~252 daily bars
print(snapshot.latest_price_bar)      # most recent PriceBar
```

Then run that `MarketSnapshot` through `IndicatorEngine` to get a set of
deterministic technical and risk indicators as an `IndicatorSnapshot`:

```python
from investment_committee.indicators import IndicatorEngine

indicators = IndicatorEngine().calculate(snapshot)

print(indicators.latest_close)             # most recent closing price
print(indicators.sma_50)                   # 50-day simple moving average, or None
print(indicators.rsi_14)                   # 14-period RSI, or None
print(indicators.annualized_volatility)    # annualized volatility, or None
```

## Design principles

- Python performs calculations; LLMs interpret, debate, and explain.
- Domain logic never depends directly on TradingAgents — only on the adapter
  in `integrations/tradingagents`.
- External providers (data, LLM, broker) sit behind interfaces/adapters so
  they can be swapped without touching domain code.
- Simple and auditable over clever: no FastAPI, Redis, Celery, Docker,
  PostgreSQL, or frontend frameworks until the project actually needs them.
