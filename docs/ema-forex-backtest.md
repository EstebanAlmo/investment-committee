# Backtest engine of the experimental Forex strategies (B4)

Engine `ema-forex-backtest@1.0.0`, common to `ema-rsi-atr-forex@0.1.0` (A)
and `ema-3-9-20-50-120-forex@0.1.0` (B).

**Experimental. Retrospective simulation under an ESTIMATED execution
model.** No order, no broker, no executable price. Nothing it produces is a
real net return: spread, slippage, commission and financing are
assumptions. So far it has only been run on made-up bars (B4.1, B4.2); the
historical run (B4.3) is not authorized yet.

## Files

| File | Content |
|---|---|
| `src/investment_committee/simulation/forex_backtest.py` | the engine: `evaluate_signals`, `run_account`, `run`, `Scenario` |
| `src/investment_committee/simulation/forex_backtest_metrics.py` | metrics, report text, reproducible record (B4.2) |
| `tests/simulation/test_forex_backtest.py`, `test_forex_backtest_metrics.py` | synthetic tests with results computed by hand |
| `tests/simulation/_forex_backtest_support.py` | scripted signals and hand-written bars |

Reused unchanged: `ema_cross.evaluate`, `ema_trend_cross.evaluate`,
`signals.closed_window`, `trade_proposal.build_proposal` / `settle` /
`indeterminate`, `ema_cross_sessions`, `quotes.estimated_from_mid`,
`opportunities.AccountState` / `RiskLimits`. Not used and not touched: the
engines of the frozen strategies (`backtest.py`, `replay.py`,
`execution.py`, `outcome.py`) and the frozen registry.

## Time: what is known when

Bars are labelled by their **open** time (verified for the Yahoo snapshot
on 2026-10-13 against 5-minute bars, see `simulation/sessions.py`). The bar
labelled `L` covers `[L, L + 1h)` and is closed at `L + 1h`.

At an instant `t` the engine reads prices only through `_Tape`:

- `closed_at(t)`: the whole bar labelled `t - 1h`, which closed at `t`;
- `open_at(t)`: the **open** of the bar labelled `t`, and nothing else of it.

Two passes:

1. **Signals** (`evaluate_signals`). The evaluation at `t` receives only
   bars with `label + 1h <= t`. It knows no account and no cost: it is
   computed once and shared by every scenario.
2. **Account** (`run_account`). At each full hour `t`, in this order:
   1. the bar that closed at `t` resolves open positions (levels touched
      inside it) or is found missing;
   2. the accounting day rolls at 00:00 UTC;
   3. with the open of the bar labelled `t`: an uncertainty is resolved, a
      level already crossed at that open fills, a position due leaves;
   4. the signals whose candle closed at `t` become proposals, EUR/USD
      before GBP/USD (the order is recorded in `Decision.order`); an
      accepted one opens its position;
   5. the account is valued.

| Recorded instant | Meaning | With H1 OHLC |
|---|---|---|
| `signal_candle_close_at` | the signal candle closed | `t` |
| `quote_available_at` | the estimated quote exists | `t` (open of the bar labelled `t`); `None` if that bar is missing |
| `entry_at` | hypothetical entry | `t` |
| `exit_at` | exit of a CLOSED trade | the open instant for a gap or a market exit; the bar's close for a level touched inside a bar; `None` for an indeterminate trade |
| `recorded_at` | the account booked the result | equal to `exit_at`, or the moment an uncertainty was resolved |

The entry bar's high, low and close are read one hour later (step 1) and
only for exits: the open came first, so a level touched inside the entry
bar counts.

## Entry

- Quote: `estimated_from_mid(open of the bar labelled t, assumed spread)`,
  kind `ESTIMADA`; `build_proposal` does the rest by its own rules.
- No bar labelled `t`: `COTIZACION_AUSENTE`. A signal is never retried at a
  later bar.
- Open further than 0.25 ATR from the signal candle's close:
  `PRECIO_DESPLAZADO`.
- Inert with OHLC data (they cannot fail, and so are not tested by a
  backtest): quote delay (5 min), quote age (30 s) and `SPREAD_EXCESIVO`,
  because the quote is built at the exact instant with a constant spread.

## Exits

Levels are compared with the estimated **exit side**: bar price minus half
the assumed spread for a LONG (it sells at the bid), plus half for a SHORT
(it buys at the ask). The spread is paid once, through the prices. Touching
a level exactly triggers it.

