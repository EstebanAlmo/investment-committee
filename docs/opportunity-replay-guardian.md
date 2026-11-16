# Opportunity Scanner: Historical Replay, Tracker and Portfolio Guardian

Assumptions, limits and how-to for `/oportunidades`, `/simular_mes`,
`/seguimiento`, `/portafolio` and `/alertas`. Nothing here sends an order,
connects a broker, or opens a simulated trade in the journal.

## Kinds of evidence (never mixed)

| Kind | Where | What it can claim |
|---|---|---|
| Retrospective backtest | `/simular_mes`, `scripts/replay_month.py`, `simulation/replay.py` | What following the scanner *would have* done on data already known. Not validation. |
| Out-of-sample validation | `DatedValidation` records (none exist) | Only evidence produced before a decision may mark a strategy VALIDADA at that decision. |
| Prospective tracking | `/seguimiento`, `simulation/tracker.py` | Hypothetical result of candidates emitted live, from data that arrived after each detection. Not P&L. |

Policies A, B and C are compared side by side with "no trading". The
report never names a winner: picking the best policy after seeing a month
proves nothing.

## Time integrity (Phase 0)

- Yahoo labels 1-hour bars by their **open** time. Verified on 2026-10-13
  for EUR/USD, GBP/USD and USD/JPY: every 1-hour bar stamped *t* equals the
  5-minute bars of *[t, t+1h)* exactly (and none of *[t-1h, t)*).
- A bar labelled *t* is usable only at or after *t + 1h*. Bars stamped in
  the future, the still-forming bar, and rows off the hourly grid (a
  provider "live" row) are dropped and counted (`prepare_bars`).
- The report says "última vela COMPLETA usada: abrió 21:00 y cerró 22:00".
  A report at 22:19 that showed "22:00" meant the *close* of the 21:00 bar;
  the bar opened at 22:00 was already excluded.

## Session calendar

- FX week: Sunday 17:00 to Friday 17:00 **New York** (`America/New_York`),
  so 21:00 UTC in summer and 22:00 UTC in winter.
- Session day: 17:00 New York to 17:00 New York, named by its end date.
- Day-trading rules: no entry decided at or after 15:00 New York, every
  position flat by 16:00 New York. In the replay, no entry whose flat time
  falls after the end of the period.
- Not modelled: bank holidays, thin Christmas / New Year sessions (they
  show up as missing bars).

## Replay execution

- The scanner sees only bars closed by each decision moment, and the
  account as it is at that moment, per policy.
- Entry: the open of the bar starting at the decision, plus half the
  spread and the slippage. No bar there means no entry ("sin precio
  ejecutable"). Stop and target are re-derived from the fill.
- Exits (backtester rules, unchanged): stop with slippage, or at a worse
  open on a gap; target exactly at the target; **both in one bar → stop**;
  forced exit at the 16:00 New York flat time.
- Limits: 0.5 % risk per trade, 1.5 % daily loss on the session's start
  equity (realized losses plus the open risk of positions), at most 1 %
  combined risk on the same USD side (EUR/USD and GBP/USD LONG are both
  short USD), margin with 30:1 leverage, one position per instrument.
  The risk is re-checked at the fill price. A stop does not cap a loss:
  gaps can make a day's loss exceed 1.5 %.
- USD/JPY: P&L in JPY converted at the exit price. Sizing uses the
  conservative rate (the lower of entry and stop).
- Cost assumptions (Yahoo has no bid/ask): EUR/USD spread 1.0 pip and
  slippage 0.2 pip per fill; GBP/USD 1.5 and 0.3; USD/JPY 1.2 and 0.3.
- AI: the replay never asks DeepSeek and never claims to reproduce it.
  Only AI ledger records written at the time (stance NO_OPERAR) can block
  policy C, within one hour of the record.

## Definitions confirmed by the integrity audit (2026-10-13)

- Per-trade risk: 0.5 % of the account's **mark-to-market equity at the
  decision** (balance plus unrealized P&L at the exit side). Above 10 000
  USD of equity a trade's risk is above 50 USD (up to 52.29 in September
  2026); no trade exceeded 0.5 % of its own decision-time equity.
- Daily loss: 1.5 % of the equity at the **first moment of the session**
  (17:00 New York), not a fixed 150 USD. Before every entry, the session's
  realized losses plus the planned risk of open positions plus the new
  risk must fit. A gap or slippage beyond a stop can still take a day past
  the limit; in September 2026 none did, measured against each day's own
  start equity.
- Policy B opens at most one position per decision moment, but can hold up
  to three at once (one per instrument), opened at different hours. Its
  path, and therefore its trade count, differs from A's.
