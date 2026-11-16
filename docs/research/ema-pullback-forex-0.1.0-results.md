# Results: `ema-pullback-forex@0.1.0` (strategy C) on D2

The single retrospective test registered in
`ema-pullback-forex-0.1.0.md`, run once on 2026-11-13 with the user's
explicit authorization. **D2 is now inspected: it cannot test this
hypothesis, or a changed version of it, again.**

## Verdict: NO CONCLUYENTE

The registration requires at least 200 valid closed trades at costs x1.5.
The run has **88**. By the registered criteria the verdict is NO
CONCLUYENTE, "stated as such whatever the result, and not upgraded by
other figures". Every number below is a description of a small sample. It
is not evidence for the strategy and it is not evidence against it.

What the abandonment rules say for this verdict: no claim is made, the
version is not promoted, and going on needs data not yet seen and the
user's decision. No parameter may be adjusted on D0, D1 or D2.

## What was run

| | |
|---|---|
| Study | `data/reports/backtests/d2_ema-pullback-forex-0.1.0_ad7ef187_41706d5/` (git-ignored) |
| Study SHA-256 | `8f48b5bd8c97e624787e1d67cd4bb915ab4e1b4f6549cd571f2bc5b1b4c1566a` |
| Code | commit `41706d5`; strategy, proposal and engine files unchanged since `51c60ab` (checked by the script before running) |
| Registration SHA-256 | `56e2fedefdd1dc317ff203f89f8b305917b4b7ecd95b8aef2abfeb7f05f3e90a`, verified before running |
| Dataset | `yahoo_fx_1h_20241114_20260323`, combined hash `a8f71116...0b91` verified end to end; bars hash of the study `ad7ef187...7715` |
| Period | first candle evaluated closes 2025-07-14 01:00 UTC; end 2026-03-20 21:00 UTC. D1 bars only warmed the indicators up |
| Configuration | the registered one: default parameters, both pairs, 10 000 USD, the six scenarios. One run |
| Command | `python scripts/backtest_ema_pullback_d2.py` (refuses to run a second time) |
| Figures below | `python scripts/research_results.py`, which only reads the stored study |

Disclosed, as the registration asks: two files were added to `simulation/`
after the registered code state and before the run - `registered_study.py`
(starts the existing engine at D2's first candle, with earlier bars as
history only) and `research_statistics.py` (the statistics below). Both
were written and tested on synthetic data before D2 was read. No existing
file of the strategy, the proposal engine or the backtest engine changed.

## Coverage of D2

No bar was invented, interpolated or filled.

| Month | Expected hours | Bars EUR/USD | Bars GBP/USD | Evaluable hours per pair |
|---|---|---|---|---|
| 2025-07 (from the 14th) | 332 | 332 | 332 | 331 |
| 2025-08 | 494 | 494 | 494 | 494 |
| 2025-09 | 520 | 517 | 518 | 468 / 463 |
| 2025-10 | 541 | 540 | 540 | 492 |
| 2025-11 | 472 | 472 | 472 | 472 |
| 2025-12 | 544 | 531 | 531 | 435 |
| 2026-01 | 518 | 495 | 496 | **0** |
| 2026-02 | 472 | 453 | 453 | **0** |
| 2026-03 (to the 20th) | 354 | 352 | 352 | 68 |

Why January and February have no evaluable hour, from the timestamps of
the original and the normalized files:

- **Not an ingest failure.** Every missing hour is missing in Yahoo's
  original file too. The 70 rows the ingest dropped per pair were all
  outside the market's hours.
- **Holidays the calendar does not model:** 13 hours on 25 December 2025
  and 17 on 1 January 2026. The market was closed or nearly so; the
  calendar expects a bar, so they count as missing.
- **A real absence in the source:** 25 expected hours from Friday 30
  January 16:00 UTC to Monday 2 February 2026 19:00 UTC, on ordinary
  trading hours.
- **A one-hour calendar mismatch** on the Sundays of 26 October 2025, 8
  March and 15 March 2026, when the clocks of the United States and Europe
  differ: Yahoo's week opened an hour later than the calendar expects.
- The strategy's data gate (at most 7 missing hours in 720, none in the
  last 50) then refuses every hour for up to 720 expected hours after a
  large hole. The three holes of 25 December, 1 January and 30 January
  chain together and silence the strategy until 18 March 2026.

