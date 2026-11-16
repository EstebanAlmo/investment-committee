# Data layer for bid/ask quotes (Entrega 1 of `ema-rsi-atr-forex`)

Status on 2026-11-11: the provider-independent infrastructure exists and is
tested with synthetic data. **No real bid/ask data has been obtained**: no
provider has been integrated, no account opened, nothing downloaded. The
strategy, its indicators and the backtest are not implemented yet.

## Three kinds of numbers, never mixed

| Kind (`QuoteKind`) | Meaning | Today |
|---|---|---|
| `OBSERVADA` | a quote a provider actually published | none on disk |
| `ESTIMADA` | derived under a stated assumption (e.g. Yahoo mid price ± an assumed spread); never an executable price | only via `estimated_from_mid`, which writes the assumption into the quote's source |
| `SINTETICA` | made up for tests | `tests/market_intelligence/_support.py` only |

Every quote, bar and dataset manifest carries its kind. `audit_quotes`,
`aggregate` and `write_dataset` refuse a series that mixes kinds, and a
dataset whose declared kind differs from its rows.

## Components (`src/investment_committee/market_intelligence/`)

| Module | What it does |
|---|---|
| `quotes.py` | `Quote` (symbol, UTC timestamp, bid, ask, kind, source), `to_utc` (naive datetimes are refused, never guessed), `estimated_from_mid` |
| `quality.py` | `audit_quotes`: invalid prices, bid > ask, duplicates (exact: one kept; contradictory: all rejected), wide spreads (flagged, kept), out-of-order rows; spread statistics. `audit_bars`: the same for bars after UTC normalization, plus off-grid labels, bars outside the market calendar, flat bars, coverage. `missing_bars`, `quote_silences`: gaps against a calendar function supplied by the caller; a closed market is never a gap |
| `aggregation.py` | `aggregate`: quotes to bid / ask / mid bars labelled by open time, UTC; an interval without quotes produces no bar. `to_price_bars`: one side as the project's `PriceBar` |
| `comparison.py` | `compare_bars`: bars present in one source and not the other, and open/high/low/close differences in pips. It measures; it does not declare a source right |
| `provenance.py` | `write_dataset` / `read_dataset`: byte-for-byte raw copies, processed CSVs, transformation parameters, quality figures and SHA-256 of every file; written once, verified on every read |

Nothing existing was modified: `YFinanceProvider`, `snapshots.py`, the
frozen strategies, the replay and both monitors are untouched. Datasets go
under `data/raw/quote_datasets/` (git-ignored).

Not built yet, on purpose: a provider adapter (no source is approved) and a
holiday calendar (pending verifiable evidence).

## Provider research (2026-11-11; nothing was signed up for or downloaded)

| Provider | Finding | Evidence |
|---|---|---|
| Dukascopy | **Paused by the user.** Terms of Use §3 forbid scrapers/robots/data mining without prior express written consent and forbid building a database from the site; downloads are for personal, non-commercial use | <https://www.dukascopy.com/swiss/english/legal-pages/terms-of-use/> |
| Tiingo | **Not usable on the free plan.** Terms of Use §1.6 (updated 2026-10-06): on a Starter or trial plan Tiingo Data and derived products may not be written to any persistent storage. Paid plans may store data while the subscription is active. Starter: 50 requests/hour, 1 000/day, internal use only; Power is 30 USD/month. The API key is data-only (it cannot place orders). FX endpoints return top-of-book `bidPrice`/`askPrice`; the documentation does not say whether historical OHLC is bid, ask or mid | <https://app.tiingo.com/tos/> (read through search excerpts; the page refused direct fetching), <https://www.tiingo.com/about/pricing>, <https://www.tiingo.com/documentation/forex> |
| TrueFX (Integral) | **Candidate, pending the user's own review.** Free; registration requires clicking "I Agree" to its Terms & Conditions. Licence: non-exclusive, revocable, for "Your own internal purpose of viewing and analyzing FX market data"; historical downloads are for internal use; no redistribution of content; no derivative works "from the System"; front-end applications over the Integral API are allowed for internal purposes; prices are "indicative, not executable". The terms name no prohibition on automation or local storage, and no explicit permission either. Pairs, date range, file format and time zone of the downloads are not published outside the login | <https://www.truefx.com/truefx-terms-and-conditions/>, <https://www.truefx.com/truefx-market-data-faq/>, <https://www.truefx.com/truefx-historical-downloads/> |
| HistData | Free manual downloads (M1, ticks); the download page publishes no terms, no time zone, and does not confirm bid and ask in the ASCII ticks; automation is a paid service | <https://www.histdata.com/download-free-forex-data/> |
| FXCM public candles | m1/H1, UTC, "for personal use", official scripts; only 2017-2020 | <https://github.com/fxcm/MarketData> |
| OANDA | **Not authorized.** Its token grants access to the whole account; no read-only scope was found | <https://developer.oanda.com/rest-live-v20/authentication> |

