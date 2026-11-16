# `ema-rsi-atr-forex@0.1.0`: trade proposals and settlement (Entrega 3)

Status on 2026-11-11: **EXPERIMENTAL, NOT VALIDATED.** This delivery turns a
confirmed signal plus a quote into a concrete proposal (entry, stop, target,
size, costs) and defines the arithmetic of how such a hypothetical trade
settles. There is still no backtest loop, no monitor, no database and no
order of any kind. Nothing here was run on real bid/ask data: none exists
in the project yet.

## Files

| File | Content |
|---|---|
| `src/investment_committee/simulation/trade_proposal.py` | `build_proposal`, `settle`, `indeterminate`, instrument rules, execution parameters, financing assumption |
| `src/investment_committee/simulation/ema_cross_sessions.py` | entry blocks, weekly close, 72-bar time exit, financing nights (New York time) |
| `src/investment_committee/simulation/ema_cross.py` | changed: continuity is validated on the expected-hours grid before the window is built (`docs/ema-rsi-atr-signals.md`) |
| `tests/simulation/test_trade_proposal.py`, `test_ema_cross_sessions.py` | tests, including an independent exact-fraction reference |

The frozen strategies, `sessions.py`, `execution.py`, the replay and both
monitors are untouched. `RiskLimits`, `AccountState` and `OpenPosition`
(`simulation/opportunities.py`) are reused as they are.

## Formulas

Per unit of base currency, in the quote currency (USD for both pairs, so
P&L is in USD with no conversion). `sign` is +1 for LONG, -1 for SHORT.

| Symbol | Meaning |
|---|---|
| `slip` | adverse slippage per market fill (assumed: 0.2 pips EUR/USD, 0.3 GBP/USD) |
| `c` | round-trip commission per unit = `commission_per_100k / 100000` (default 0) |
| `phi` | financing per unit per night = `E x annual_rate / 360` |
| `N` | financing nights of the planned horizon; `F = N x phi` |

```
                 LONG                            SHORT
Entry     E  = ask + slip                  E  = bid - slip          rounded against the trader
Stop      SL = E - 1.5 x ATR14             SL = E + 1.5 x ATR14     nearest tick;  D = |E - SL|
  triggers   bid <= SL                     ask >= SL
  fills      SL - slip                     SL + slip                 or beyond, if the market gapped through
Loss      l  = D + slip + c + F
Target    T  = smallest tick multiple with  T >= 3 x (D + slip) + 4 x (c + F)
          TP = E + T                       TP = E - T
  triggers   bid >= TP                     ask <= TP
  fills      TP exactly                    TP exactly
Gain      g  = T - c - F                   (so g >= 3 x l)
Units     floor(0.005 x equity / l / 1000) x 1000
```

## Nothing is counted twice

- The **spread** is paid only through the prices: in at the ask, out at
  the bid (the reverse for a SHORT). It is reported (`spread_cost`) as
  information and never subtracted.
- The **entry slippage** is inside `E` (`entry_slippage_cost`, information).
- The **stop's slippage** is the only slippage in `l`. A target fills
  without slippage.
- **Commission and financing** are the only amounts subtracted outside the
  prices: once from the gain, once added to the loss. Hence the factor 4.
- Realized: `units x (exit - E) x sign - commission - financing for the
  nights actually crossed`.

## Three money figures

| Field | What it is |
|---|---|
| `risk_budget` | 0.5 % of equity: what may be put at risk when opening |
| `estimated_loss_at_stop` | `units x l`: the loss if the stop fills normally; never above the budget after units are rounded down |
| `TradeResult.net_pnl` | what actually happens. A gap or extraordinary slippage makes the loss larger than both: `loss_beyond_estimate`, `exceeded_budget`. **A stop does not cap the loss.** |

## Financing

Configurable (`FinancingAssumption`), cost-only (both sides pay, nothing is
credited), with a status that travels with every proposal and result:

| Status | When | Effect |
|---|---|---|
| `NO_VERIFICADA` (default) | no rate | `F = 0`; flag `COSTO_FINANCIACION_NO_VERIFICADO`; `net_reward_risk` is empty: no claim about a ratio after financing |
| `ESCENARIO_ILUSTRATIVO` | a rate given for illustration | used in the numbers; same flag; `net_3r_verified` is False |
| `VERIFICADA` | a rate with a documented source, explicitly marked verified | no flag; `net_3r_verified` is True |

