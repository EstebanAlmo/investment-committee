# Strategy research V2 and prospective registry

Status on 2026-10-13: **no strategy is validated.** Every result below is
a retrospective diagnosis on months that had already been inspected.

## Strategies

All of them run through the same scanner, replay, risk controls and policies
(`ScanStrategy` interface in `simulation/opportunities.py`). Each has its
own name and version. Its parameters are a frozen dataclass whose SHA-256
fingerprint is recorded in SQLite: the same `name@version` with different
parameters is refused, so a change needs a new version. The V2 strategies
reuse v1's plan rules unchanged (`strategy._plan`: sizing at the account's
risk fraction, stop and target at ATR multiples of the fill, gates on
minimum size, margin, costs above 25 % of the risk, and net reward/risk
below 1.5), plus v1's data gates. Only closed bars are used.

| id | idea | parameters fingerprint (SHA-256) |
|---|---|---|
| `tendencia-momentum-volatilidad@1.0.0` | baseline, **unchanged** (golden tests) | `13f2690d…8977` |
| `tendencia-confirmada-4h@0.1.0` | 1h trend (close > SMA20 > SMA50) confirmed by 4h buckets (close above a rising SMA20); RSI14 in 45-65 / 35-55; stop 1.5 ATR, target 3 ATR | `3185131d…7e0c` |
| `ruptura-rango-londres@0.1.0` | 8-bar range 0.8-3 ATR wide; close beyond it by > 0.1 ATR; bar closing 08:00-12:00 London; stop 1.5 ATR, target 3 ATR | `52001a2c…26ae` |
| `reversion-media-lateral@0.1.0` | only when sideways (SMA50 slope ≤ 0.5 ATR over 12 bars, |SMA20 − SMA50| ≤ 0.75 ATR); close beyond SMA20 ± 2 SD with RSI14 < 30 / > 70; stop 1 ATR, target 2 ATR | `9f4a57a0…bee8` |

Full fingerprints are in `strategies.parameters_sha256` in
`data/investment_committee.db`. All share ATR14 between 3 and 40 pips,
data older than 3 h rejected, and the FX market open. The parameter values
are conventional ones, fixed before any V2 strategy was run on any data,
and not tuned afterwards.

## Retrospective diagnosis (April-September 2026, already inspected)

Same snapshot for all (`ae35f14e…c879`, Yahoo 1h, session months
2026-03-31 21:00 to 2026-09-30 21:00 UTC), 10 000 USD, same limits.
Continuous mode, total P&L in USD; not trading = 0.

| strategy | pol. | base | ×1.5 | ×2 | negative months (base) |
|---|---|---|---|---|---|
| v1.0.0 | A | −1 909.77 | −2 411.81 | −3 216.02 | 4/6 |
| v1.0.0 | B | −2 274.80 | −2 426.06 | −3 249.59 | 4/6 |
| tendencia-confirmada-4h | A | −1 472.97 | −2 515.06 | −2 691.06 | 5/6 |
| tendencia-confirmada-4h | B | −1 136.73 | −2 072.41 | −2 801.25 | 5/6 |
| ruptura-rango-londres | A | +190.44 | −601.13 | −650.93 | 4/6 |
| ruptura-rango-londres | B | +108.48 | −649.77 | −579.86 | 4/6 |
| reversion-media-lateral | A | −182.13 | −193.76 | −170.56 | 2/6 |
| reversion-media-lateral | B | +39.41 | −66.18 | −170.56 | 2/6 |

Policy C did not trade with any strategy (no prior validated evidence).
How to read this: the breakout's small gain comes from April and May and
vanishes at 1.5× costs; mean reversion makes very few trades (75 in six
months) and sits around zero; the two trend strategies lose under every
scenario. Months, instruments and costs are in
`data/reports/research_20260401_20261001/` and in the SQLite
`backtest_runs` table (record_kind RETROSPECTIVO). None of this is
evidence in favour: a strategy that "did well" here was simply looked at.

## Pre-registered validation protocol (fixed 2026-10-13, before seeing any later data)

1. **Frozen versions:** the four ids and fingerprints above. Any change is a
   new version and restarts the protocol for that version.
2. **Development data (used, exhausted):** everything up to 2026-09-30
   21:00 UTC. October 2026 bars up to the start below are also excluded,
   because live scans have already looked at them.
3. **Validation window:** sessions from 2026-10-19 00:00 UTC (a Monday) to
   2027-01-15 21:00 UTC. It is evaluated once, after it ends, with the
   frozen replay engine and limits, on a new snapshot downloaded after the
   end (its hash is recorded). In parallel, the prospective registry
   (`/oportunidades` or `scripts/check_opportunities.py --estrategia ...`,
   run by hand) logs live candidates as a consistency check. It is not a
   second chance.
4. **Primary measure:** policy A, base costs, continuous mode, per strategy.
   Policy B, ×1.5 costs and per-instrument results are reported too.
5. **Minimum sample:** 100 trades per strategy in the window. Below that,
   the result is "insufficient" and the strategy stays NO VALIDADA.
6. **Pass criteria (all required):** net P&L > 0 and profit factor ≥ 1.15
   at base costs; net P&L > 0 at ×1.5 costs; max drawdown ≤ 10 %; no
   instrument contributing more than 60 % of the gross profit; and better
   than not trading. With four strategies tested, a single pass is reported
   as such, without picking the best afterwards.
7. **Outcome:** passing does not authorise operations. It makes the
   strategy a candidate for a longer prospective test. Recording VALIDADA
   needs a new database migration (today's schema forbids that status).

With today's data the window has not started: **all strategies remain
NO VALIDADA**.

## Prospective registry (SQLite) and Excel

- `data/investment_committee.db` (sqlite3, git-ignored), versioned
  migrations, UTC timestamps (CHECKed), foreign keys, unique ids, and
  append-only tables (triggers refuse UPDATE/DELETE). Only `alert_states`
  is mutable.
- Responsibilities: the JSONL ledgers for the deterministic committee
  (`decisions.jsonl`), the AI committee (`ai_committee.jsonl`) and the
  `/trade` journal (`simulated_trades.jsonl`) are unchanged and not
  duplicated. Scans, observations (data and prices seen), recommendations
  (ranked candidates), their status events with hypothetical results,
  alerts, audit events, the strategy registry and retrospective backtest
  summaries live in SQLite. Those JSONL files had never been written in
  production, so there was nothing to migrate.
- Prospective rows carry `record_kind = 'PROSPECTIVO'`, backtest summaries
  `'RETROSPECTIVO'`, each pinned by a CHECK.
- `scripts/export_excel.py` rebuilds `data/reports/investment_committee.xlsx`
  from the database (sheets: Recomendaciones, Resultados, Evolucion diaria,
  Estrategias, Backtests retrospectivos, Alertas). It is deterministic:
  regenerating it produces the same file and duplicates nothing.
- Telegram-approved simulated trades are **not** enabled.

## What is still missing for a rigorous prospective evaluation

- Scans run only by hand: nobody runs them yet, and none is scheduled
  (deliberately). A regular, documented manual routine (or a scheduler the
  user explicitly authorises) is needed to cover the window evenly.
- The validation window needs real data that does not exist yet
  (2026-10-19 to 2027-01-15).
- The scanner runs one strategy per scan. Logging the four strategies in
  parallel needs one scan per strategy each time.
- Cost assumptions remain assumptions (no bid/ask from Yahoo), so a broker
  quote sample would make the ×1.5 / ×2 scenarios concrete.
