# `ema-3-9-20-50-120-forex@0.1.0`: signals, proposals and diagnosis (B0-B3)

Status on 2026-11-12: **EXPERIMENTAL, NOT VALIDATED.** A second strategy,
independent of `ema-rsi-atr-forex@0.1.0` ("A", `docs/ema-rsi-atr-signals.md`),
which keeps its identifier, parameters, 300-hour window and results. This
one ("B") detects signals and turns them into hypothetical proposals with
the engine of `docs/ema-rsi-atr-proposals.md`. There is no backtest, no
monitor, no database and no order. It is not registered among the frozen
strategies (`simulation/strategies.py`).

The hypothesis: a fast cross (EMA3 over EMA9) taken only in the direction
of a trend already established on three horizons (EMA20, EMA50, EMA120) -
a continuation after a pullback, where A trades the turn of the trend.

## Files

| File | Content |
|---|---|
| `src/investment_committee/simulation/ema_trend_cross.py` | `EmaTrendCrossParameters`, `fingerprint`, `classify`, `evaluate`, `TrendCrossEvaluation` |
| `src/investment_committee/simulation/signals.py` | shared by A and B: `Outcome`, the data gates (`closed_window`), the contract `TradeSignal` read by `build_proposal` |
| `src/investment_committee/simulation/wilder.py` | unchanged: `ema_series`, `wilder_rsi`, `wilder_atr` take any period |
| `tests/simulation/test_ema_trend_cross.py`, `test_ema_trend_cross_proposals.py`, `test_signals.py` | tests of B and of the shared part |
| `tests/simulation/test_ema_cross_golden.py` | golden tests of A, written before its code was touched |
| `scripts/diagnose_ema_trend_cross_signals.py` | detection counts over the existing Yahoo snapshot (diagnosis only) |

What changed in A's files, and what did not: the data gates moved from
`ema_cross.evaluate` to `signals.closed_window`, and `build_proposal` takes
any `TradeSignal` instead of A's own class. A's identifier, parameter
fingerprint, every evaluation of a synthetic series, its rejections, its
proposals and settlements, and the counts of its Yahoo diagnosis are
literal in the golden tests and did not move.

## Rule

On the candle that closed at the last full hour at or before the
evaluation moment (formulas of the averages, RSI and ATR as in A):

| | LONG | SHORT |
|---|---|---|
| Cross, previous candle | EMA3 <= EMA9 | EMA3 >= EMA9 |
| Cross, this candle | EMA3 > EMA9 | EMA3 < EMA9 |
| Trend, this candle | EMA20 > EMA50 > EMA120 | EMA20 < EMA50 < EMA120 |
| RSI14 (Wilder) | > 50 | < 50 |

- The order of the trend averages is strict: two equal averages are not in
  order. RSI exactly 50 confirms neither side.
- A cross that fails a filter is `CRUCE_SIN_CONFIRMACION`, with one reason
  per filter - `TENDENCIA_ALINEADA` or `TENDENCIA_NO_ALINEADA`,
  `RSI_CONFIRMA` or `RSI_NO_CONFIRMA` - so that what each filter rejects can
  be counted. It is not confirmed later by another candle.
- Averages that touch or come close are `SIN_CRUCE`. No tolerance is
  applied to a cross, however small: a minimum separation would be one more
  parameter. Decimal arithmetic makes the comparison the same everywhere.
- ATR14 is reported with every evaluation and used only by the proposal.

## Data gates

The same gates as A, in the same order, before any indicator
(`signals.closed_window`; first failure: `DATOS_RECHAZADOS`), with B's own
numbers, fixed before the diagnosis was run:

| | A | B |
|---|---|---|
| Window (hours the calendar expects) | 300 | 720 |
| Missing hours tolerated in the window | 3 | 7 |
| Missing hours among the last 50 | none | none |

Closed market, forming bars, contradictory duplicates, off-grid labels and
non-positive prices are treated as in A. A weekend between two expected
hours is a normal closure; an expected hour without a bar is an anomalous
gap and is never replaced by an older candle (the averages then run over
713-719 bars).