**No real rate is configured anywhere.** The 3 % a year in the examples is
an illustration of the arithmetic only.

`N` is the number of 17:00 New York rollovers the position would cross if
it lasted its maximum (72 bars or the weekly close, whichever is first),
Monday to Thursday, the Wednesday one counting three: the conservative
case. The target is fixed with that `N` when the proposal is made and is
never moved afterwards. A trade that leaves sooner pays fewer nights; its
result records the nights actually crossed, next to the planned profit.

## Worked examples (10 000 USD; illustrative prices; illustrative 3 % rate)

| | EUR/USD LONG | GBP/USD SHORT |
|---|---|---|
| Quote bid / ask | 1.08490 / 1.08500 | 1.27400 / 1.27415 |
| ATR14, slippage | 10.0 pips, 0.2 pips | 14.0 pips, 0.3 pips |
| Quote time | Tuesday 09:00 New York | Thursday 08:00 New York |
| Planned nights, horizon end | 5, Friday 09:00 New York | 1, Friday 16:00 New York |
| Entry | 1.08502 | 1.27397 |
| Stop loss | 1.08352 (15.0 pips) | 1.27607 (21.0 pips) |
| Take profit | 1.09139 (63.7 pips, 6.37 ATR) | 1.26715 (68.2 pips, 4.87 ATR) |
| Take profit with no rate configured | 1.08958 | 1.26758 |
| Units | 25 000 | 22 000 |
| Risk budget | 50.00 | 50.00 |
| Estimated loss at the stop | 49.30 | 49.20 |
| - of which price / stop slippage / financing | 37.50 / 0.50 / 11.30 | 46.20 / 0.66 / 2.34 |
| Expected net profit | 147.95 | 147.70 |
| Reward/risk: net / before financing / gross | 3.001 / 4.19 / 4.25 | 3.002 / 3.20 / 3.25 |
| Notional, margin at 30:1 | 27 125.50 / 904.18 | 28 027.34 / 934.24 |
| Spread (information) | 2.50 | 3.30 |
| Flags | `COSTO_FINANCIACION_NO_VERIFICADO` | `COSTO_FINANCIACION_NO_VERIFICADO` |

How they settle (same illustrative rate):

| Case | Exit | Nights | Net result |
|---|---|---|---|
| EUR/USD target reached 3 hours later | 1.09139 | 0 | +159.25 (planned +147.95; the target did not move) |
| EUR/USD target reached after one rollover | 1.09139 | 1 | +156.99 |
| EUR/USD normal stop at the horizon | 1.08350 | 5 | -49.30 |
| EUR/USD, no rate: stop gapped, bid reopens at 1.08200 | 1.08198 | - | -97.28 with 32 000 units: twice the estimated 48.64, above the 50.00 budget |
| EUR/USD order of stop and target unknown | - | 0 | indeterminate: -38.00 (conservative) or +159.25 (optimistic) |

With the illustrative rate, five nights move the EUR/USD target from 45.6
to 63.7 pips while the stop stays 15 pips away: financing weighs heavily on
this strategy, and its real level is unknown.

## Entry rules (a proposal is rejected, with its reason, when)

| Reason | Rule |
|---|---|
| `SIN_SEÑAL_CONFIRMADA` | the evaluation is not LONG or SHORT |
| `INSTRUMENTO_NO_SOPORTADO` | not EUR/USD or GBP/USD |
| `COTIZACION_AUSENTE`, `COTIZACION_DE_OTRO_INSTRUMENTO`, `COTIZACION_INVALIDA` | no quote; another pair; bid not positive or above the ask |
| `COTIZACION_ANTERIOR_AL_CIERRE` | the quote is from before the signal candle closed |
| `SEÑAL_EXPIRADA` | the quote is more than 5 minutes after the close |
| `COTIZACION_DESACTUALIZADA` | more than 30 seconds between the quote's time and its reception (or a negative interval) |
| `MERCADO_CERRADO`, `ENTRADA_BLOQUEADA_VIERNES`, `ENTRADA_BLOQUEADA_ROLLOVER` | FX week closed; Friday from 12:00 New York; 16:00-18:00 New York |
| `SPREAD_EXCESIVO` | spread above 2.5 pips (EUR/USD) or 3.0 pips (GBP/USD) |
| `PRECIO_DESPLAZADO` | quote mid more than 0.25 ATR from the signal candle's close (this also covers an opening gap) |
| `RELACION_NO_CALCULABLE` | no positive ATR, or no valid stop/target |
| `POSICION_ABIERTA` | the pair already has an open position (an opposite signal is rejected too) |
| `TAMAÑO_INSUFICIENTE` | fewer than 1 000 units fit the budget |
| `MARGEN_INSUFICIENTE` | used margin + this margin above 75 % of equity |
| `LIMITE_PERDIDA_DIARIA` | open risk + this risk above what is left of 1.5 % of the day's starting equity |
| `EXPOSICION_USD` | risk on the same side of the dollar above 1 % of equity |

