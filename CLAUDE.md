# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this project is

Investment Committee is a personal, local, multi-agent investment analysis
system for stocks, ETFs and Forex. It collects market data, analyzes it
through specialized agents, stores recommendations, simulates hypothetical
investments, evaluates historical performance, and explains results to a
beginner. See `docs/architecture.md` for the full layer breakdown and
`docs/roadmap.md` for the build order.

Current research focus: two experimental, independent Forex strategies on
H1 (EUR/USD, GBP/USD), to be evaluated prospectively and in parallel, with
hypothetical trades only: `ema-rsi-atr-forex@0.1.0`
(`docs/ema-rsi-atr-signals.md`, `docs/ema-rsi-atr-proposals.md`) and
`ema-3-9-20-50-120-forex@0.1.0` (`docs/ema-trend-cross-signals.md`).
Neither is validated; a change to either one's parameters is a new version.
A retrospective backtest under an ESTIMATED execution model exists
(`docs/ema-forex-backtest.md`): both lost in every scenario, it is not an
independent validation, and its stored study is never rerun or rewritten.
Its trades are reviewed in a local, read-only viewer
(`docs/trade-viewer.md`).

## Hard constraints

- Local development only, on Windows and macOS: everything must run on
  both. No VM, no cloud server, no Docker. Paths through `pathlib`,
  timezone-aware datetimes only, no dependency on PowerShell, Bash or any
  tool of a single system; document commands for both.
- Python 3.11, `src` layout, type hints on all new code.
- No paid LLM API unless the user explicitly introduces one.
- No broker integration, no real-money execution, ever, unless the user
  explicitly changes this constraint.
- No FastAPI, Redis, Celery, Docker, PostgreSQL, or frontend frameworks yet.
  The one exception, explicitly authorized: the local trade viewer
  (`interfaces/trade_viewer/`, Streamlit + Plotly + Kaleido) as OPTIONAL
  dependencies (`pip install -e ".[viewer]"`). It listens on 127.0.0.1
  only, is never published or deployed, only reads studies, and nothing in
  `simulation/` may import it or its dependencies: the engine and the test
  suite must work without them.
- No Graphify yet — revisit only when there is enough real code to justify it.
- CLI first. Telegram exists; the only dashboard is the local trade viewer
  above. No other dashboard yet.
- SQLite (standard library) is the store: one database file per experiment,
  one active monitor per experiment. Git synchronizes code, never an
  active database.
- Never commit secrets, API keys, `.env`, virtual environments, local
  databases, or generated reports (`data/raw`, `data/processed`,
  `data/reports` are git-ignored on purpose).
- Never run `git commit` or `git push` unless the user explicitly asks for it
  in that turn.

## Architecture rules

- Python performs calculations. LLMs interpret, debate, and explain — do not
  push numeric computation into prompts.
- Domain logic must never import TradingAgents directly. All access goes
  through `src/investment_committee/integrations/tradingagents/`, so
  TradingAgents stays replaceable.
- External providers (market data, news, LLM, broker) sit behind an
  interface/adapter in the relevant layer, not called directly from domain
  code.
- Dependency direction: `interfaces` and `committee` depend on
  `market_intelligence`, `memory`, `evaluation`, `simulation`, `portfolio`,
  and `education` — not the other way around. See `docs/architecture.md` for
  the full diagram.

## Working style

- Avoid overengineering. Don't add abstractions, config layers, or
  dependencies beyond what the current task needs.
- Keep modules cohesive and explicit; prefer a few clear functions over a
  premature framework.
- Use dataclasses or Pydantic only when the validation or serialization they
  provide is actually needed — plain functions and types are the default.
- When adding a new layer or package, add a corresponding minimal test
  (importability at least) rather than leaving it untested.

## Verifying changes

Run tests with `pytest` from the repository root after any code change.