So the test covers, in effect, **14 July to 25 December 2025** (the last
evaluable hour is 09:00 UTC that day) plus the three days from 18 March
2026. Within that span the series is continuous enough to
evaluate, and the engine marked as indeterminate the positions that met a
hole (2 trades). The evaluation is coherent in time, and short.

## Results (the engine's figures; valid closed trades)

| | Base costs | Costs x1.5 (the registered basis) | Costs x2 |
|---|---|---|---|
| Signals | 164 | 164 | 164 |
| Rejected signals | 72 | 74 | 73 |
| Trades opened | 92 | 90 | 91 |
| Valid closed | 90 | **88** | 89 |
| Indeterminate (data hole) | 2 | 2 | 2 |
| Open at the end | 0 | 0 | 0 |
| Wins / losses | 30 / 60 | 27 / 61 | 26 / 63 |
| Win rate (Wilson 95 %) | 33.3 % (24.5 to 43.6) | 30.7 % (22.0 to 41.0) | 29.2 % (20.8 to 39.4) |
| Expectancy | -0.038 R | **-0.121 R** | -0.166 R |
| Profit factor | 0.93 | 0.82 | 0.76 |
| Net result | -193.18 USD | -529.34 USD | -717.73 USD |
| Account return (indeterminate booked conservatively) | -2.90 % | -6.26 % | -8.12 % |
| Maximum drawdown on equity | 8.96 % | 12.16 % | 13.86 % |
| Average win / average loss | 92.09 / -49.27 USD | 90.66 / -48.81 USD | 89.60 / -48.37 USD |
| Exits: stop / target / time / weekly close | 58 / 27 / 1 / 4 | 59 / 24 / 1 / 4 | 61 / 23 / 1 / 4 |
| Spread + slippage, informative | 428.45 USD | 612.52 USD | 796.92 USD |

Rejections at costs x1.5: position already open 46, entry price moved
more than 0.25 ATR 11, dollar exposure 9, rollover block 6, pullback
deeper than 3 ATR 6, Friday block 5, daily loss limit 1 (a signal can
carry more than one).

The other scenarios, for completeness: illustrative financing 3 %: 84
trades, -0.028 R, -144.53 USD; hypothetical commission 7 USD per 100 000:
89 trades, -0.096 R, -442.07 USD; optimistic reading of the indeterminate
trades: the same 90 trades as base, account +0.01 %.

Impact of costs: from base to x1.5 the expectancy falls by 0.083 R, and by
a further 0.045 R to x2. At base costs the result is near zero; it is
negative in every scenario.

### Net result and account return are different sums

The net result adds the valid closed trades. The account's return also
books the two indeterminate trades (a position that met a data hole on
2025-09-15 and another on 2025-12-23), at their conservative result, as
the engine's rules say. Checked to the cent against `run.json`:

| | Valid closed | Indeterminate, booked | Final balance | Return |
|---|---|---|---|---|
| Base | -193.18 USD (90 trades) | -96.36 USD (2 trades) | 10 000 - 289.54 = 9 710.46 USD | -2.90 % |
| Costs x1.5 | -529.34 USD (88 trades) | -96.53 USD (2 trades) | 10 000 - 625.87 = 9 374.13 USD | -6.26 % |

Read as targets instead of stops, the same two trades would have added
+192.72 USD (base) and +193.06 USD (x1.5). Nothing was miscalculated; the
two figures were shown side by side without saying they had different
bases.

## Statistics of the protocol (costs x1.5)

Seed of the bootstrap 20261113, 10 000 resamples. Trades of the two pairs
at the same hours are correlated, so the intervals are optimistic.

| Scope | n | Wins | Expectancy (R) | 95 % interval, t | 95 % interval, bootstrap | Target-first rate | Null rate | p (one-sided) |
|---|---|---|---|---|---|---|---|---|
| All | 88 | 27 | -0.121 | -0.406 to +0.164 | -0.393 to +0.167 | 24 of 83 = 28.9 % | 28.5 % | 0.51 |
| EUR/USD | 47 | 14 | -0.113 | -0.507 to +0.280 | -0.485 to +0.284 | 29.5 % | 29.0 % | 0.53 |
| GBP/USD | 41 | 13 | -0.130 | -0.561 to +0.301 | -0.532 to +0.308 | 28.2 % | 28.0 % | 0.55 |
| LONG | 46 | 12 | -0.272 | -0.649 to +0.106 | -0.618 to +0.102 | 23.3 % | 28.4 % | 0.82 |
| SHORT | 42 | 15 | +0.044 | -0.397 to +0.485 | -0.369 to +0.485 | 35.0 % | 28.7 % | 0.24 |
| First half (to 2025-11-15) | 67 | 22 | -0.038 | -0.380 to +0.304 | -0.357 to +0.300 | 32.3 % | 28.6 % | 0.30 |
| Second half | 21 | 5 | -0.386 | -0.897 to +0.124 | -0.815 to +0.109 | 16.7 % | 28.1 % | 0.92 |

