# Spec 007 - Strategy C: `ema-pullback-forex@0.1.0`

Status: **rules approved by the user on 2026-11-12 and implemented (B5.2):
signals in `simulation/ema_pullback.py`, structural stops in the shared
proposal engine. Not validated.** Every number is a hypothesis fixed before
looking at results. The registered hypothesis and its criteria are in
`docs/research/ema-pullback-forex-0.1.0.md`; its single retrospective test
on D2 (2026-11-13) was NO CONCLUYENTE, 88 trades of the 200 required
(`docs/research/ema-pullback-forex-0.1.0-results.md`). No rule or number
here was changed after it.

Origin: B5.0 (`docs/ema-forex-backtest.md`, loss audit of 2026-11-12).
The frozen strategies A and B enter on a moving-average cross; B behaved
like random entries paying costs, half of all stops came within six hours,
and A did worse after a directional day - consistent with entering late.
C tests one idea: **in an established trend, enter when a pullback ends,
with the stop behind the pullback.** That idea came from data already
seen, so those data cannot test it.

A and B stay frozen and untouched. C would be a third, independent
strategy with its own identifier, fingerprint and hypothetical account.

## 1. Scope

| | |
|---|---|
| Instruments | EUR/USD, GBP/USD |
| Timeframe | H1, closed candles only, labelled by their open |
| Direction | LONG and SHORT, mirrored |
| Data gates | those of `simulation/signals.py`, unchanged: window of 720 expected hours, at most 7 missing, none in the last 50 |
| Account | its own 10 000 USD hypothetical account; one position per pair |

The 720-hour window is B's, for B's reason: after 600 further hours the
seed of an EMA120 weighs about 4.5e-5 (`docs/ema-trend-cross-signals.md`).

## 2. Indicators (all on closed candles)

EMA9, EMA20, EMA50, EMA120 of the close and Wilder's ATR14, from
`simulation/wilder.py`, computed on the window that ends at the candle
being evaluated. Subscript `t` is the candle; `c` is the candle that just
closed.

## 3. The rule, LONG (SHORT is the mirror image)

A LONG signal exists at the close of candle `c` if and only if **all** of:

1. **Trend at `c`:** `EMA20_c > EMA50_c > EMA120_c` (strict).
2. **Confirmation at `c`:** `close_c > EMA9_c`, `close_c > EMA20_c` and
   `close_c > open_c` (a bullish candle; for a SHORT, `close_c < open_c`).
3. **A retracement leg just ended:** `close_(c-1) <= EMA9_(c-1)`. Let the
   leg be the longest run of consecutive candles `c-n ... c-1` (n >= 1)
   that each closed at or below their own EMA9. So `c` is the first close
   back above the fast average.
4. **The leg is short:** `n <= 12`.
5. **The leg reached the trend average:** at least one candle of the leg
   has `low <= EMA20` of that candle.
6. **The trend survived the leg:** at every candle of the leg,
   `EMA20 > EMA50 > EMA120`, and no candle of the leg closed below its
   EMA50.
7. **The leg was observed whole:** `c-n ... c` are consecutive expected
   hours with a bar each - no data hole and no market closure inside.

SHORT: every inequality reversed, `high >= EMA20` for the touch, highs for
the structure.

The rule is a pure function of the window ending at `c`: there is no
state carried from one hour to the next, so evaluating the same candle
twice gives the same answer, and nothing after `c` is read.

| Term | Definition |
|---|---|
| Pullback | the leg of rule 3 together with rule 5: closes at or below EMA9 that reach EMA20, inside an intact trend |
| Confirmation | rule 2 at the first candle after the leg |
| Invalidation | any of rules 4, 6 or 7 failing; the pullback simply gives no signal |
| Pullback extreme | LONG: `L` = lowest low of candles `c-n ... c`. SHORT: `H` = highest high |

Equalities: a low exactly at EMA20 is a touch; a close exactly at EMA9 is
still part of the leg and is not a confirmation; equal averages are not a
trend.

