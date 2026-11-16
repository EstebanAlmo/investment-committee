# Historical data: sources and the ingest pipeline (B5.1)

What was looked for: hourly EUR/USD and GBP/USD history older than the
months already inspected, ideally with real bid and ask, free, and
downloadable without breaking anybody's terms.

**What was found: no source with real bid/ask could be downloaded under
verified terms. What was downloaded is more history of the same kind as
before** - Yahoo's single indicative price - so the dataset is larger, not
better. Real bid/ask needs a decision of the user (below).

Nothing here computes a signal, a trade or a return.

## Sources (researched 2026-11-12)

| Source | History and resolution | Price | Time zone | Access | Terms | Verdict |
|---|---|---|---|---|---|---|
| Yahoo Finance (yfinance) | 1h bars for the last 730 days only (verified: the request of 2026-11-12 was answered from its first day, 2024-11-14) | one indicative mid price; no bid/ask | Europe/London labels with their UTC offset, bar open | no account; already this project's provider | no data licence published for this route; personal local research, no redistribution; not verified clause by clause | **used** |
| FXCM public candles | m1, H1, D1; the README lists 2017-2020; by HTTP headers only (no file was downloaded) H1 files answer for 2012-2023 and not for 2025 | README does not state the columns; believed bid and ask OHLC, **unverified** | UTC ("Timestamps are in UTC") | no account; weekly `.csv.gz` files at a documented URL; official Python scripts | "for personal use and abides by our EULA"; the EULA page is a software licence behind an e-signature form whose text could not be read | **not downloaded: terms unverified** |
| HistData | M1 bars and ticks, many years | M1 from the tick **bid** only; ask only in "Generic ASCII" ticks | EST without daylight saving | manual web download; automation is sold (FTP/SFTP) | none published; "use the data at your own will and risk" | **not downloaded**: no API, and automating the free page would go around the paid route |
| Dukascopy | ticks, many years, bid and ask | real bid/ask | UTC | site and JForex | Terms of Use §3: no scraper, robot or data mining without prior express written consent; no database built from the site | **forbidden without written consent** (paused by the user since 2026-11-11) |
| TrueFX | ticks, bid and ask | real (indicative) bid/ask | not published outside the login | free account, "I Agree" to its terms | internal use, no redistribution | **needs an account**: the user's decision |
| OANDA | candles and ticks, bid/ask | real bid/ask | UTC | API token of a trading account | the token reaches the whole account | **not authorized** |
| Tiingo | intraday FX | top of book | UTC | API key | free plan forbids persistent storage | **not usable free** |

Evidence: <https://github.com/fxcm/MarketData>, <https://www.fxcm.com/uk/forms/eula/>,
<https://www.histdata.com/f-a-q/>, <https://www.histdata.com/f-a-q/data-files-detailed-specification/>,
<https://www.dukascopy.com/swiss/english/legal-pages/terms-of-use/>,
<https://github.com/ranaroussi/yfinance/issues/1916> (the 730-day message),
and `docs/data-layer.md` for TrueFX, OANDA and Tiingo.

### FXCM: what can be verified, and why nothing was downloaded (re-checked for B5.2)

- FXCM's own repository states, for its candle data: "This is for
  personal use and abides by our EULA", and for licensing questions gives
  the address api@fxcm.com. It publishes the URL pattern and Python scripts
  to download the weekly files, and says timestamps are UTC and that "data
  points are indicative and based on the lowest spreads available
  exclusively on Active Trader accounts".
- The EULA that sentence points to is titled "FXCM Software End User
  License Agreement". Its page shows no clause: only a button to an
  electronic-signature form. Its text could not be read without opening
  that form, and no public copy of the clauses was found.
- So two things are verifiable - the data are offered for personal use,
  and that use is subject to an agreement - and one is not: what the
  agreement allows (local storage, research, any restriction on automated
  download). An agreement presented as a form to sign is a condition that
  requires acceptance.
- **Conclusion: not downloaded.** Using these files needs the user to read
  and accept that agreement personally, or a written answer from
  api@fxcm.com that local research use of the public candle files is
  allowed. Either would be recorded in the dataset's provenance.
- By HTTP headers only (no file content was fetched), H1 files exist for
  2012 through 2023 and not for 2025.

### Why Yahoo, and what it does not give

It is the only source that is free, needs no account, has no term that was
found to forbid this use, and is already the project's provider. It gives
no bid, no ask, no spread: execution stays ESTIMATED exactly as in B4.

### The decision left to the user: real bid/ask

In order of fit for this project:

1. **FXCM candles** - H1 with (probably) bid and ask, UTC, about 4 MB for
   both pairs and twelve years, official download scripts. Blocked only by
   the unread EULA: the user would have to open the e-signature form, read
   it and decide. It stops in 2023 or 2024, so it would not overlap the
   Yahoo data of 2025-2026.