At base costs: 90 trades, expectancy -0.038 R (t interval -0.326 to
+0.251), target-first 27 of 85 = 31.8 % against a null of 30.1 %
(p = 0.41).

Reading, without stretching it:

- Every interval contains zero, and each is about 0.6 R wide. The sample
  cannot tell an expectancy of -0.4 R from one of +0.16 R.
- The rate at which the target came before the stop is where a price
  without drift would put it (28.9 % against 28.5 %). Nothing here
  separates C's entries from random entries with the same stops and
  targets.
- The differences between LONG and SHORT, between the pairs and between
  the halves are differences between samples of 21 to 67 trades. None is
  to be used to choose a filter.
- By month (costs x1.5): July +599.87 USD on 15 trades, then August
  -291.19, September -153.54, October -4.31, November -524.53, December
  -108.72, March -46.92. One good month out of seven carries the whole
  positive side.

One arithmetic detail of the engine, unchanged: at costs x1.5 the GBP/USD
slippage (0.45 pip) is not a multiple of the price tick, so a stop fills
one fraction of a tick worse than estimated and those losses read -1.002 R
instead of -1.000 R.

## Next to A and B: a description, not a comparison

| | A (`ema-rsi-atr-forex`) | B (`ema-3-9-20-50-120-forex`) | C (`ema-pullback-forex`) |
|---|---|---|---|
| Period | 2026-05-06 to 2026-09-30 | the same | 2025-07-14 to 2025-12-25, plus 3 days of March 2026 |
| Data | Yahoo H1 snapshot of 2026-10-13 | the same | Yahoo H1 download of 2026-11-12 |
| How the data had been used | inspected many times | inspected many times | read once, for this registered test |
| Target | 3 R | 3 R | 2 R |
| Valid closed trades, base | 60 | 111 | 90 |
| Win rate, base | 21.7 % | 22.5 % | 33.3 % |
| Expectancy, base | -0.405 R | -0.159 R | -0.038 R |
| Expectancy, costs x1.5 | -0.415 R (60 trades) | -0.228 R (115 trades) | -0.121 R (88 trades) |
| Net result, base | -1 143.77 USD | -855.98 USD | -193.18 USD |
| Maximum drawdown on equity, base | 11.73 % | 13.98 % | 8.96 % |

The three ran on different months, in different market conditions, and C
has a nearer target, which raises the win rate by construction. C's
smaller loss cannot be read as C being better than A or B: the periods
differ and C's own interval spans from clearly negative to positive. All
three lose after estimated costs in every scenario that was run.

## Limits

- Yahoo's single indicative price, no bid/ask: every execution is
  ESTIMATED; spread, slippage, commission and financing are assumptions.
- D2 is not independent of D1: same source, same years, and the idea came
  from later data. It was never going to be a validation.
- 88 trades over five and a half months, with no evaluable hour in two of
  the eight months registered.
- Hourly candles: the order of prices inside an hour is unknown, and the
  drawdown is measured on hourly marks.

## Artefacts

- Study: `data/reports/backtests/d2_ema-pullback-forex-0.1.0_ad7ef187_41706d5/`
  (`manifest.json`, `summary.csv`, `signals.csv` with every evaluation, and
  per scenario `trades.csv`, `decisions.csv` with every rejected signal,
  `equity.csv`, `run.json`, `record.json`, `report.txt`).
- Offline report, costs x1.5:
  `data/reports/exports/d2_ema-pullback-forex-0.1.0_ad7ef187_41706d5/reporte_offline_costos_x1_5/strategy_C_trades_costos_x1_5.html`
- Offline report, base costs:
  `data/reports/exports/d2_ema-pullback-forex-0.1.0_ad7ef187_41706d5/reporte_offline_base/strategy_C_trades_base.html`
- Each folder also holds `strategy_C_trades.csv` and `excluded_trades.csv`.