| In the bar labelled `L` | Result | Dated |
|---|---|---|
| open beyond the stop | STOP at that open less slippage (`gap`) | `L` |
| open beyond the target | TARGET at its level, no improvement | `L` |
| only the stop inside | STOP at its level less slippage | `L + 1h` |
| only the target inside | TARGET at its level | `L + 1h` |
| both inside | INDETERMINATE | booked at `L + 1h` |
| due (72 bars or Friday 16:00 New York), levels untouched | market exit at the open of the bar labelled then, exit side less slippage (`APERTURA`) | that instant |
| due, that bar missing, the previous one present | market exit at the close of the bar that just ended (`CIERRE_VELA_ANTERIOR`): the same instant, not a stale price | that instant |

An exit inside a bar is dated at the bar's close; if the bar crosses 17:00
New York, that night's financing is charged. A stop does not cap the loss:
a gap fills beyond it (`loss_beyond_estimate`, `exceeded_budget`).

A position still open when the period ends is `ABIERTA_AL_FINAL`: it is not
closed by force and is not a closed trade.

## Uncertainty

Nothing is assumed about an hour that was not observed.

| Case | Treatment |
|---|---|
| Stop and target inside one bar | INDETERMINATE at that bar's close: conservative = stop, optimistic = target |
| An hour the calendar expects has no bar while a position is open | the position is **not** assumed to have survived. It stays unresolved, holding its pair, risk and margin, until the first bar that exists again (labelled `r`). At `r`, using only that bar's open: INDETERMINATE, conservative = the stop, or the open at `r` if that is worse; optimistic = the target |
| A position is due and no price exists | same as a hole |
| Prices never return before the end | `ABIERTA_AL_FINAL` with `uncertainty` and a stale valuation |

For a hole the trade records the missing hours, the interval, the last
close before it (and when that bar closed) and the first open after it. No
exit instant is invented: `exit_at` is `None`; `recorded_at` is `r`, the
first moment the account could know. Chronology is kept without future
data: the decision at `r` uses the open of the bar labelled `r` and
nothing later, and until then the pair simply cannot open a new position.

An indeterminate trade is not a valid closed trade for the metrics. The
account books its **conservative** result, so later sizing and limits
never benefit from an unknown. The optimistic reading exists only as a
separate run (`INDETERMINADAS_OPTIMISTAS`) with its own account; it cannot
touch the main one.

The two bounds are not strict: inside an unobserved hour the price can
have gone further than either.

## Account and risk

- One run is one strategy and one account of 10 000 USD
  (`RiskLimits.capital`). Runs share nothing: A cannot affect B.
- Sizing and limits use the **realized balance** (initial + booked
  results). Unrealized gains never enlarge a position.
- Controls, all inside `build_proposal`, unchanged: risk per trade 0.5 %;
  daily loss 1.5 % of the day's starting balance less the losses realized
  that day; risk on one side of the dollar 1 %; margin up to 75 % at
  leverage 30; one position per pair.
- **Accounting day: the UTC date** (00:00 UTC, as `AccountState` defines
  it), not the 17:00 New York session day. A result that comes from the bar
  closing exactly at 00:00 belongs to the day that ended; an event at the
  00:00 open belongs to the new day.
- EUR/USD and GBP/USD can be open together, even in the same direction
  (0.5 % + 0.5 % fits the 1 %). Simultaneous signals: EUR/USD first;
  GBP/USD then sees that position's risk and margin.

## Valuation and drawdown

Every hour: `balance` (realized) and `equity` (balance + each open position
at the exit side of the last close, less its commission and the financing
nights so far; no exit slippage). If the bar that should have just closed
is missing, the position is valued at the last close known and the point is
flagged `stale`: it is not a current valuation.

Drawdown is reported on both series. **Hourly OHLC marks miss what happens
inside the hour: the drawdown is understated.**

## Costs and scenarios

All are assumptions; none is verified.

| | EUR/USD | GBP/USD | Where it enters |
|---|---|---|---|
| Spread | 1.0 pip | 1.5 pips | in the prices (in at the ask, out at the bid): reported, never subtracted |
| Slippage | 0.2 pip | 0.3 pip | inside the entry, the stop fill and market exits; a target has none |
| Commission | 0 | 0 | assumed, not a verified commission; subtracted outside prices |
| Financing | no rate | no rate | see below |

| Scenario | Change |
|---|---|
| `BASE` | the table above; result **before financing** |
| `COSTOS_X1_5`, `COSTOS_X2` | spread, slippage and commission multiplied |
| `FINANCIACION_ILUSTRATIVA_3_POR_CIENTO` | 3 % a year, cost-only, illustrative: no broker's rate |
| `COMISION_HIPOTETICA_<n>_USD_POR_100K` | a made-up round-trip commission (`hypothetical_commission(n)`) |
| `INDETERMINADAS_OPTIMISTAS` | the account books the optimistic result of indeterminate trades |

