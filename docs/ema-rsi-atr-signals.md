# `ema-rsi-atr-forex@0.1.0`: indicators and signal detection (Entrega 2)

Status on 2026-11-11: **EXPERIMENTAL, NOT VALIDATED.** This delivery only
detects signals. There is no entry price, stop, target, position size,
backtest or monitor yet, and the strategy is not registered among the
frozen ones (`simulation/strategies.py`), so the earlier protocol and its
monitor are unaffected.

## Files

| File | Content |
|---|---|
| `src/investment_committee/simulation/wilder.py` | `ema`, `ema_series`, `wilder_rsi`, `wilder_atr`, `true_ranges` |
| `src/investment_committee/simulation/ema_cross.py` | `EmaCrossParameters`, `fingerprint`, `classify_cross`, `evaluate`, `SignalEvaluation` |
| `tests/simulation/test_wilder.py`, `test_ema_cross.py`, `_ema_cross_support.py` | tests, synthetic series and an independent exact-fraction reference |
| `scripts/diagnose_ema_cross_signals.py` | detection counts over the existing Yahoo snapshot (diagnosis only) |

`simulation/technical.py` (SMA, simple-average RSI and ATR of the frozen
strategies) is untouched.

## Formulas

Closes `C`, highs `H`, lows `L` of closed H1 bars, oldest first, Decimals.

```
EMA(n)   a = 2 / (n + 1);  seed = mean of the first n closes
         EMA_t = a * C_t + (1 - a) * EMA_(t-1)                      n = 20, 50

RSI(14)  gain_t = max(C_t - C_(t-1), 0);  loss_t = max(C_(t-1) - C_t, 0)
         seed = mean of the first 14 gains / losses
         avg_t = (13 * avg_(t-1) + value_t) / 14
         RSI = 100 - 100 / (1 + avg_gain / avg_loss)
         avg_loss = 0: 100 if avg_gain > 0, otherwise 50

ATR(14)  TR_t = max(H_t - L_t, |H_t - C_(t-1)|, |L_t - C_(t-1)|)
         seed = mean of the first 14 true ranges
         ATR_t = (13 * ATR_(t-1) + TR_t) / 14
```

## Rule

On the candle that closed at the last full hour at or before the
evaluation moment, with the EMAs of that candle and of the one before it:

- cross up: `EMA20 <= EMA50` before and `EMA20 > EMA50` now; **LONG** if
  also `RSI14 > 50`;
- cross down: `EMA20 >= EMA50` before and `EMA20 < EMA50` now; **SHORT** if
  also `RSI14 < 50`;
- a cross whose RSI does not confirm is `CRUCE_SIN_CONFIRMACION` (RSI
  exactly 50 confirms neither side) and is not confirmed by a later candle;
- averages that touch or come close are `SIN_CRUCE`.

## Data gates (first failure ends the evaluation: `DATOS_RECHAZADOS`)

1. `MERCADO_CERRADO`: the candle's hour is outside the calendar (default:
   FX week, Sunday 17:00 to Friday 17:00 New York, daylight saving applied).
2. Bars still forming at the evaluation moment are dropped and counted.
3. `DATOS_INCONSISTENTES`: from the start of the window on, two different
   bars for the same instant, a label off the hourly grid, or a
   non-positive price. (Exact duplicates collapse; bars outside the
   calendar are dropped.)
4. Continuity, established BEFORE any indicator is computed. The window
   is the last 300 hours the calendar expects, ending at that candle - not
   "the last 300 bars available". Two expected hours separated by a closed
   market (a weekend) are neighbours: a **normal closure**
   (`market_closures_in_window`). An expected hour without a bar is an
   **anomalous gap** (`missing_in_window`); the window never reaches
   further back to replace it.
   - `DATOS_INSUFICIENTES`: the history does not reach the window's start;
   - `DATOS_DESACTUALIZADOS`: the candle that just closed has no bar;
   - `DATOS_INCOMPLETOS`: more than 3 hours missing in the window, or any
     among the last 50.

   The candle and the one before it are therefore always consecutive
   market hours: a cross is never read across a hole. Up to 3 older holes
   are tolerated (the approved rule); the averages then run over 297-299
   bars, the hole being passed over, never filled.

Timestamps are compared as instants (UTC); a naive datetime is an error.
The checks reuse `market_intelligence.quality` (Entrega 1), and every
evaluation carries the prices' `QuoteKind` and source.

## One signal per strategy, instrument and candle

`signal_id = "ema-rsi-atr-forex@0.1.0|<symbol>|<candle close, UTC>"`.
Evaluating the same candle again (later in the hour, after a restart, with
more history, with the bars in another order) gives the same id and the
same numbers. The uniqueness constraint itself belongs to the store that
will record signals, in a later delivery.

## The 300-bar window

Indicators are always computed on the bars of the last 300 expected hours
(300 bars, or 297-299 with tolerated holes), so an evaluation can be
reproduced from those bars alone, whatever else the provider returned.

The price to pay is that a value depends, slightly, on where the window
starts, because of the seed. The seed's remaining weight at the last bar:

| Indicator | Values after the seed | Weight of the seed |
|---|---|---|
| EMA20 | 280 | (19/21)^280 ≈ 7e-13 |
| EMA50 | 250 | (49/51)^250 ≈ 4.5e-5 |
| RSI14, ATR14 | 285 | (13/14)^285 ≈ 7e-10 |

So the EMA50 of one candle differs between two windows one bar apart by
about 4.5e-5 times the difference between two 50-bar averages: far below a
tenth of a pip (tested: under 0.000001). Two consequences:

- The "previous" EMAs are read from the same window as the current ones,
  not from the previous hour's evaluation. An hour's evaluation is
  self-contained.
- In principle, if EMA20 and EMA50 are closer to each other at a candle
  than this window effect, that candle's cross can be seen an hour apart
  by two consecutive evaluations, be missed, or be seen twice. The effect
  is of the order of 4.5e-5 times the change of a 50-bar average over one
  bar: around 1e-8 in price for a 1-pip change, against a tested bound of
  1e-6. In the Yahoo diagnosis the averages at a cross were typically
  0.00003 to 0.0001 apart, and under 0.00001 in the closest case shown
  below. One position per pair and one signal per candle bound the
  consequences; it is a known property, not corrected.

A longer window would shrink the effect and need more history at start-up
(300 bars are about two and a half weeks of market).

## Examples

Synthetic (in `test_ema_cross.py`, series `whipsaw()`): 330 flat bars at
1.10000, then +119.1 pips, 30 flat bars, -118.0 pips, 4 flat bars,
+71.8 pips.

| Bar | EMA20 vs EMA50 | RSI14 | Result |
|---|---|---|---|
| 331 | from exact equality to 6.7 pips above | 100 | LONG |
| 366 | from 4.3 pips above to 0.1 below | 9.2 | SHORT |
| 367 | from 0.1 pips below to 0.1 above | 49.57 | cross up, not confirmed |
| 368 | still above | - | no cross: not confirmed afterwards |

Yahoo snapshot (diagnosis, prices `ESTIMADA`, months already inspected;
`scripts/diagnose_ema_cross_signals.py`):

| Candle close (UTC) | Pair | EMA20 / EMA50 | Before | RSI14 | Result |
|---|---|---|---|---|---|
| 2026-04-17 10:00 | EUR/USD | 1.17877 / 1.17871 | 1.17866 / 1.17867 | 59.00 | LONG |
| 2026-04-17 02:00 | EUR/USD | 1.17865 / 1.17868 | 1.17872 / 1.17871 | 42.23 | SHORT |
| 2026-04-24 18:00 | GBP/USD | 1.34931 / 1.34922 | 1.34903 / 1.34911 | 68.28 | LONG |
| 2026-05-29 04:00 | GBP/USD | 1.34331 / 1.34331 | 1.34332 / 1.34331 | 50.83 | cross down, not confirmed |

Counts over the snapshot (2026-03-23 to 2026-09-30, hourly evaluations):

| Calendar | Pair | LONG | SHORT | Not confirmed | No cross | Rejected: incomplete | Rejected: stale |
|---|---|---|---|---|---|---|---|
| New York | either | 0 | 0 | 0 | 0 | 2 950 | 60 |
| observed in Yahoo | EUR/USD | 22 | 21 | 0 | 2 509 | 394 | 10 |
| observed in Yahoo | GBP/USD | 25 | 23 | 1 | 2 503 | 394 | 10 |

(The rest are closed-market hours and the first 300 bars of warm-up.)
These are detections only - no entry, no outcome - and say nothing about
profitability.

## What the diagnosis shows

1. **Calendar and provider must match.** With the New York calendar,
   Yahoo's bars never pass: its week opens two hours later, so every window
   has "missing" bars. The calendar is a parameter of `evaluate`; each data
   source needs the calendar that describes it, verified on its own data.
2. **The completeness rule is strict, and it is kept as approved.** Ten
   genuinely missing bars in six months block 394 hourly evaluations per
   pair (about 12 %). About three quarters of them come from one event:
   the week opening of 3 May had five bars missing beyond the usual, which
   exceeds the maximum of 3 and blocks every evaluation until those hours
   leave the window, some two and a half weeks later. The rest come from
   the "none among the last 50" rule after two holes of 2 and 3 bars. No
   signal was detected or lost differently when the window was redefined
   on expected hours (43 and 48 signals before and after). The rule
   penalizes how long a hole stays in the window more than how large it
   is; changing it would be a new version.
3. **The RSI filter almost never rejects, and it is kept as approved**
   (RSI14 above 50 for LONG, below 50 for SHORT). 1 of 92 crosses was not
   confirmed. EMA20 - EMA50 and RSI14 - 50 are both weighted sums of the
   same recent price changes: over all evaluated hours they are on the
   same side 73 % (EUR/USD) and 75 % (GBP/USD) of the time, and at a cross
   almost always (median distance of RSI from 50: 12 and 9 points). The
   rule behaves nearly like a plain EMA cross - by construction, not by
   luck of this sample.
4. **Signal rate:** about 8 per pair per month before any entry filter.

## Limitations

- No real bid/ask data: the only real-data run used Yahoo's indicative
  series, labelled `ESTIMADA`.
- Mid prices are assumed as input; with a bid/ask source the bars come from
  `aggregation.to_price_bars(bars, "mid")`.
- Holidays are not in the calendar; a holiday shows up as missing bars and
  rejects evaluations (the safe side).
- No lateness limit yet between the candle's close and the evaluation: the
  entry rules (Entrega 3) define how late a signal may still be acted on.
- The window effect described above.
- Parameters are hypotheses fixed a priori. The parameter fingerprint is
  `fingerprint()`; it is not yet recorded in any database or protocol.

## Commands

```
Windows:  .venv\Scripts\python.exe -m pytest tests\simulation\test_wilder.py tests\simulation\test_ema_cross.py
          .venv\Scripts\python.exe scripts\diagnose_ema_cross_signals.py
macOS:    .venv/bin/python -m pytest tests/simulation/test_wilder.py tests/simulation/test_ema_cross.py
          .venv/bin/python scripts/diagnose_ema_cross_signals.py
```