- Order inside each hourly step t: (1) the bar that closed at t updates
  stops, targets and the session close; (1b) the OPEN of the bar starting
  at t - the price new entries fill at - processes gaps through a stop and
  pending session closes (only the open is read); (2) equity is marked;
  (3) decisions and entries. Before the audit, step 1b did not exist: a gap
  exit at t was processed at t + 1h, so B's open risk peaked at 150.21 USD
  counting a position that had already exited.
- Fills are on the instrument's tick (5 decimals, 3 for JPY) and P&L comes
  only from the fill prices. Spread and slippage live inside those prices,
  and the reported costs are informative and never deducted again. Before
  the audit, exits at a bar's open or close kept the provider's unrounded
  price in the P&L (up to 0.15 USD per trade).

## Tracker

- `/oportunidades` adds the ranked candidates to
  `data/ledger/opportunity_tracker.jsonl` (OBSERVED event, frozen levels,
  costs and strategy version).
- `/seguimiento` or `scripts/update_tracker.py` evaluates the pending
  ones. Entry is the open of the first bar opening at or after the scan
  moment, within 2 hours and while entries are allowed; otherwise EXPIRADA.
  A STATUS event is appended only when the status changes; nothing is
  rewritten.

## Portfolio Guardian

- Positions come from `scripts/portfolio.py agregar` (manual) plus the
  simulated journal's open trades. Closing only stops the monitoring.
- P&L follows the position: a falling asset is a gain for a SHORT, and the
  asset's own move is shown separately.
- Gold: Yahoo publishes no XAU/USD spot (`XAUUSD=X` does not exist).
  `GC=F` is the COMEX future (another instrument, with basis) and is used
  only with `--usar-futuros-oro` / `GuardianSettings(allow_gold_futures_proxy=True)`,
  labelled as a proxy. Otherwise gold valuations show NO DISPONIBLE.
- Non-USD prices need a USD rate (`USDxxx=X`). Without one the valuation is
  NO DISPONIBLE, never guessed.
- Alerts: they fire on activation, repeat at most once per cooldown (4 h)
  while still true, and resolve only after the value recovers 20 % beyond
  the threshold (hysteresis). State lives in
  `data/ledger/portfolio_alert_state.json`, with an append-only log in
  `portfolio_alerts.jsonl`.

## Scheduling (not enabled)

Nothing runs on a schedule, and no command sends Telegram messages by
itself. To check alerts periodically later, a person can register
`scripts\portfolio.py alertas` (and `scripts\update_tracker.py`) with
Windows Task Scheduler (`schtasks /Create /SC HOURLY ...`). Pushing alerts
to Telegram would need an explicit sender and the user's authorization;
it is not implemented.

## Open limitations

- The strategy is an experimental baseline with no favourable evidence.
  Its parameters were designed for EUR/USD and are applied unchanged to
  GBP/USD and USD/JPY.
- One month is a small sample: the figures are not statistically
  meaningful.
- Correlation is handled only through the shared-USD cap, not with
  correlation estimates.
- The tracker's `/seguimiento` fetches data when called. A long gap
  between calls is fine: evaluation uses only bars after detection.

## Multi-period evaluation (`scripts/replay_period.py`)

- Data: downloaded once per instrument into
  `data/raw/snapshots/fx_1h_<start>_<end>/`, one CSV per instrument plus a
  `manifest.json` (source, requested period, download time, first and last
  bar, bar count, SHA-256 per file and combined). Later runs reuse the
  snapshot (`--refrescar` downloads again), and a hash mismatch stops the
  run. Missing hours of the open FX week are listed as gaps, never filled.
  The run refuses to start without the strategy's warm-up bars before the
  first decision.
- Months are whole FX sessions (17:00 to 17:00 New York, daylight saving
  applied). No position crosses a month boundary, since everything is flat
  by 16:00 New York.
- Two modes, never mixed: CONTINUOUS (one replay, the capital carries
  over) and NORMALIZED (each month alone from 10 000 USD). The first month
  is identical in both.
- Cost scenarios: spread and slippage multiplied by 1, 1.5 and 2. All of
  them are reported, and none is picked.
- Outputs in `data/reports/period_<from>_<to>/`: `summary.json`,
  `monthly_continuous.csv`, `monthly_normalized.csv`, `trades.csv`,
  `equity_curve.csv`, `rejections.csv`.
- Integrity: these months have all been seen, so the evaluation is a
  retrospective backtest and validates nothing. A later walk-forward must
  fix chronologically separate windows before looking at them:
  development (months already seen, e.g. April to September 2026),
  validation (later months, chosen in advance), and a final test
  (prospective tracking from a pre-set date), with no re-optimisation in
  between.
