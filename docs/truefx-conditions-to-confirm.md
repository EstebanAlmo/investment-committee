# TrueFX: conditions to confirm before using it

Status on 2026-11-11: **nothing integrated, nothing downloaded, no account
created.** TrueFX (Integral Development Corp.) is the first candidate for
historical and live bid/ask quotes. This is the list of what must be
confirmed - by the account holder, reading the terms shown at registration,
and where they are silent, in writing from TrueFX - before any code touches
it. It is not legal advice.

## What the public pages say (read 2026-11-11)

Sources: <https://www.truefx.com/truefx-terms-and-conditions/>,
<https://www.truefx.com/truefx-market-data-faq/>,
<https://www.truefx.com/truefx-historical-downloads/>.

- Free; access requires registering and clicking "I Agree" (an agreement
  with Integral, governed by California law, disputes by arbitration in San
  Francisco; the user indemnifies Integral for claims arising from use).
- Licence: "non-exclusive, revocable, non-transferable", "for Your own
  internal purpose of viewing and analyzing FX market data". The "System"
  includes real-time streaming data and historical data downloads.
- Not allowed: to "transmit, distribute, publish or otherwise disseminate"
  the System or its content, including prices; to "make derivative works
  from the System".
- Allowed: front-end applications that connect to the real-time streaming
  data through the Integral API, for internal purposes.
- "The market data is indicative, not executable." Listed uses include
  "historic back-testing".
- Fees may be introduced with 45 days' notice; Integral may stop any part
  of the service at any time.
- Not stated publicly: pairs, date range, file format and time zone of the
  downloads; any rule on automated downloading, request rates, or keeping
  local copies.

## Must be confirmed (blocking)

| # | Question | Why the project needs it | Where to look |
|---|---|---|---|
| 1 | May downloaded historical files be **kept on local disk indefinitely**, including after the account or the agreement ends? | Reproducible backtests need the raw files and their hashes for as long as results are cited | Terms at registration (licence, termination clauses); else ask |
| 2 | May **processed copies** be stored locally: normalized quotes, H1 bars, a SQLite database? Is that a "derivative work from the System"? | The data layer writes exactly these (`provenance.write_dataset`) | Definition of "System" and of derivative works; else ask |
| 3 | May live quotes be **recorded** (bid, ask, time) each time a signal appears, and kept? | The December log stores the quote behind every hypothetical entry | Streaming/API clauses; else ask |
| 4 | Is **programmatic access** allowed: scripted download of the monthly files, and polling or streaming quotes from our own program? Any rate limit? | A monitor polls hourly; history is 2 pairs x ~24 months | Terms; API documentation after login; else ask |
| 5 | Is the account type **personal / individual**, and is personal research an "internal purpose"? | The agreement speaks of "You, and the entity you represent" and "internal business purposes" | Registration form and definitions |
| 6 | Do **EUR/USD and GBP/USD** exist with both bid and ask, and for which months? Is the current month available, and with what delay? | Without recent months there is no overlap with the December window | Download page after login |
| 7 | **Time zone and format** of the files (timestamp resolution, columns, decimals) | Needed to write the importer and to normalize to UTC without guessing | File documentation; a sample file |
| 8 | Does the **live feed** require anything that can place orders (a trading account, a FIX session with trading rights)? | Only read-only access is authorized; TrueFX states it is not a broker, which must hold for the API too | API documentation after login |

## Should be confirmed (not blocking)

- Whether **results derived** from the data (trade lists, statistics,
  charts) may be shown to other people. Redistribution of prices is
  forbidden; the status of derived figures is not stated. Until confirmed:
  reports stay private and Git contains no TrueFX price.
- What the data is: "top-of-book" quotes from Integral's network,
  **indicative, not executable**. Its spreads may be tighter than a retail
  broker's; a backtest must not treat them as fills.
- Service continuity: free today, revocable, may change with notice. The
  December protocol needs a rule for a provider outage.

## If a condition fails

- 1 or 2 fails: TrueFX cannot support reproducible backtests. Alternatives
  already examined are in `docs/data-layer.md` (none free and unrestricted).
- 3 or 4 fails: TrueFX can still serve history (manual download) while the
  live quote stays an estimate from Yahoo, labelled `ESTIMADA`.
- 8 fails: not usable for live quotes.

## What happens after confirmation (not before)

1. The account holder downloads one month of EUR/USD and GBP/USD by hand
   into a local folder outside Git.
2. An importer for that exact format is written and tested against a small
   hand-made sample in the same format (synthetic values).
3. The month is audited, aggregated to H1, compared with Yahoo, and stored
   as a dataset with hashes; the quality report is presented.
4. Only then is extending the history, or a live adapter, proposed.