## The 720-hour window and the seed of EMA120

Every average starts from the simple mean of its first n closes in the
window. The weight that seed keeps at the last bar:

| Average | In 720 hours | In 300 hours |
|---|---|---|
| EMA120 | (119/121)^600 ≈ 4.5e-5 | (119/121)^180 ≈ 5 % |
| EMA50 | (49/51)^670 ≈ 2e-12 | ≈ 4.5e-5 |
| EMA20, EMA9, EMA3 | below 1e-30 | below 1e-12 |

With 300 hours EMA120 would be, for a twentieth, a simple average of bars
two weeks old; with 720 it keeps what EMA50 keeps in A. Tested on a
synthetic series against the same average run over 1 700 bars: the
720-hour value differs by less than a thousandth of a tick, the 300-hour
value more than 500 times as much. A perfectly steady trend hides the
effect (the seed then lags exactly as much as the average).

Consequences:

- The crossing pair, EMA3 and EMA9, does not depend on the window at all:
  unlike A, a cross cannot be seen an hour apart by two consecutive
  evaluations because of the window.
- The window effect falls on the filter instead: when EMA50 and EMA120 are
  closer than about 4.5e-5 times the distance between the seed and the
  converged average, two evaluations an hour apart could disagree on the
  order. Known property, not corrected.
- Start-up needs 720 hours of history: about six weeks of market.
- A hole of more than 7 hours blocks evaluations until it leaves the
  window, up to six weeks later (in A, more than 3 hours and two and a half
  weeks).

## One signal per strategy, instrument and candle

`signal_id = "ema-3-9-20-50-120-forex@0.1.0|<symbol>|<candle close, UTC>"`.
The same candle evaluated later in the hour, after a restart, with more
history or with the bars in another order gives the same id and the same
numbers. An id of B can never equal an id of A.

## Example (synthetic, `pullbacks()` in `test_ema_trend_cross.py`)

A trend of 1 pip a bar for 800 bars, pullbacks inside it, then a reversal:

| Bar | Cross | EMA20 / EMA50 / EMA120 | RSI14 | Result |
|---|---|---|---|---|
| 801 | down | rising order | 51.06 | not confirmed: trend and RSI |
| 810 | up | rising order | 56.80 | LONG |
| 847 | up | rising order | 48.15 | not confirmed: RSI only |
| 848 | none (EMA3 still above) | - | - | no cross: 847 is not confirmed afterwards |
| 941 | up | falling order | 37.85 | not confirmed: trend and RSI |
| 949 | down | falling order | 42.68 | SHORT |

## Proposals and settlement (B2)

