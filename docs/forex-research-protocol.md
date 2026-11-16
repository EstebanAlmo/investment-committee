# Research protocol for new Forex hypotheses (B5.1)

Written before any test of any new hypothesis. Its purpose is that a
result, good or bad, means what it appears to mean.

A and B stay frozen. Their study of 2026-11-12 is never rerun or
rewritten. Nothing here authorizes a backtest: each step below needs the
user's approval when its turn comes.

## 1. Data, and what each part may be used for

| Set | Period | Kind | Status | May be used for |
|---|---|---|---|---|
| D0 | 2026-03-23 to 2026-09-30 | Yahoo H1, mid | **inspected many times** (B3, B4.3, B5.0) | debugging code only. Never as evidence |
| D1 | 2024-11-14 to 2025-07-13 | Yahoo H1, mid | downloaded 2026-11-12, **not looked at** beyond the quality report | development: making the code run, counting signals. No parameter may be chosen by its return |
| D2 | 2025-07-14 to 2026-03-20 | Yahoo H1, mid | **read once on 2026-11-13** by the registered test of C (`docs/research/ema-pullback-forex-0.1.0-results.md`): inspected for C from then on | the retrospective test: **one** run per registered hypothesis. Spent for C and for any changed version of C |
| D3 | starts after the rules are frozen AND live capture begins; no start date exists yet | to be recorded as it happens | does not exist | prospective validation |

- D1 and D2 are one dataset (`docs/data-ingest.md`) split at Monday
  2025-07-14 00:00 UTC, half and half. The split is fixed here, before
  either half is read.
- D2 is older than D0. A test on it is **retrospective out-of-sample**:
  the rules were not fitted to it, but it is not prospective, it comes
  from the same source and the same market regime years, and the ideas
  came from D0, which follows it in time. It must never be described as
  prospective or independent validation.
- **Prospective means recorded forward.** A date counts as prospective only
  if, before it, the strategy's version was frozen (identifier and
  fingerprint committed) and the capture of its evaluations was already
  running and writing them as they happened. Nothing dated before both of
  those - October and November 2026 included, although they come after D0
  - may be presented as prospective validation, whenever it is downloaded
  or simulated. No monitor is running today, so D3 has not begun.
- D0, D1 and D2 are a single indicative price. No bid/ask: every execution
  is ESTIMATED.

## 2. Registration before testing

Before a hypothesis touches D2, a file `docs/research/<id>.md` is
committed with: the exact rules and parameters (or the spec and its
fingerprint), the engine version, the dataset hash, the scenarios, the
primary metric, the success, failure and abandonment criteria below filled
in, and the single configuration to be run. The commit hash and date are
the proof that it came first.

The registration is immutable: its SHA-256 is pinned in
`tests/simulation/test_research_registry.py`, so any later edit fails the
suite. Results are written to a separate file, `<id>-results.md`, never
into the registration.

## 3. Limits on what is tried

- One primary configuration per hypothesis. No parameter sweep.
- At most three hypotheses in this round (strategy C; the H3 experiment is
  descriptive and makes no profitability claim).
- A change of any rule or number after seeing D2 is a new version and
  needs data that have not been seen: D2 can be used once per hypothesis.
- Every run is reported in full, including the ones that fail.

## 4. Scenarios

The six of B4.3, unchanged, each a complete run: base, costs x1.5, costs
x2, illustrative financing 3 %, hypothetical commission 7 USD per 100 000
round trip, optimistic reading of indeterminate trades.

**The criteria are judged on costs x1.5**, not on base costs: the base
spread and slippage are assumptions that are probably too kind at the
rollover, in the Asian session and on news.

## 5. The null model

A strategy is compared with what its own stops and targets would give if
the price had no drift:

- For each trade that ends at its stop or its target, the chance of
  reaching the target first on a driftless walk from the mid price at
  entry is `down / (down + up)`, with `down = D - slippage - spread` and
  `up = T + slippage + spread` (B5.0). The null win rate is the mean of
  those chances; under it the expectancy is about minus the cost in R.
- Reported next to it: the same strategy's entries with the direction
  reversed is NOT used (it is not a null), and no "random entry"
  simulation is run on D2 beyond this analytic figure, so that D2 is read
  once.

## 6. Statistics

- Unit: the valid closed trade, in R. Indeterminate and open trades are
  reported apart, as in B4.2.
- Interval for the expectancy: 95 %, by the t distribution on the trades'
  R, and by a bootstrap with a fixed seed (10 000 resamples) as a check;
  both are reported. Trades of the two pairs at the same hours are
  correlated, so the intervals are optimistic: stated with the result.
- Win rate against the null: one-sided exact binomial test.
- Multiple hypotheses: with k hypotheses registered for D2, each test uses
  0.05 / k.
- Sample size: with a 2 R target and a win rate near 1/3, the standard
  deviation of a trade is about 1.4 R. Detecting a true edge of +0.2 R
  with 80 % power at 5 % one-sided needs about 300 trades; +0.3 R, about
  135; +0.1 R, about 1 200. D2 holds about eight months: at B's rate of
  about 20 trades per pair and month that would be about 320 trades, and C
  is expected to trade less.

## 7. Criteria, fixed now

On D2, costs x1.5, both pairs together:

| Verdict | Condition |
|---|---|
| **NO CONCLUYENTE** | fewer than 200 valid closed trades. Stated as such whatever the result, and not upgraded by other figures |
| **FALLA** | 200 or more trades and the upper end of the 95 % interval of the expectancy is below 0, or the win rate is not above the null at 0.05 / k |
| **SUPERA LA PRUEBA RETROSPECTIVA** | all of: 200 or more trades; the lower end of the 95 % interval of the expectancy is above 0; the win rate is above the null at 0.05 / k; the expectancy is positive in each pair and in each half of D2; the result stays positive at costs x2; the maximum drawdown on equity is below 15 % |
| **NO DEMOSTRADA** | anything else |