## Diagnosis of the existing Yahoo snapshot

`scripts/audit_yahoo_snapshot.py` on `fx_1h_20260331_20260930` (hash
`ae35f14e...c879`, verified; read-only, nothing downloaded). Calendar: FX
week Sunday 17:00 to Friday 17:00 New York. EUR/USD and GBP/USD give the
same figures:

- 3 272 bars received, labels in two UTC offsets (`+00:00` and `+01:00`,
  London time); after normalization there are no duplicates and every label
  is on the hourly grid.
- Coverage of the open-market grid: 3 245 of 3 309 bars (98.07 %).
- The difference is mostly a **calendar mismatch, not lost data**: in summer
  Yahoo's week runs Sunday 23:00 UTC to Friday 22:00 UTC, while the New York
  convention is Sunday 21:00 to Friday 21:00 UTC. Hence 26 Sundays with the
  first 2 hours missing, and 27 bars on Fridays at 21:00 UTC outside the
  calendar.
- Real holes: 2026-04-20 07:00 (2 bars), 2026-07-17 06:00 (3 bars) and
  Sunday 2026-05-03 (7 bars instead of the usual 2) - 10 bars.
- Every price has more than 5 decimals (binary float artefacts); volume is
  always 0; there is no bid or ask.
- Open versus the previous close: EUR/USD is identical in 48 % of bars,
  GBP/USD in 6 %, median difference about 1 pip - the series is not a
  continuous stream of one side of the market. Yahoo's provider already
  widens high/low to contain open/close for the same reason.

Consequence: Yahoo H1 can serve as a secondary reference for comparison. It
cannot support entry, stop and target prices as observed bid/ask values;
anything computed from it is `ESTIMADA`.

## Limitations

- No real bid/ask data yet, so spreads, the SL/TP order inside a bar and
  overnight financing cannot be measured, only assumed.
- The comparison against Yahoo has only been exercised on synthetic bars.
- The market calendar is the weekly one; holidays are not modelled.
- `PriceBar` has no `kind`; the kind travels in `BidAskBar` and in the
  dataset manifest.

## Proposal to extend history (needs approval; nothing is downloaded yet)

1. One month, EUR/USD and GBP/USD, from the approved source, placed by the
   user in a local folder. Import, audit, aggregate, compare with Yahoo for
   the overlapping month, write the dataset with hashes.
2. Measure on that month: file size, quotes per month, import time, spread
   by hour. Only then estimate the full history; tick files for a major
   pair are typically tens of megabytes compressed per month, but that
   figure is unverified for any candidate here.
3. If the month is sound, extend backwards in blocks (3 months, then the
   rest of the development period), each block a separate dataset with its
   own manifest. Raw files stay out of Git.

## Commands

```
Windows:  .venv\Scripts\python.exe -m pytest
          .venv\Scripts\python.exe scripts\audit_yahoo_snapshot.py
macOS:    .venv/bin/python -m pytest
          .venv/bin/python scripts/audit_yahoo_snapshot.py
```