2. **HistData ticks** - bid and ask, but only by hand: the user would
   download the monthly files in a browser and the pipeline would read them
   (EST without daylight saving, converted to UTC).
3. **Dukascopy** - the best data; needs their written consent.
4. **TrueFX** - needs the user to register and accept its terms.

## The dataset downloaded

`data/raw/ingest/yahoo_fx_1h_20241114_20260323/` (git-ignored), by
`scripts/download_fx_history.py`: two requests, one per pair, on
2026-11-12T21:26:40Z.

| | EUR/USD | GBP/USD |
|---|---|---|
| Period (UTC) | 2024-11-14 00:00 to 2026-03-20 20:00 | same |
| Requested | from 2024-11-14 00:00 to 2026-03-23 00:00 exclusive | same |
| Rows in the original | 8 281 | 8 283 |
| Bars kept | 8 211 | 8 213 |
| Refused: outside Yahoo's calendar | 70 | 70 |
| Kept and flagged: flat bars (open = high = low = close) | 9 | 6 |
| Unreadable rows, rows without prices, invalid prices, contradictory duplicates, widened ranges, incomplete bars | 0 | 0 |
| Expected hours | 8 306 | 8 306 |
| Coverage | 98.86 % | 98.88 % |
| Expected hours without a bar | 95 in 17 gaps | 93 in 17 gaps |
| Original: bytes, SHA-256 | 919 537, `5239c73e...ba30a57` | 919 176, `c6f2942c...0ab65de` |
| Normalized: bytes, SHA-256 | 846 040, `772b6f21...17a742` | 845 686, `cb5ef02d...170e525` |

Dataset hash (normalized): `a8f7111623dbd578d072ea1a04b4a218c4ac7d75fcbd83abeb06fe0404140b91`.
The full hashes are in `procedencia.json`.

Gaps and anomalies:

- The 70 refused bars are the one bar a week that Yahoo labels at Friday
  17:00 New York, after the week has closed.
- Most missing hours are holidays, which the calendar does not model:
  25 December 2024 and 2025 (12-13 hours), 1 January 2025 and 2026 (up to
  19 hours).
- **2 February 2026: 19 hours missing in both pairs** on an ordinary
  Monday, and 6 more on 30 January 2026. These are outages of the source,
  not market closures.
- Labels arrive with two offsets, `+00:00` and `+01:00` (London's summer
  time): converted to UTC from the offset each one carries.
- No original row had to be repaired.

Period rules: the dataset ends where the snapshot already inspected begins
(2026-03-23 00:00 UTC). Nothing at or after that instant was requested, so
April-September 2026 and everything later are untouched by this download.

## The pipeline (`market_intelligence/ingest.py`)

```
original/      the provider's answer, byte for byte; written once
normalizado/   UTC, one bar per hour, snapshot format (read_snapshot)
procedencia.json   provider, URL, terms, when, period, resolution, zone,
                   kind of price, SHA-256 and size of every file, dataset hash
calidad.json   rows refused, duplicates, calendar, gaps
```

| Requirement | How |
|---|---|
| Idempotent download | the script downloads only if the dataset folder does not exist; otherwise it verifies it and reports |
| Immutable originals | `store_original`: the same bytes again are a no-op, other bytes under the same name are an error |
| Integrity | `verify_ingest`: every original and normalized file against its hash, and the normalized bars against a fresh normalization of the originals |
| UTC, daylight saving | each label is converted from the offset it carries; one instant written with two offsets is one bar |
| Duplicates | exact ones collapse (`DUPLICADO`); contradictory ones are all refused (`DUPLICADO_CONTRADICTORIO`) |
| Gaps | expected hours without a bar are listed; nothing is filled or interpolated |
| OHLC | unreadable rows, rows without prices and non-positive prices are refused and counted; an open or close outside the range widens it with the bar's own prices and is counted (`OHLC_AMPLIADO`) |
| Incomplete bars | a bar not yet closed when the data was fetched is dropped (`VELA_INCOMPLETA`); so is anything at or after the end of the period |
| Bid/ask | `audit_bid_ask`: bid <= ask, positive, each side a consistent OHLC, no zero spread - for when a bid/ask source is approved |
| Ticks to H1 | `aggregation.aggregate`: the bar labelled L holds the quotes of [L, L + 1h); open is the first quote, close the last; an hour without quotes gives no bar |
| Retries | `with_retries`: at most three attempts with pauses, only for download failures |
| Reproducibility | the same originals give the same normalized bytes and the same dataset hash, on Windows and macOS |

Adding a bid/ask source later means one reader for its file format; the
storage, verification and reports stay as they are.

## Commands

Windows and macOS, from the repository root:

```
python scripts/download_fx_history.py     # downloads once; afterwards only verifies and reports
python -m pytest tests/market_intelligence/test_ingest.py
```