Every scenario is a complete run: `build_proposal` recomputes entry, stop,
target and size with the scenario's values, so trades can differ from the
base run. With higher costs: the entry is worse; the stop distance (1.5
ATR) is the same; the loss per unit grows, so the size falls; the target
moves away (`T >= 3 x (D + slip) + 4 x (c + F)`); levels are reached at
different bar prices because the exit side moves with the spread. The
maximum-spread rule is not scaled (at x2 GBP/USD sits exactly on its 3.0
pips limit, which is inclusive). Signals and strategy parameters never
change.

Three results that must not be confused: (1) before financing - the base
run; (2) under the illustrative rate - a separate run, not a subtraction,
because planned financing moves the target and the size; (3) the real net
result - not computable: there is no verified rate, spread or slippage.

## Limitations

- One indicative price per hour; bid and ask are estimated around it with a
  constant spread. Real spreads widen at the rollover and on news.
- About 1 pip of noise in Yahoo's prices can decide whether a level a few
  pips away was touched.
- The order of events inside an hour is unknown; hence the indeterminate
  trades. With a stop of 1.5 hourly ATR they may be frequent.
- Holidays are not modelled; their missing bars are holes.
- April to September 2026 has already been inspected: no run on it is an
  independent validation, and no winner is to be chosen from it.

## Metrics (B4.2)

`forex_backtest_metrics.summarize(run)` and `render(summary)`. Three groups
that are never mixed:

| Group | Used for |
|---|---|
| Valid closed trades (`CERRADA`) | win rate, expectancy, profit factor, averages, duration, exits, R distribution, costs, concentration |
| Indeterminate trades | counted apart, by kind, with the sum of their conservative and of their optimistic results |
| Open at the end | counted apart, with their unrealized mark |

The account (balance, return, drawdown) is what the engine booked: valid
trades plus the conservative result of the indeterminate ones.
`optimistic_pnl` re-reads the same trades and sizes with the optimistic
results; the `INDETERMINADAS_OPTIMISTAS` run is the path-dependent version.

| Figure | Definition |
|---|---|
| Win rate | wins / n; a win is net P&L > 0 |
| Interval | Wilson score interval at 95 % (z = 1.959964), only with n >= 30. Chosen over the normal approximation because it stays inside 0-1 and behaves with few trades and rates near 0, which is where a 3R strategy lives |
| Expectancy | mean of `r_multiple` = net P&L / estimated loss at the stop |
| Profit factor | sum of wins / sum of losses; `None` without losses |
| Concentration | share of the sum of wins from the best 1, 3 and 5 trades |
| Drawdown | largest fall from a previous maximum, as a fraction of it, on the hourly balance and on the hourly estimated equity |
| Monthly | by the UTC month in which the account booked the trade; valid and indeterminate in separate columns |
| Costs | spread and slippage are information (already inside prices and P&L); commission and financing are the amounts subtracted |

