# Investment Committee

A personal, local, multi-agent investment analysis system for stocks, ETFs and Forex.

Investment Committee collects market data, runs it through a set of specialized
analysis agents, stores their recommendations, simulates hypothetical
investments, and evaluates how those recommendations would have performed —
all explained in beginner-friendly language. It is a research and learning
tool, not a trading system: it does not place real orders and does not touch
real money.

## Status

Research stage. Besides the committee and its Telegram interface, the
project studies two experimental Forex strategies on H1 (EUR/USD and
GBP/USD), independent of each other, with hypothetical trades only:

| Strategy | Rule | Documents |
|---|---|---|
| `ema-rsi-atr-forex@0.1.0` | EMA20 crosses EMA50, RSI14 | [signals](docs/ema-rsi-atr-signals.md), [proposals](docs/ema-rsi-atr-proposals.md) |
| `ema-3-9-20-50-120-forex@0.1.0` | EMA3 crosses EMA9 with EMA20/50/120 in order, RSI14 | [signals](docs/ema-trend-cross-signals.md), same proposal engine |

Neither is validated. A retrospective backtest under an ESTIMATED execution
model exists ([`docs/ema-forex-backtest.md`](docs/ema-forex-backtest.md)):
on the one Yahoo snapshot available both strategies lost in every cost
scenario, and that run is not an independent validation. Its simulated
trades can be reviewed one by one in a local viewer
([`docs/trade-viewer.md`](docs/trade-viewer.md)). There is no prospective
monitor yet, and no real bid/ask data has been obtained
([`docs/data-layer.md`](docs/data-layer.md)). See
[`docs/roadmap.md`](docs/roadmap.md) for the original build order.

## Current constraints

- Local only, on Windows and macOS (both supported), no VM, no cloud server.
- Python 3.11.
- No paid LLM API initially; no broker integration; no real-money execution.
- Telegram is the first user-facing interface (deterministic, no LLM); a dev CLI also exists.
- SQLite (standard library) stores registries; one database file per experiment, never synchronized through Git.

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
├── interfaces/                   # Telegram bot; local trade viewer (optional)
└── integrations/
    └── tradingagents/             # adapter boundary for the TradingAgents project
```

See [`docs/architecture.md`](docs/architecture.md) for layer responsibilities
and the dependency direction between them.

## Setup

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

macOS:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and fill in any values as later layers require
them. `.env` is git-ignored and must never be committed.

### Trade viewer (optional)

A local dashboard to review the simulated trades of a backtest study on
their candles. The same two commands on Windows and macOS, from the
repository root with the virtual environment active:

```
pip install -e ".[viewer]"
python -m streamlit run scripts/trade_viewer.py
```

Open `http://127.0.0.1:8501`. It runs on this computer only, reads the
stored study without changing it, and shows a simulation on estimated
prices - never real trades. PNG export needs Chrome or Chromium installed;
HTML export does not. Everything else in the project works without these
dependencies. See [`docs/trade-viewer.md`](docs/trade-viewer.md).

## Running tests

```
Windows:  .venv\Scripts\python.exe -m pytest
macOS:    .venv/bin/python -m pytest
```

The results are the same on both systems: Decimal arithmetic, UTC
instants, and the time-zone database of the `tzdata` package.

## Running the Telegram bot

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Find your numeric Telegram user id (e.g. message [@userinfobot](https://t.me/userinfobot)).
3. In `.env` (git-ignored) set:

   ```
   TELEGRAM_BOT_TOKEN=<token from BotFather>
   TELEGRAM_ALLOWED_USER_IDS=<your numeric user id>
   ```

4. From the repository root:

   ```powershell
   .venv\Scripts\python.exe -m investment_committee.interfaces.telegram
   ```

The bot uses long polling (no public URL needed) and answers only the
allowlisted user ids. Market data comes from Yahoo Finance via `yfinance`
(free, no key). Every analysis is appended to `data/ledger/decisions.jsonl`
(git-ignored), relative to the directory you start the bot from.

| Message | What happens |
|---|---|
| `/analyze AAPL` | Runs the deterministic committee, records the decision, replies with it |
| `Analyze Apple`, `Should I buy Nvidia?` | Same, via deterministic intent routing |
| `/history AAPL` (or `/history`) | Lists recorded decisions, most recent first |
| `/explain AAPL` | Shows the evidence behind the latest decision |
| `/compare MSFT AAPL` | Analyzes and records both assets |
| `/help` | Lists commands |
| `/analyze BTC-USD` | Rejected: only stocks, ETFs, and Forex pairs are supported; nothing is recorded |
| `Buy Apple` | Declined: the bot never places trades |
| `/ping`, `/id` | Diagnostics: liveness check and your Telegram user id |

Voice messages get a "please type" reply; speech-to-text is not part of
this version.

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
  The local trade viewer (Streamlit, optional, 127.0.0.1 only) is the one
  authorized exception.