"Supera la prueba retrospectiva" means only: worth a prospective test. It
is not a claim of profitability.

## 8. After the retrospective test

1. Prospective tracking on D3 as defined in section 1: rules frozen
   first, capture running first, hypothetical trades only, recorded as
   they happen. The same criteria, on at least 200 trades. Starting it
   needs a monitor, which is not authorized today.
2. Real bid/ask data for the prospective period, so that spread and
   slippage are measured instead of assumed. This needs a data source the
   user has approved (`docs/data-ingest.md`).
3. Real money is outside this protocol. `CLAUDE.md` forbids broker
   integration and execution; changing that is the user's decision alone
   and would need its own review of costs, risk of ruin and operations.

## 9. Experiment H3: stop distance, cost and exposure

Independent of strategy C. **It is a measurement, not a strategy, and it
makes no claim that any stop is better.**

Question: when the stop is placed further away, what happens to the cost
measured in R, the size, the time in the market, the share of stops, the
financing and the net expectancy?

What arithmetic already says, to be checked rather than assumed:

- cost in R = (spread + 2 x slippage) / stop distance: it halves when the
  distance doubles;
- size = risk budget / loss per unit: it halves too, so the cost in USD
  per trade halves and the result in USD of a 1 R loss does not change;
- under the null the expectancy is about minus the cost in R, whatever the
  stop and target. A wider stop therefore loses more slowly per trade; it
  cannot by itself make a positive expectancy.

Design:

| | |
|---|---|
| Entries | the signals of B, frozen, exactly as recorded - chosen because B is the best-understood set of entries and was indistinguishable from the null |
| Cells | stop 1.5 ATR or 3.0 ATR, crossed with target 2 R or 3 R: four runs. 1.5 ATR / 3 R is B itself |
| Everything else | B's rules, limits, sessions and the six scenarios |
| Data | D1 for a first reading; D2 only if the user decides the measurement is worth a D2 run of its own |
| Reported per cell | trades, cost in R, median size, hours in the market, share of stops, share of time and weekly exits, financing nights, expectancy with its interval, the null win rate |

What it can show: whether the measured cost and exposure follow the
arithmetic, and how many more trades end by time or by the weekly close
when both levels are further away. What it cannot show: that a stop is
"better". Four cells on one period will differ by chance; no cell is to be
picked for its result. If H3 is ever used to choose a stop for a strategy,
that choice must be registered before D2 is read for that strategy.

## 10. What invalidates a result

- Any rule, parameter, scenario or criterion changed after D2 was read.
- A second run on D2 for the same hypothesis.
- A dataset whose hash is not the registered one.
- A code change between registration and the run other than a bug fix
  with its own test, disclosed with the result.

## 11. Coverage of D1 and D2, and what D2 can show (B5.2)

Counted on 2026-11-12 and 2026-11-13. D1: C's signals only, with no entry,
exit or result simulated. D2: from the bars' timestamps alone, with every
price replaced by a constant, so no price of D2 was read.

| | EUR/USD | GBP/USD |
|---|---|---|
| D1 bars / expected hours / missing | 4 025 / 4 059 / 34 | 4 025 / 4 059 / 34 |
| D2 bars / expected hours / missing | 4 186 / 4 247 / 61 | 4 188 / 4 247 / 59 |
| D1 evaluable hours (from 2025-02-12 23:00 UTC) | 2 377 | 2 377 |
| D1 hours refused for incomplete data | 942 | 941 |
| D1 signals of C (LONG + SHORT) | 42 + 28 = 70 | 56 + 29 = 85 |
| D1 pullbacks not confirmed | 83 | 102 |
| D2 evaluable hours | 2 760 | 2 755 |
| D2 hours refused for incomplete data | 1 426 | 1 433 |

D2's evaluable hours by month, per pair: July 2025 331, August 494,
September 468 (463 on GBP/USD), October 492, November 472, December 435,
**January and February 2026 none**, March 2026 68. A few dozen missing
bars are enough: the gate needs at most 7 missing hours in 720 and none in
the last 50, so one hole silences up to 720 hours after it.

What follows, before any run:

- D2 is in effect five and a half months, not eight. Section 6's estimate
  of about 320 trades does not hold.
- At D1's rate (155 signals in 4 754 evaluable pair-hours) D2 would give
  about 180 signals of C before the entry filters, and fewer trades. The
  200 valid closed trades of section 7 are unlikely to be reached: the
  expected verdict of a D2 run of C is **NO CONCLUYENTE**.
- The threshold is not lowered to fit the data. Reaching it needs more
  data that have not been seen (an older or a different source, or the
  prospective period), which is the user's decision.
- With about 120 trades, only an edge near +0.3 R or more would be
  detectable at all (section 6), and it would still not be a verdict.

The registration of C is `docs/research/ema-pullback-forex-0.1.0.md`.

## 12. The registered test of C (B5.4)

Run once on 2026-11-13, authorized by the user. 88 valid closed trades at
costs x1.5, fewer than the 200 of section 7: **NO CONCLUYENTE**, as
section 11 expected. Expectancy -0.121 R with a 95 % interval from -0.41
to +0.16 R; the target came first as often as a driftless price would
give. The full record, the audit of D2's holes and the limits are in
`docs/research/ema-pullback-forex-0.1.0-results.md`. Nothing in that
result may be used to adjust a parameter of C.

The audit found no ingest failure. The holes are holidays the calendar
does not model (25 December, 1 January), 25 hours absent from Yahoo on 30
January to 2 February 2026, and a one-hour mismatch of the calendar on
the Sundays when the clocks of the United States and Europe differ.
