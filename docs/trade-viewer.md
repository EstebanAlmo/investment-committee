# Trade viewer (B4.4)

A local dashboard to look at every simulated trade of a backtest study on
its own candles, with the indicators the strategy used, the levels the
engine set and what happened - and to write down a reviewer's opinion and
share it.

**It shows a simulation.** Candles are Yahoo's single indicative price;
entries, stops, targets and exits are the ESTIMATED executions of the
engine (`docs/ema-forex-backtest.md`). Nothing shown was executable, no
result is a real net return, and the months of the existing study had
already been inspected. The viewer exists to find problems in a strategy,
not to make results look good.

It is a reading layer: it never runs a backtest, never changes a study,
never talks to a broker or the network, and is served on this computer
only.

## Install and open

Windows (PowerShell) and macOS (Terminal), from the repository root, with
the project's virtual environment active:

```
pip install -e ".[viewer]"
python -m streamlit run scripts/trade_viewer.py
```

Then open `http://127.0.0.1:8501` in a browser (the address is printed).
Stop it with Ctrl+C. `.streamlit/config.toml` keeps the server on
`127.0.0.1` and disables Streamlit's usage statistics; run the command
from the repository root so that file is read.

The optional dependencies are Plotly (charts), Streamlit (dashboard) and
Kaleido (PNG). The engine, the backtest and the whole test suite work
without them. PNG export also needs Google Chrome or Chromium already
installed; none is installed from here, and without one the HTML export
still works.

## What it reads and what it writes

| Folder | Use |
|---|---|
| `data/reports/backtests/<study>/` | read only: `manifest.json`, `run.json`, `record.json`, `signals.csv` |
| `data/raw/snapshots/<snapshot>/` | read only: the candles; found by the name the manifest gives |
| `data/reviews/<study>/notes.jsonl` | written: review notes (git-ignored) |
| `data/reports/exports/<study>/` | written: shared reports (git-ignored) |

Before anything is drawn the study must check out
(`interfaces/trade_viewer/study_store.py`):

- `manifest.json` against its own study hash;
- the snapshot against the manifest's dataset hash;
- each `run.json` against the results hash the manifest recorded for it;
- `signals.csv`, which has no hash in the manifest (a gap of B4.3), against
  the verified run: same instrument, instant, outcome and ATR for every
  decision. A test also recomputes a sample of the recorded indicators with
  the strategy's own function.

If any check fails the dashboard shows the error and draws nothing.

## Using it

1. **Choose the run** in the sidebar: study, strategy, period (`COMPLETO`
   or `COMUN`), cost scenario, pair. The run is named at the top of every
   screen; scenarios are never mixed.
2. **Time zone**: Bogotá by default; New York and UTC are available. It
   only changes how instants are written. Stored timestamps stay in UTC.
3. **Resumen**: capital, result, win rate with its sample size and
   interval, expectancy, profit factor, drawdown, capital curve, monthly
   results, R distribution, exits, rejection reasons, and all strategies of
   the study under the same period and scenario.
4. **Operaciones**: the table of trades; filter by direction, result, exit
   reason and dates; click a row to open it; download the filtered CSV.
5. **Operación**: the chart, the technical sheet and the review form of the
   selected trade; "Anterior" / "Siguiente" walk the list.
6. **Comparación A/B**: pick a pair, a day and a number of hours to see
   what every strategy did there, under the same period and scenario.
7. **Revisión y exportación**: the reviewed trades and the shareable
   report.

## Reading the chart

| Element | Meaning | Kind |
|---|---|---|
| Candles | Yahoo's mid price, one per hour, labelled by its opening time | observed |
| EMA, RSI14, ATR14 lines | the values the strategy recorded for that candle; a break means the strategy did not evaluate that hour (warm-up, incomplete data). Nothing is interpolated or recomputed | derived |
| Yellow diamond | the candle that produced the signal | observed |
| Triangle | the entry, on the bar whose **open** is the entry: the first price after the signal candle closed | estimated |
| Solid dark line | entry price (ask + slippage for a LONG, bid - slippage for a SHORT) | estimated |
| Dashed red / green | stop loss and take profit, with pips and R | estimated |
| Thin dotted red / green | the same levels moved onto the candles' price: where a candle must reach. The engine compares levels with the estimated bid (LONG) or ask (SHORT), half a spread away from the mid price | derived |
| Red / green shading | risk and reward zones, from the entry bar to the end of the trade | - |
| X | the exit. A shaded bar means "inside this hour, instant unknown"; without shading the exit happened at that bar's open (a gap, the 72-bar limit, the weekly close) | estimated |
| Amber band, two open X | an INDETERMINATE trade: stop and target in one bar, or an hour without data. Both hypothetical exits are shown; which one happened is not known | - |
| Dashed amber line | expected hours with no candle | observed |
| Dotted grey line | market closed (weekend) | observed |
| Purple circles (optional) | signals the engine rejected, with its own reasons (`POSICION_ABIERTA`, `EXPOSICION_USD`, `LIMITE_PERDIDA_DIARIA`, ...) | - |

