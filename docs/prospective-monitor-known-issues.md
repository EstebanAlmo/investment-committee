# Prospective monitor: known issues (found 2026-11-11)

Found while adding tests for the existing monitor of the pre-registered
protocol (`application/monitor.py`, window 2026-10-19 to 2027-01-15). The
monitor's rules were **not** changed: these are recorded so that whoever
runs it for the first time knows what to expect. None of them affects the
`ema-rsi-atr-forex` experiment, which uses its own database and monitor.

State on 2026-11-11: the monitor has never run against
`data/investment_committee.db`. That file holds only migration 1, four
registered strategies, 252 retrospective backtest summaries, and no scans,
recommendations or monitor tables.

## 1. Gap counter overstated on a repeated late cycle

- **Where:** `ProspectiveMonitor.run_once`, the `TARDE` branch
  (`return CycleResult(moment, "TARDE", gaps_recorded=gaps + 1)`).
- **What:** when a cycle runs more than `max_lateness` (50 min) after the
  candle closed, the candle is recorded as a `MONITOR_NO_EJECUTADO`
  incident. Running again in the same hour does not record it twice
  (`_gap` returns False), but the returned `gaps_recorded` still adds 1.
- **Impact:** only the number printed by `scripts/monitor.py` for that run.
  The database is correct; reports built from it are unaffected.
- **Covered by:** `test_a_cycle_that_runs_too_late_is_a_gap_and_is_never_evaluated`
  (asserts one incident row after two runs).
- **Fix, if wanted:** add `self._gap(...)`'s return value instead of 1.

## 2. Opening the registry applies migration 2

- **Where:** `memory.database.connect` calls `migrate` on every connection.
- **What:** the real registry is still at migration 1. The first script
  that opens it (`scripts/monitor.py`, `scripts/export_excel.py`,
  `scripts/send_notifications.py`, `scripts/check_opportunities.py`, ...)
  will create the monitor tables (`monitor_cycles`, `monitor_evaluations`,
  `monitor_incidents`, `notifications`) and a unique index on
  `recommendations`.
- **Impact:** additive, and no existing row changes, but the file is
  modified by what looks like a read-only command (the Excel export).
- **Before the first run:** copy `data/investment_committee.db` to a dated
  backup. Read-only inspection without migrating is possible with
  `sqlite3.connect("file:data/investment_committee.db?mode=ro", uri=True)`.

## 3. A first start in mid-window records every missed hour

- **Where:** `ProspectiveMonitor._record_gaps`: with no earlier cycle it
  starts from `window_start`.
- **What:** the first cycle ever run records one `MONITOR_NO_EJECUTADO`
  incident for each open-market hour since 2026-10-19 00:00 UTC (more than
  400 by 2026-11-11, about 120 per week). Those hours are never evaluated
  afterwards, by design: no retrospective signal is produced.
- **Impact:** consistent with the protocol ("hours never processed are
  gaps"), but the incident table and the "Errores y huecos" sheet start
  with a long block of gaps, and the prospective registry covers only the
  part of the window after the first run. The protocol's primary
  evaluation (a replay over a snapshot downloaded after the window ends)
  does not depend on the monitor.
- **Covered by:** `test_hours_the_monitor_missed_are_recorded_as_gaps_not_evaluated_later`.

## Also noted

- `docs/prospective-monitoring.md`, cited by `application/monitor.py` and
  `scripts/monitor.py`, does not exist.
- `scripts/monitor.py` and `scripts/send_notifications.py` have no
  automated tests (they are manual entry points); the classes they call do.
- `build_default_monitor` takes no clock, so the tests set the monitor's
  private `_clock` attribute.