## 4. Entry

At the first price after `c` closes, through the existing proposal engine:
the open of the next bar, as an ESTIMATED quote, with the existing checks
(no bar, no entry; open further than 0.25 ATR from `close_c`: rejected).
The signal is never retried.

## 5. Stop

Structural, with ATR limits. With `E` the estimated entry and `A = ATR14_c`:

- LONG: `SL = L - 0.10 x A`, rounded to the tick.
- If `E - SL < 1.0 x A`: `SL = E - 1.0 x A` (a floor: a stop inside the
  noise of one hour is not structural).
- If `E - SL > 3.0 x A`: **no trade** (`RETROCESO_DEMASIADO_PROFUNDO`).

The engine compares the stop with the estimated bid (LONG) or ask (SHORT),
as it does now; `L` is a mid-price low, so the 0.10 ATR buffer also has to
cover half the assumed spread.

**Implemented:** a signal may carry a `signals.StructuralStop` (reference
level, floor and cap in ATR). `build_proposal` uses it when present; A's
and B's evaluations carry none and take the unchanged `1.5 x ATR` path.
Their golden tests, the engine's tests and the pinned results hash pass
without any edit. The cap is inclusive (exactly 3.0 ATR trades); the
reference is rounded to the nearest tick, half to even, like every stop; a
reference on the wrong side of the entry (the next bar opened beyond the
pullback's extreme) falls under the floor rule.

## 6. Target, size, risk

- Target: the smallest tick multiple that gives at least **2 R** net of
  the estimated costs, by the existing formula with ratio 2.
- Size: `floor(0.5 % of the balance / estimated loss at the stop)`, in
  steps of 1 000 units - unchanged.
- Limits: unchanged (daily loss 1.5 %, same side of the dollar 1 %, margin
  75 %, one position per pair).

## 7. Sessions, spread, exits

Unchanged from `simulation/ema_cross_sessions.py` and the proposal engine:
no entry while the week is closed, on Fridays from 12:00 New York, or from
16:00 to 18:00 New York; no entry if the spread exceeds 2.5 pips (EUR/USD)
or 3.0 (GBP/USD); exit at the stop, the target, after 72 bars of open
market, or at Friday 16:00 New York. No trailing stop, no partial exit, no
move to break-even: one fixed stop and one fixed target.

No session filter is added. B5.0 showed B's entries in the Asian session
doing better, on 37 trades, one cut among dozens, in the session where the
real spread is widest: not a reason for a rule.

## 8. Simultaneous signals and other cases

| Case | Treatment |
|---|---|
| LONG and SHORT on one pair at one candle | impossible: the two trends exclude each other |
| Both pairs at one candle | EUR/USD first, then GBP/USD, which sees the first position's risk |
| Signal while the pair has a position | rejected (`POSICION_ABIERTA`); not queued |
| Another signal a few candles later | allowed if the rule holds again: it needs a new leg, so at least one close at or below EMA9 after the previous confirmation |
| The leg spans a weekend or a data hole | no signal (rule 7) |
| The window fails the data gates | no evaluation, as in A and B |
| Stop distance beyond 3 ATR | no trade |
| Estimated costs make the size smaller than 1 000 units | rejected (`TAMAÑO_INSUFICIENTE`), as now |

## 9. Parameters

Inherited without change (not new, but not proven either): H1, the data
gates, EMA 9 / 20 / 50 / 120, ATR14, the risk limits, the session rules,
the entry and cost model.

New, and each one an unproven hypothesis:

| Parameter | Value | Why this value |
|---|---|---|
| Longest leg | 12 candles | half a trading day; a longer pause is a range, not a pullback |
| Stop buffer | 0.10 ATR | must exceed half the assumed spread (0.5-0.75 pip against an ATR near 10 pips) |
| Stop floor | 1.0 ATR | below one hour's average range a stop is noise; B5.0: a third of stops within two candles at 1.5 ATR |
| Stop cap | 3.0 ATR | beyond that the "pullback" is a reversal, and the position would be very small |
| Net reward | 2 R | see below |

Five new numbers. None was fitted; none may be changed after a result is
seen - a change is a new version.

Why 2 R and not 3 R: it is **not** because 2 R was found to work. In B5.0
the share of B's trades reaching +1 R, +2 R and +3 R (43 %, 27 %, 20 %)
matched what a driftless price gives; no target stands out. 2 R is chosen
because a structural stop is usually wider than 1.5 ATR, so a 3 R target
would rarely be reached within 72 hours. The alternative, keeping 3 R so
that C differs from B only in its entry, is cleaner as an experiment: an
open question (below).

## 10. Identifier

`ema-pullback-forex@0.1.0`, with a SHA-256 fingerprint of its parameters
like A and B. It is not added to the frozen registry.

## 11. No lookahead

- Every condition reads candles at or before `c`.
- The pullback extreme includes `c` itself and nothing later.
- The entry is the next bar's open; that bar's high, low and close are
  read only for exits.
- The rule is stateless, so a re-evaluation cannot leak anything.
- To be tested as for A and B: cutting the series at any instant, or
  altering every later bar, changes nothing earlier.

## 12. What is assumed, not known

- That a pullback entry has any edge. B's signals are, in effect,
  fast-average re-crosses inside a trend, and showed none.
- That the EMA20 touch and the EMA9 close-back are the right markers.
- All five new parameters.
- That costs will be those assumed: spread, slippage and financing are
  unverified, and C would trade less often than B, so each trade matters.
- That enough trades will exist to conclude anything (the protocol says
  how many are needed).

## 13. Decisions taken by the user (2026-11-12)

1. Target: **2 R** net estimated.
2. Confirmation: close back beyond EMA9 **and** a candle closing in the
   direction of the trade (`close > open` for a LONG).
3. Rule 6 kept whole: the order of the averages, and no close beyond EMA50
   against the trend during the leg.
4. Stop: structural, buffer 0.10 ATR, floor 1.0 ATR, cap 3.0 ATR.
5. Entry at the next available bar's open; existing risk limits, sessions,
   exposure and capital.

These are predefined hypotheses, not demonstrated improvements.

## 14. Ambiguities found in this spec and how the code resolves them

| Ambiguity | Resolution |
|---|---|
| Which EMA value belongs to an earlier candle of the leg | all averages are computed once, on the window ending at `c`; earlier candles read those same series (not the value an evaluation at that earlier hour would have given, which differs only by the seed's weight) |
| "A candle of the leg closed below EMA50" when it closes exactly on it | exactly on EMA50 is allowed; only strictly beyond breaks the rule |
| The leg when more than 12 candles closed at or below EMA9 | counted up to 13 and reported as `RETROCESO_DEMASIADO_LARGO`; how much longer it was is not measured |
| Rule 7 and the data gates | a missing hour among the last 50 is already refused by the gate (`DATOS_INCOMPLETOS`); rule 7 then catches a leg that spans a market closure, which the gate accepts |
| Names of the outcomes | the outcome enum is shared with A and B and is not changed: "nothing to confirm" is `SIN_CRUCE`, a pullback that fails a condition is `CRUCE_SIN_CONFIRMACION`; the reasons carry C's own vocabulary |
| What is reported when several conditions fail | every failed condition, in a fixed order |
| Where the floor and the cap are applied | in `build_proposal`, because the entry is only known there; the signal carries the reference level and both limits |
| The 2 R target | belongs to the strategy (`net_reward_risk` in its parameters and fingerprint); the backtest engine reads it by strategy identifier, leaving the default `ExecutionParameters` (A's and B's) untouched |
| A signal one candle after another | possible only after a new leg, so never on consecutive candles; if the pair still has a position it is rejected (`POSICION_ABIERTA`) |