The EMAs can differ slightly from a charting platform's: the strategy
computes them on a fixed window (300 hours for A, 720 for B) seeded with a
simple average.

**Size and risk**, in the sheet: units of base currency; standard lots
(units / 100 000); notional and margin in USD; the risk budget (0.5 % of the
capital before the trade); the estimated loss if the stop fills normally
(at most the budget, after rounding the size down) and the estimated gain
at the target. A stop does not cap the loss: a gap fills beyond it, and the
sheet shows the excess.

Each sheet value is marked OBSERVADO (Yahoo's price and clock), ESTIMADO
(the execution model and its money) or DERIVADO (arithmetic on those).
Prices are shown with at most six decimals; the study's files keep every
digit.

## Review notes

In **Operación**: a rating (Buena entrada, Mala entrada, Discutible,
Pendiente), a comment, the problem identified, a suggested improvement and
the reviewer. Saving appends a line to `notes.jsonl`; nothing is rewritten,
so earlier versions remain as history.

A note belongs to study hash + trade id + scenario + period: the same
signal under another scenario or period is another trade and has its own
notes.

Notes are qualitative. Nothing in the engine reads them; a suggested
improvement is a hypothesis for a new strategy version, to be tested on
data not yet seen - reviewing losing trades one by one invites rules that
only fit what was already looked at.

## Sharing

| Export | Where | Content |
|---|---|---|
| HTML of one trade | Operación | interactive chart, identity, sheet and current note; Plotly is inside the file, so it opens without internet or the repository |
| PNG of one trade | Operación | the chart, 3200 x 2000 pixels |
| CSV | Operaciones | the filtered table |
| Report | Revisión y exportación | one HTML: the run's figures, the limitations and every chosen trade with chart, sheet and note; also saved under `data/reports/exports/<study>/` |

Every export names strategy, pair, timeframe, date, scenario, period, trade
ID, data kind (ESTIMADA), the study hash and the simulation warning. None
contains a path, a user name or anything else of this computer.

## Offline report for an outside reviewer (B5.3)

One HTML file that a trader can open with a double click, with no Python,
no Streamlit, no server and no internet, plus one CSV per strategy:

```
python scripts/export_trades_report.py
```

The same command on Windows and macOS. It writes, under
`data/reports/exports/<study>/reporte_offline/` (git-ignored):

| File | Content |
|---|---|
| `investment_committee_trades.html` | the whole report: A and B, common period, base scenario |
| `strategy_A_trades.csv`, `strategy_B_trades.csv` | the valid closed trades of each strategy (60 and 111 in the B4.3 study) |
| `excluded_trades.csv` | the trades left out of the count, when there are any (one of B, open at the end) |

What the page offers: the engine's summary of each strategy; a strategy
selector; a table with search, filters (pair, direction, result, kind of
exit) and sorting; the figures of the filtered selection (wins, losses,
expectancy, drawdown, cumulative result); a candle chart per trade with
the recorded EMAs, RSI14 and ATR14, the signal, entry and exit, the stop
and target lines, zoom, pan and a tooltip; the trade's sheet; CSV export
of the selection; the rules of each strategy; three time zones.

**"Sin conocer el futuro".** In this mode a trade opens with the candles
known at the entry and nothing else. The reader advances one candle at a
time (or five, or plays it), and the exit, result, duration, colours and
figures stay hidden - in the chart, the sheet, the table and the summary
of the selection - until "Revelar resultado" is pressed for that trade.
The window that can be replayed has a fixed length (92 candles after the
entry), so that its end says nothing about how long the trade lasted.
Nothing is recomputed: the page only decides what to draw.

How it is built, and why it can be trusted as far as the study can:

- `interfaces/trade_viewer/offline_report.py` and `offline_page.py`,
  standard library only. The page draws on a canvas with its own
  JavaScript: no Plotly, no CDN, no file next to it. Its size is about
  1.4 MB, nearly all of it the candles of the snapshot (kept with every
  digit) and the recorded indicators (six decimals; RSI two), stored once
  per pair and shared by every trade.
- The study is loaded through `study_store` (manifest, dataset and run
  hashes verified; `signals.csv` checked against the run). The report is
  refused if its trades do not add up to the engine's own summary - count,
  wins, losses and net result - and the page repeats that check when it
  opens.
- A trade counts only if it is a valid closed trade (closed, with a
  result, not indeterminate). The others are kept, shown apart on request
  and never counted.
- The page states that prices are Yahoo's single indicative price and
  that every execution is ESTIMATED, and carries the study, dataset and
  run hashes. It names no path, user or machine.
- The summary of each strategy is the engine's. Only the figures of a
  filtered selection are added in the browser, in whole cents.
- Tests: `tests/interfaces/trade_viewer/test_offline_report.py`. Two of
  them open the page from `file://` in a headless Chrome or Edge with name
  resolution cut off and run the page's own check of filters, sorting,
  export and replay (`investment_committee_trades.html#selftest`); they
  skip themselves where no such browser is installed.

The same script writes the report of another verified study. For the
registered D2 test of strategy C (`docs/research/ema-pullback-forex-0.1.0-results.md`):

```
python scripts/export_trades_report.py --study data/reports/backtests/d2_ema-pullback-forex-0.1.0_ad7ef187_41706d5 --snapshot data/raw/ingest/yahoo_fx_1h_20241114_20260323/normalizado --period COMPLETO --scenario COSTOS_X1_5 --html-name strategy_C_trades_costos_x1_5.html --out data/reports/exports/d2_ema-pullback-forex-0.1.0_ad7ef187_41706d5/reporte_offline_costos_x1_5
```

A strategy shown alone keeps its letter and brings its own rules and
limitations; C has no RSI, so its chart has the price and ATR panels only.
The A/B report is not affected.

Yahoo publishes no data licence for these prices (`docs/data-ingest.md`):
the file is for a private review between two people, not for publication.

## The folder to hand over (B5.5)

```
python scripts/build_report_distribution.py
```

writes `data/reports/Reportes_Trading/` (git-ignored), to be shared whole:

| File | Content |
|---|---|
| `Indice_Reportes.html` | the index: what each strategy is, its main figures, the warnings, a link to each report, and each file's size, hash and provenance |
| `Estrategia_A_Cruce_EMA_2026.html` | A, common period, base scenario: 60 valid closed trades |
| `Estrategia_B_Cruce_Rapido_2026.html` | B, common period, base scenario: 111 |
| `Estrategia_C_Retrocesos_2025-2026_Base.html` | C on D2, base costs: 90 |
| `Estrategia_C_Retrocesos_2025-2026_Costos_Altos.html` | C on D2, costs x1.5: 88 |

Each report holds one strategy, opens offline, links back to the index by
its relative name, and shows in its heading the strategy, the period, the
scenario and the number of trades. The studies of B4.3 and B5.4 and the
reports exported in B5.3 and B5.4 are neither moved nor rewritten: the
script builds the reports again from the verified studies, compares each
one trade by trade with the earlier export (`offline_index.same_trades`),
and hashes every original before and after.

**Net result and account return are two figures.** The net result adds
the valid closed trades only. The account's return is its final balance
against the initial capital: it also books the indeterminate trades, at
their conservative result, and leaves a position still open out. For C:
10 000 - 193.18 - 96.36 (two indeterminate trades) = 9 710.46 USD, a
return of -2.90 %; at costs x1.5, 10 000 - 529.34 - 96.53 = 9 374.13 USD,
-6.26 %. Each report and the index now spell that sum out.

Module `interfaces/trade_viewer/offline_index.py`; tests
`tests/interfaces/trade_viewer/test_offline_index.py`.

## Modules

| File | Content | Needs |
|---|---|---|
| `interfaces/trade_viewer/study_store.py` | verified, read-only loading | standard library |
| `interfaces/trade_viewer/trade_view.py` | one trade as data: candles, recorded indicators, levels, sheet; A/B interval | standard library |
| `interfaces/trade_viewer/notes.py` | review notes | standard library |
| `interfaces/trade_viewer/charts.py` | figures | Plotly |
| `interfaces/trade_viewer/export.py` | HTML, PNG, CSV, report | Plotly, Kaleido |
| `interfaces/trade_viewer/app.py` | dashboard | Streamlit |
| `interfaces/trade_viewer/offline_report.py`, `offline_page.py` | the offline report and its page | standard library |
| `scripts/export_trades_report.py` | writes the offline report | standard library |
| `scripts/trade_viewer.py` | start-up | Streamlit |

`simulation/` imports none of this. Tests: `tests/interfaces/trade_viewer/`
(those that need Plotly or Streamlit skip themselves when the viewer is not
installed; `test_real_study.py` skips itself where the study is absent).

## Limitations

- Everything listed in `docs/ema-forex-backtest.md` applies: one indicative
  price per hour, constant assumed spread, unknown order of events inside
  an hour, unverified costs.
- About 1 pip of noise in Yahoo's prices can decide whether a level a few
  pips away was touched; the chart cannot show more than the hour's range.
- The viewer shows the six scenarios that were run; it cannot produce a new
  one.
- `signals.csv` is checked for consistency with the verified run, not by a
  hash of its own.
