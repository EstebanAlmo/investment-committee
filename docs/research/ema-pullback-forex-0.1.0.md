# Registration: `ema-pullback-forex@0.1.0` (strategy C) on D2

Registered on 2026-11-13, under `docs/forex-research-protocol.md`, **before
any profitability run of C on any data**. Nothing has been run. The run
itself needs the user's explicit authorization; this file does not give it.

This file is immutable. Its SHA-256 is pinned in
`tests/simulation/test_research_registry.py`: an edit fails the suite.
Until the user authorizes the run, a correction is possible only as a
commit that changes this file and its pinned hash together, visible in the
history. After the run, never. Results go to
`ema-pullback-forex-0.1.0-results.md`, whatever they are.

## 1. Hypothesis

In an established trend (EMA20, EMA50, EMA120 in strict order), entering
when a pullback to EMA20 ends, with the stop behind the pullback and a
target of 2 R, has a positive expectancy net of the estimated costs.

The idea came from D0 (the loss audit of B5.0), so D0 cannot test it.

## 2. Rules, parameters and code

| | |
|---|---|
| Specification | `docs/specs/spec-007-ema-pullback-forex.md`, as committed in `51c60ab` |
| Strategy | `ema-pullback-forex@0.1.0` |
| Parameters fingerprint (SHA-256) | `c4891b951204320a8fffb22539f0a4ec399c5094f4cf4bb56039f3fa97debe71` |
| Parameters | H1; EMA 9 / 20 / 50 / 120; ATR14; window 720 expected hours, at most 7 missing, none in the last 50; longest leg 12 candles; stop buffer 0.10 ATR, floor 1.0 ATR, cap 3.0 ATR; target 2 R net |
| Signals | `simulation/ema_pullback.py` |
| Proposals and stops | `simulation/trade_proposal.py` (structural stop) |
| Engine | `ema-forex-backtest@1.0.0`, `simulation/forex_backtest.py` |
| Code state | commit `51c60ab` |

A code change between this registration and the run, other than a bug fix
with its own test and disclosed with the result, invalidates the result.

## 3. Data

| | |
|---|---|
| Dataset | `yahoo_fx_1h_20241114_20260323` (`docs/data-ingest.md`) |
| Dataset SHA-256 | `a8f7111623dbd578d072ea1a04b4a218c4ac7d75fcbd83abeb06fe0404140b91` |
| Kind | Yahoo H1, one indicative mid price, no bid/ask: every execution is ESTIMATED |
| D2 | bars labelled from 2025-07-14 00:00 UTC to 2026-03-20 20:00 UTC |
| Calendar | `yahoo_week` |
| Instruments | EUR/USD and GBP/USD, EUR/USD processed first |

The first evaluations of D2 read, in their 720-hour window, bars of D1.
Those bars only warm the indicators up: no signal, entry or result dated
before 2025-07-14 00:00 UTC belongs to the test.

## 4. The single configuration

The default parameters above, both pairs, one hypothetical account of
10 000 USD, the existing risk limits, sessions and cost model, unchanged.
One study: the six scenarios of the protocol (base, costs x1.5, costs x2,
illustrative financing 3 %, hypothetical commission 7 USD per 100 000
round trip, optimistic reading of indeterminate trades). No other
configuration, no sweep, no second run.

## 5. Primary metric and statistics

- Primary metric: the expectancy in R of the valid closed trades, both
  pairs together, **at costs x1.5**.
- Interval: 95 %, by the t distribution, and by a bootstrap of 10 000
  resamples with seed 20261113 as a check. Both reported.
- Null win rate: the driftless figure of the protocol, section 5.
  One-sided exact binomial test.
- Multiplicity: k = 3, the most hypotheses this round allows, fixed now so
  that it cannot shrink later. Each test uses 0.05 / 3.

## 6. Criteria

On D2, costs x1.5, both pairs together:

| Verdict | Condition |
|---|---|
| **NO CONCLUYENTE** | fewer than 200 valid closed trades. Stated as such whatever the result, and not upgraded by other figures |
| **FALLA** | 200 or more trades and the upper end of the 95 % interval of the expectancy is below 0, or the win rate is not above the null at 0.05 / 3 |
| **SUPERA LA PRUEBA RETROSPECTIVA** | all of: 200 or more trades; the lower end of the 95 % interval of the expectancy is above 0; the win rate is above the null at 0.05 / 3; the expectancy is positive in each pair and in each half of D2; the result stays positive at costs x2; the maximum drawdown on equity is below 15 % |
| **NO DEMOSTRADA** | anything else |

"Supera la prueba retrospectiva" means only: worth a prospective test. It
is not a claim of profitability, and D2 is not an independent validation.

## 7. Abandonment

- **FALLA:** version 0.1.0 is abandoned. No prospective tracking, no
  second run, no parameter of it adjusted on D0, D1 or D2.
- **NO CONCLUYENTE or NO DEMOSTRADA:** no claim is made and the version is
  not promoted. D2 is spent for this hypothesis; going on needs data not
  yet seen, and the user's decision.
- **Any verdict:** a changed rule or number is a new version with a new
  registration, and cannot be tested on D2.
- **Before the run:** if the dataset's hash is not the one above, or the
  code is not the registered state, the run does not start.

## 8. What the sample is expected to be (known before the run)

Counted without reading any price outcome:

- D1, signals of C (counts only, no entry or exit simulated): 2 377
  evaluable hours per pair, from 2025-02-12 to 2025-07-13; 70 signals on
  EUR/USD and 85 on GBP/USD: 155 signals in 4 754 evaluable pair-hours.
- D2, from the bars' timestamps alone (no D2 price read): 2 760 evaluable
  hours on EUR/USD and 2 755 on GBP/USD. **None in January or February
  2026** and 68 per pair in March 2026: the data gate refuses those hours
  for missing bars. In effect D2 covers 2025-07-14 to December 2025.
- At D1's rate that is about 180 signals in D2, before the entry filters.
  A and B opened between 60 % and 76 % of their signals in B4.3.

So fewer than 200 valid closed trades are expected, and **NO CONCLUYENTE
is the likely verdict**. That is accepted in advance: the threshold of 200
is not lowered, and no figure of a smaller sample will be presented as
evidence for or against C.