With fewer than 30 valid closed trades everything computable is shown and
labelled `MUESTRA_INSUFICIENTE`. An undefined figure is `None` ("no
definido"), never 0. Every block of the report states its n.

## Comparing A and B

`evaluate_signals` once per strategy over the whole dataset, then:

| Run | Start |
|---|---|
| A, full | `first_evaluable_at(signals_a)` |
| B, full | `first_evaluable_at(signals_b)` |
| A, common period | `common_start(signals_a, signals_b)`, with a fresh 10 000 USD |
| B, common period | the same instant (equal to B full) |

The common period is a calendar window. No hour is removed because one
strategy could not evaluate it: each keeps its own coverage
(`BacktestRun.coverage`: evaluations by outcome, data rejections by
reason) and its own rejection reasons, reported side by side.

## Reproducibility

`record(run, commit=, dataset=, dataset_description=, executed_at=)`:

- `reproducible`: engine, commit, dataset hash, strategy id and
  fingerprint, period, scenario, execution parameters, instrument rules,
  risk limits, uncertainty policy, `results_sha256` (every decision, trade
  and hourly point, in a canonical JSON: decimals as text, instants in
  UTC) and the summary; `reproducible_sha256` hashes exactly that.
- `execution`: when it ran, operating system, Python version. Never inside
  a hash.

Decimal arithmetic and the standard library only: the same inputs give the
same hashes on Windows and macOS (a literal hash is pinned in
`test_forex_backtest_metrics.py`). `write_record` writes `record.json`,
`run.json` and `report.txt` with identical bytes on both; the intended
place is `data/reports/backtests/` (git-ignored, separate from any
prospective database).

## Commands

Windows (PowerShell) and macOS (zsh/bash), from the repository root:

```
python -m pytest tests/simulation/test_forex_backtest.py tests/simulation/test_forex_backtest_metrics.py
python -m pytest
```

## The study (B4.3)

`forex_backtest_study.run_study` / `write_study`, run by
`scripts/backtest_ema_forex.py` (verifies the snapshot hashes, refuses a
dirty tree, never rewrites an existing folder):

```
python scripts/backtest_ema_forex.py
```

Two periods per strategy (`COMPLETO`, `COMUN`), six scenarios decided
before reading any data. **Commission convention:** USD per 100 000 units
for the whole round trip, charged once when the trade settles - not per
side; the made-up 7 USD is 7 per standard lot opened and closed.

**Calendar:** the one observed in Yahoo (`yahoo_week`): the week begins
Sunday 19:00 New York, two hours after New York's 17:00 open, and ends
Friday 17:00 New York. With New York's calendar those two Sunday hours
would be "missing" every week. Entry blocks, weekly close and financing
nights are New York's in both cases.

Found while rehearsing on a synthetic snapshot, before any historical run:
the signal pass handed each evaluation a fixed number of bars, so bars
outside the calendar could starve the window. It now hands the bars of
twice the window in clock hours (`Strategy.lookback_hours`), with a test.

### The single diagnostic run of 2026-11-12

Snapshot `fx_1h_20260331_20260930` (Yahoo, EUR/USD and GBP/USD, 3 272 bars
each, 2026-03-23 to 2026-09-30), dataset hash `107957e7...`, commit
`e6cf138`, study hash `a34b6843...`. Output (git-ignored):
`data/reports/backtests/fx_1h_20260331_20260930_107957e7_e6cf138/`.

**ESTIMATED execution on one indicative price. Not a real net return, not
an independent validation (these months had been inspected), not a basis
for choosing a strategy. Nothing was tuned afterwards.**

Common period 2026-05-06 06:00 UTC to 2026-09-30 21:00 UTC, both pairs,
10 000 USD each:

| Scenario | | A | B |
|---|---|---|---|
| BASE | valid closed trades | 60 | 111 (+1 open at the end) |
| | win rate (Wilson 95 %) | 21.67 % (13.12-33.62) | 22.52 % (15.75-31.14) |
| | expectancy | -0.4048 R | -0.1587 R |
| | profit factor | 0.4672 | 0.7797 |
| | result | -1 143.77 USD (-11.44 %) | -855.98 USD (-8.56 %) |
| | drawdown balance / equity | 11.44 % / 11.73 % | 12.85 % / 13.98 % |
| COSTOS_X1_5 | result | -1 168.71 | -1 237.74 (+1 indeterminate) |
| COSTOS_X2 | result | -1 185.46 | -1 480.73 (+1 indeterminate) |
| FINANCIACION 3 % | result | -832.19 | -1 292.51 (+1 indeterminate) |
| COMISION 7 USD | result | -1 166.94 | -949.36 (+1 indeterminate) |

A over its full period (from 2026-04-08): 70 trades, 20.00 % wins
(12.30-30.82), -0.4169 R, profit factor 0.4547, -1 350.88 USD (-13.51 %).
B's full period is its common period.

What it shows, and no more:

- **Both lose in every scenario.** A 3R target needs more than about 25 %
  of wins; both intervals contain 25 %, so the sample does not even tell
  whether either is below break-even by more than chance - and it gives no
  evidence of an edge.
- B's less negative result is not a ranking: the intervals overlap almost
  entirely, B lost 831 USD in June alone and made 345 USD in September.
- No trade was indeterminate at base costs (a bar holding both a 1.5 ATR
  stop and a target beyond 4.5 ATR is rare, and no position met a data
  hole); one appears in B's other scenarios.
- B is far more sensitive to costs than A (-856 -> -1 481 at x2; A -1 144
  -> -1 185): its EMA3/EMA9 entries are more frequent and its stops are hit
  sooner by a wider spread.
- A's better figure under illustrative financing is an artefact of sizing,
  not an improvement: planned financing enlarges the estimated loss per
  unit, so positions are smaller, and a trade stopped within hours never
  pays the nights it was sized for.
- Of B's 187 signals, 51 were rejected because the pair already had a
  position and 27 by the shared dollar exposure: signals are not trades.