B uses the proposal engine of A as it is (`docs/ema-rsi-atr-proposals.md`):
entry at the ask or bid with slippage, stop at 1.5 x ATR14, target of at
least 3R net of spread, slippage, commission and the configured financing,
0.5 % of equity as the risk budget, the session, quote, spread, drift,
margin, daily-loss and dollar-exposure rules, and the same three money
figures (budget, estimated loss at the stop, realized loss). No rule of the
engine was changed; the only change is that `build_proposal` reads the
signal through the contract `signals.TradeSignal` (symbol, outcome, kind of
data, candle close, last close, ATR and the signal's identifiers).

- A proposal keeps its signal's identifier, so a proposal of B can never be
  taken for one of A, even on the same candle and pair.
- Each strategy has its own hypothetical account of 10 000 USD. "One
  position per pair" is checked inside the account handed to
  `build_proposal`: a position of the other strategy is not in it. Inside
  the account a second signal in the pair, opposite or not, is recorded as
  rejected (`POSICION_ABIERTA`); it neither closes nor reverses anything.
- Without a verified financing rate every proposal carries
  `COSTO_FINANCIACION_NO_VERIFICADO` and claims no ratio after financing.

Worked example (synthetic prices; candle 810 of the series above and its
mirror image; quote 20 seconds after a close on Tuesday 09:00 New York,
spread 1 pip; illustrative 3 % rate, 5 planned nights):

| | LONG | SHORT |
|---|---|---|
| ATR14, RSI14 | 4.1 pips, 56.80 | 4.1 pips, 43.20 |
| Quote bid / ask | 1.17875 / 1.17885 | 1.02115 / 1.02125 |
| Entry | 1.17887 | 1.02113 |
| Stop loss | 1.17825 (6.2 pips) | 1.02175 (6.2 pips) |
| Take profit | 1.18276 (38.9 pips) | 1.01750 (36.3 pips) |
| Take profit with no rate configured | 1.18079 | 1.01921 |
| Units (with no rate) | 44 000 (78 000) | 46 000 (78 000) |
| Estimated loss at the stop / expected net profit | 49.77 / 149.55 | 49.01 / 147.41 |
| Margin at 30:1 | 1 729.01 | 1 565.73 |

The numbers show the same property as in A, more sharply because the stop
is short: with the illustrative rate, five nights of financing move the
target from 19 to 39 pips while the stop stays 6 pips away.

## Diagnosis on the Yahoo snapshot (B3)

`scripts/diagnose_ema_trend_cross_signals.py`, run **once**, on 2026-11-12,
on the snapshot `fx_1h_20260331_20260930` (hash `ae35f14e9d59...`, verified;
nothing downloaded), with the calendar observed in Yahoo. Prices are
`ESTIMADA` (Yahoo's single indicative series, no bid or ask) and the months
were already inspected in this project. **These are detections only: no
entry, no trade, no profit or loss, and nothing here is evidence for or
against the strategy.** The parameters were fixed before the run and were
not touched after it.

Hours walked: 4 605 per pair (2026-03-23 to 2026-09-30).

| | EUR/USD | GBP/USD |
|---|---|---|
| Evaluable hours | 2 442 | 2 442 |
| Not evaluable: closed market | 1 350 | 1 350 |
| Not evaluable: warm-up (history shorter than 720 hours) | 719 | 719 |
| Not evaluable: incomplete data | 91 | 91 |
| Not evaluable: stale data | 3 | 3 |
| EMA3/EMA9 crosses (up / down) | 274 (136 / 138) | 299 (149 / 150) |
| Confirmed signals (LONG / SHORT) | 91 (35 / 56) | 96 (51 / 45) |
| Crosses not confirmed | 183 | 203 |
| - the trend only | 85 | 106 |
| - the RSI only | 8 | 7 |
| - both | 90 | 90 |
| Rejections by the trend filter | 175 | 196 |
| Rejections by the RSI filter | 98 | 97 |
| Crosses with EMA3 and EMA9 less than 0.1 pip apart (less than 0.01) | 15 (1) | 16 (3) |
| - among confirmed signals, less than 0.1 pip apart | 3 | 6 |
| Smallest separation at a cross | 0.00000045 | 0.00000040 |
| Median separation at a cross | 0.9 pips | 1.1 pips |
| Signals whose closest pair of trend averages is less than 0.1 pip apart (less than 1 pip) | 1 (9) | 0 (5) |

Signals by month of the candle's close (B's evaluable hours; signals of B;
signals of A in the same month, on A's own evaluable hours):

| Month | Evaluable hours | EUR/USD B | EUR/USD A | GBP/USD B | GBP/USD A |
|---|---|---|---|---|---|
| 2026-04 | 0 (warm-up) | 0 | 6 | 0 | 6 |
| 2026-05 | 418 | 17 | 3 | 8 | 5 |
| 2026-06 | 520 | 16 | 10 | 18 | 14 |
| 2026-07 | 490 | 18 | 13 | 24 | 5 |
| 2026-08 | 496 | 10 | 9 | 25 | 11 |
| 2026-09 | 518 | 30 | 2 | 21 | 7 |

Signal frequency, A against B:

| | EUR/USD | GBP/USD |
|---|---|---|
| A, signals on all its evaluable hours | 43 on 2 552 = 1.68 per 100 hours | 48 on 2 552 = 1.88 |
| A, on B's evaluable hours where A is evaluable too | 37 on 2 195 = 1.69 per 100 hours | 42 on 2 195 = 1.91 |
| B, signals on its evaluable hours | 91 on 2 442 = 3.73 per 100 hours | 96 on 2 442 = 3.93 |
| B, raw EMA3/EMA9 crosses | 11.22 per 100 hours | 12.24 |

A month of this snapshot has about 500 evaluable hours, so the rates are
roughly 8 to 10 signals per pair per month for A and 19 to 20 for B.

### What the diagnosis shows

1. **B signals about twice as often as A**, before any entry rule. The raw
   cross is six to seven times as frequent; the filters remove two thirds.
2. **The trend filter does almost all the filtering.** It fails on 64 %
   (EUR/USD) and 66 % (GBP/USD) of the crosses.
3. **The RSI filter adds little once the trend is in order**: of the
   crosses that pass the trend filter, it rejects 8 of 99 (EUR/USD) and 7 of
   103 (GBP/USD). That is more than in A (1 of 92), and still small: the
   rule behaves nearly like "EMA3/EMA9 cross with EMA20/50/120 in order".
   It is kept as approved.
4. **Signals come in clusters.** The examples printed by the script include
   two LONG signals six hours apart and two SHORT signals two hours apart
   in the same pair: EMA3 re-crosses EMA9 while the trend holds. With one
   position per pair, part of the signals would be rejected as
   `POSICION_ABIERTA`; how many is not known without simulating trades. The
   number of signals is not a number of trades.
5. **Signals are unevenly spread**: EUR/USD has 10 in August and 30 in
   September.
6. **Very small crosses exist but are few**: about 5 % of the crosses, and
   9 of 187 confirmed signals, have EMA3 and EMA9 less than 0.1 pip apart
   at the cross - less than the noise of an indicative close. They are
   decided by exact arithmetic and are reproducible; whether they are
   meaningful is another matter. No tolerance was added.
7. **The window effect on the trend filter is rare**: one confirmed signal
   had two trend averages less than 0.1 pip apart.
8. **The data rule blocks less than in A**: 91 hours rejected as incomplete,
   against 394 for A on the same snapshot, because up to 7 old holes are
   tolerated. The price is the warm-up: 719 hours, the whole of April.

## Limitations

- No real bid/ask data: the only real-data run used Yahoo's indicative
  series (`ESTIMADA`). A noise of about 1 pip in the close weighs far more
  on EMA3 than on EMA20; the counts could differ on mid prices built from
  bid and ask.
- The months of the snapshot were already inspected for A. Nothing here is
  validation, and the comparison of frequencies says nothing about which
  strategy is better.
- Holidays are not in the calendar; each data source needs its own
  calendar, verified on its own data.
- The stop (1.5 x ATR14) and the 3R target were designed with A; with a
  signal that lives a few hours the target is far in relation to the
  signal's horizon. Not changed: it is the hypothesis to be measured.
- Spreads, slippage, the order of stop and target inside a bar and the
  financing rate remain assumptions (`docs/ema-rsi-atr-proposals.md`).

## Commands

```
Windows:  .venv\Scripts\python.exe -m pytest tests\simulation\test_ema_trend_cross.py tests\simulation\test_signals.py tests\simulation\test_ema_cross_golden.py
macOS:    .venv/bin/python -m pytest tests/simulation/test_ema_trend_cross.py tests/simulation/test_signals.py tests/simulation/test_ema_cross_golden.py

Windows:  .venv\Scripts\python.exe scripts\diagnose_ema_trend_cross_signals.py
macOS:    .venv/bin/python scripts/diagnose_ema_trend_cross_signals.py
```

The diagnosis reads the git-ignored snapshot under `data/raw/snapshots/`
and takes about three minutes. It was authorized once; running it again
gives the same numbers and is not a new observation.
