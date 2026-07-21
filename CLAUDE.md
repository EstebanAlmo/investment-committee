# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this project is

Investment Committee is a personal, local, multi-agent investment analysis
system for stocks and ETFs. It collects market data, analyzes it through
specialized agents, stores recommendations, simulates hypothetical
investments, evaluates historical performance, and explains results to a
beginner. See `docs/architecture.md` for the full layer breakdown and
`docs/roadmap.md` for the build order.

## Hard constraints

- Local Windows development only. No VM, no cloud server, no Docker.
- Python 3.11, `src` layout, type hints on all new code.
- No paid LLM API unless the user explicitly introduces one.
- No broker integration, no real-money execution, ever, unless the user
  explicitly changes this constraint.
- No FastAPI, Redis, Celery, Docker, PostgreSQL, or frontend frameworks yet.
- No Graphify yet — revisit only when there is enough real code to justify it.
- CLI first. Telegram and a dashboard come later.
- SQLite is introduced later, once the domain logic it will store is stable.
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