The first five groups stop at the first failure; the risk controls are all
checked and all reported. Limits are inclusive: exactly at the limit passes.

## Exits (arithmetic only; finding them in price data is Entrega 4)

- **Target**: fills at its level.
- **Stop**: fills at its level less slippage, or at the first exit-side
  price beyond it less slippage (a gap).
- **Time**: at the market when 72 hourly bars of open market have passed,
  counting the entry's bar.
- **Weekly close**: at the market, Friday 16:00 New York.
- Market exits fill on the exit side (bid for a LONG, ask for a SHORT)
  with slippage.
- **Indeterminate**: when stop and target could both have been reached and
  observed data cannot order them, or no observed data cover the period.
  Both results are kept (`conservative` = stop, `optimistic` = target); the
  trade is not a valid closed trade; the hypothetical account books the
  conservative one so later sizing never benefits from an unknown.
- The rollover window blocks entries only; stops and targets stay live.
- Holidays: none is assumed. With no quotes there is no entry, and an
  open position keeps its levels; when quotes return, the gap rule applies.
- All session times are New York local times converted with the time-zone
  database; tested across the changes of 1 November 2026 and 14 March 2027.

## Valid closed trade (for the prospective metrics)

All of: the signal was detected live, inside the window, by the frozen
version, with the monitor running at the candle's close; the proposal was
accepted on a quote that passed every rule; the hypothetical entry was
recorded before any later data; it left by target, stop, time or weekly
close at a determined price; it is not indeterminate and no incident
invalidates it. The first three conditions belong to the monitor (a later
delivery); this module supplies the last two.

## Observed, estimated, synthetic

A proposal's kind is the weakest of its signal's bars and its quote
(`OBSERVADA` > `ESTIMADA` > `SINTETICA`), and the result inherits it. A
proposal that is not `OBSERVADA` carries `PRECIO_ESTIMADO_NO_EJECUTABLE`
(or `DATOS_SINTETICOS`). Yahoo-based runs build their quote with
`estimated_from_mid`, which writes the assumed spread into the quote's
source: a simulation on estimated prices, never an executable quote. Every
proposal and result is `hypothetical`.

## Precision

Prices on a 0.00001 tick (pip 0.0001). Entry and market exits round against
the trader; the stop to the nearest tick (half to even); the target away
from the entry. Money in USD cents, half to even. Units in multiples of
1 000, rounded down. Ratios are compared exactly, never after rounding.
Decimal arithmetic throughout: the same results on Windows and macOS, with
no platform-specific dependency (standard library only).

## Not verifiable yet

| Assumption | Value used | What would verify it |
|---|---|---|
| Spread, and its profile by hour | quote's own; 1.0 / 1.5 pips when estimating from a mid price | bid/ask history of the chosen source |
| Slippage; a stop filling at its level less slippage | 0.2 / 0.3 pips | cannot be measured without execution; sensitivity scenarios (x1.5, x2) |
| Order of stop and target inside a bar | not decided here | tick or M1 bid/ask data |
| Financing rate | none | a published financing table from a documented source |
| Wednesday counts three; 360-day year | assumed | the same source |
| Leverage 30:1, commission 0 | assumed account | a decision of the protocol |
| 5-minute and 30-second quote limits are achievable | assumed | a live read-only quote source |
| Behaviour on holidays; the provider's weekly calendar | none assumed | the chosen source's data and published calendar |
| Indicative quotes resemble executable ones | not assumed | cannot be verified without a broker; stated as a limitation |

## Commands

```
Windows:  .venv\Scripts\python.exe -m pytest tests\simulation\test_trade_proposal.py tests\simulation\test_ema_cross_sessions.py
macOS:    .venv/bin/python -m pytest tests/simulation/test_trade_proposal.py tests/simulation/test_ema_cross_sessions.py
```
