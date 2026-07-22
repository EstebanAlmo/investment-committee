# Spec 004 - Application Service

## Objective

Define the Application Service layer (`docs/architecture.md`, layer 6): the
single orchestration entry point that composes `market_intelligence` →
`indicators` → `committee` into one immutable analysis result for a single
ticker. Every future interface (internal CLI, Telegram, a future web
interface, schedulers, historical evaluation workflows) calls this layer and
never orchestrates `market_intelligence`, `indicators`, or `committee`
directly — product principle 4 in `docs/architecture.md`.

This is the first Epic that has no committee-domain modeling of its own; it
wires together components that already exist and are already tested:
`MarketIntelligenceService` (Epic 2), `IndicatorEngine` (Epic 3), and
`DeterministicCommitteeEngine` (Epic 5). Nothing here recalculates an
indicator, re-implements a committee rule, or introduces a new domain
concept.

```
symbol: str
     │
     ▼
ApplicationService.analyze_asset
     │
     ├─▶ MarketIntelligenceService.get_daily_history(ticker) ──▶ MarketSnapshot
     │
     ├─▶ IndicatorEngine.calculate(market_snapshot) ──▶ IndicatorSnapshot
     │
     ├─▶ DeterministicCommitteeEngine.run(indicator_snapshot) ──▶ InvestmentDecision
     │
     ▼
AnalysisResult(market_snapshot, indicator_snapshot, decision)
```

## Context

Epics 1-5 are complete and independently tested:

- `domain/`: `Asset`, `PriceBar`, `MarketSnapshot`, `Recommendation` — frozen
  dataclasses, standard library only.
- `market_intelligence/`: `MarketIntelligenceService.get_daily_history(ticker: str) -> MarketSnapshot`,
  backed by `MarketDataProvider` (`YFinanceProvider` by default). Raises
  `MarketDataError` (`market_intelligence/providers/base.py`). Notably, this
  layer already accepts a **raw ticker string**, already normalizes it
  (`ticker.strip().upper()`), and already constructs the `Asset` itself
  inside `YFinanceProvider.get_daily_history` — callers never build an
  `Asset` and hand it to this layer.
- `indicators/`: `IndicatorEngine.calculate(snapshot: MarketSnapshot) -> IndicatorSnapshot`.
  Raises `IndicatorCalculationError` (`indicators/engine.py`) for
  structurally invalid input; insufficient history per-indicator is `None`,
  not an exception.
- `committee/`: `DeterministicCommitteeEngine(technical_analyst, risk_manager,
  devils_advocate, chair, clock=...).run(snapshot: IndicatorSnapshot) ->
  InvestmentDecision`. Raises `CommitteeEngineError`
  (`committee/errors.py`). The engine already owns its own `Clock` and
  stamps one `generated_at` across all three `AgentReport`s and the
  `InvestmentDecision` for a single `run()` call.

Each of these three layers already timestamps its own output
independently: `MarketSnapshot.generated_at` (fetch time),
`IndicatorSnapshot.generated_at` (calculation time), and
`InvestmentDecision.generated_at` (committee run time). This is
pre-existing behavior from Epics 2, 3, and 5 — Epic 6 does not unify these
into one Application-layer timestamp (see "Reproducibility" and
"Unresolved questions").

`interfaces/` and `memory/` currently exist only as empty placeholder
packages.

## Non-goals

Per the Epic prompt, this Epic must not include:

- persistence of any kind;
- the Recommendation Ledger / Decision Ledger (Epic 7);
- paper portfolio or simulation logic (Epic 8);
- performance evaluation (Epic 9);
- LLM analysts (Epic 11);
- Telegram (Epic 10) or any other product-facing interface;
- FastAPI, database integration, broker integration, background jobs,
  caching;
- new external dependencies (standard library only for all new
  `application/` and `cli.py` code).

## Architecture boundaries

Per `docs/architecture.md`:

- `application/` depends on `committee`, `market_intelligence`,
  `indicators`, and `domain` (for type references). It gains no dependency
  on `memory`, `evaluation`, `portfolio`, `simulation`, or `education` in
  this Epic — those compositions are deferred to the Epics that introduce
  them (7-9), even though the layer diagram already reserves the dependency
  for later.
- `application/` has no dependency on `interfaces` or `integrations`.
  `integrations` is only ever reached indirectly, through
  `market_intelligence`'s own adapter boundary.
- `cli.py` and `__main__.py` (in `interfaces/`'s conceptual position, but
  physically at the package root — see "Package structure") depend only on
  `application/`. They contain no orchestration and no business logic of
  their own.
- Nothing in `market_intelligence/`, `indicators/`, or `committee/` gains a
  new dependency on `application/`.

## Package structure

```
src/investment_committee/application/
    __init__.py     # public exports
    service.py       # ApplicationService
    models.py        # AnalysisResult
    errors.py         # ApplicationServiceError hierarchy
    factory.py         # build_default_application_service()

src/investment_committee/cli.py
src/investment_committee/__main__.py
```

This matches the structure proposed in the Epic prompt exactly, and mirrors
`committee/`'s existing shape (`models.py` + `errors.py` + behavior file).
No structural deviation is justified by current conventions.

`cli.py` and `__main__.py` live at the package root rather than inside a new
`interfaces/` subpackage. `interfaces/` (currently an empty placeholder)
remains reserved for Epic 10's Telegram bot and any future web interface —
real, product-facing user experiences per `docs/architecture.md` layer 7.
The Epic 6 CLI is explicitly *not* that: it is a developer/debugging tool
that happens to ship alongside the Application Service
(`docs/roadmap.md`, Epic 6), so it sits next to `application/` at the
package root instead of inside the package reserved for product-facing
interfaces. This avoids implying the dev CLI is "interface #1" when
Telegram (Epic 10) is the first real one.

## Public API

### `application/service.py`

```python
from __future__ import annotations

from investment_committee.committee import DeterministicCommitteeEngine
from investment_committee.indicators import IndicatorEngine
from investment_committee.market_intelligence import MarketIntelligenceService

from .models import AnalysisResult


class ApplicationService:
    """Single entry point that drives one full analysis pass for one ticker."""

    def __init__(
        self,
        market_intelligence_service: MarketIntelligenceService,
        indicator_engine: IndicatorEngine,
        committee_engine: DeterministicCommitteeEngine,
    ) -> None:
        self._market_intelligence_service = market_intelligence_service
        self._indicator_engine = indicator_engine
        self._committee_engine = committee_engine

    def analyze_asset(self, symbol: str) -> AnalysisResult:
        """Run one full analysis pass for `symbol` and return its AnalysisResult.

        Fetches market data exactly once, computes indicators exactly once,
        and runs the committee exactly once. Raises a subclass of
        ApplicationServiceError (see errors.py) on any failure.
        """
        raise NotImplementedError
```

### `application/__init__.py`

```python
__all__ = [
    "ApplicationService",
    "AnalysisResult",
    "ApplicationServiceError",
    "InvalidTickerError",
    "MarketDataUnavailableError",
    "IndicatorCalculationFailedError",
    "CommitteeDecisionFailedError",
    "build_default_application_service",
    "analyze_asset",
]
```

### Convenience function

```python
def analyze_asset(symbol: str) -> AnalysisResult:
    """Convenience wrapper: analyze_asset via a freshly built default ApplicationService.

    Builds a new ApplicationService (and therefore a new default
    YFinanceProvider) on every call. Fine for a single ad hoc analysis or
    the dev CLI; callers making repeated calls (a scheduler, a batch
    evaluation run) should build one ApplicationService via
    build_default_application_service() and reuse it, both to avoid
    reconstructing collaborators repeatedly and because a future Epic may
    give some collaborator real per-instance state (e.g. an HTTP session)
    that is wasteful to recreate per call.
    """
    return build_default_application_service().analyze_asset(symbol)
```

**Design choice: the class is the core API; the function is a thin,
optional convenience.** `ApplicationService` is what tests construct with
fakes and what every real interface (Telegram, future web, schedulers)
depends on via dependency injection. The module-level `analyze_asset`
function exists only for the simplest callers (a REPL, a one-off script,
the dev CLI) and does nothing `ApplicationService` doesn't already do —
flagged in "Design decisions requiring approval" as a judgment call, since
an argument exists for omitting it entirely and requiring even the CLI to
use `build_default_application_service()` explicitly.

## Input normalization

The public entry point accepts a **raw ticker string only** —
`analyze_asset(symbol: str)`, not an `Asset`. This mirrors
`MarketIntelligenceService.get_daily_history(ticker: str)` exactly:
`Asset` construction already happens one layer down, inside
`YFinanceProvider`, using only the ticker (`AssetType.STOCK`, no name,
exchange, or currency populated). There is currently no lower-layer support
for a caller-supplied `Asset` — accepting one here would be a new API
surface with nothing underneath it to honor it, so it is explicitly out of
scope until a future Epic gives `market_intelligence` a reason to accept
pre-built `Asset`s (e.g. ETF metadata lookup). See "Unresolved questions."

Normalization, performed by `ApplicationService.analyze_asset` before any
call to `market_intelligence`:

1. `symbol.strip()` — surrounding whitespace is stripped, matching
   `Asset.__post_init__` and `YFinanceProvider`'s own normalization.
2. If the stripped string is empty, raise `InvalidTickerError` immediately
   — no network call is made for blank input.
3. Uppercase the stripped string (`.upper()`), matching
   `Asset.__post_init__` and `YFinanceProvider`'s convention exactly, so
   the ticker Application Service logs/reports and the ticker
   `market_intelligence` and `domain` end up storing are identical.
4. If the normalized string contains internal whitespace (e.g. `"AA PL"`),
   raise `InvalidTickerError` — no real provider ticker format contains a
   space, so this catches an obviously malformed input before spending a
   network call to discover the same thing via `MarketDataError`. This is
   a new rule beyond what `YFinanceProvider` itself checks today — flagged
   in "Design decisions requiring approval."
5. **No other character restriction is applied.** Symbols such as `BRK.B`,
   `BTC-USD`, `^GSPC`, or `EURUSD=X` are all passed through unchanged
   (after strip/uppercase) — `.`, `-`, `^`, and `=` are all real
   `yfinance` ticker characters. Rejecting them would violate the Epic's
   explicit instruction not to invent validation rules that reject valid
   provider symbols.

The normalized ticker is what gets passed to
`market_intelligence_service.get_daily_history(...)`. `market_intelligence`
and `domain` independently re-derive the same normalization on their own
inputs (defense in depth, already true today); Application Service's check
is strictly a fail-fast, no-network-call layer in front of that, not a
replacement for it.

## `AnalysisResult`

### Decision: richer result, not bare `InvestmentDecision`

`ApplicationService.analyze_asset` returns an immutable `AnalysisResult`
containing the `MarketSnapshot`, `IndicatorSnapshot`, and
`InvestmentDecision` produced by one analysis pass — not just the
`InvestmentDecision` alone.

Rationale, directly from the Epic 7 (Recommendation/Decision Ledger)
requirement: once that ledger exists, it must be able to record "exactly
what the committee knew when it made the decision" (product principle 2,
`docs/architecture.md`) — the `MarketSnapshot` and `IndicatorSnapshot` that
produced a given `InvestmentDecision`, not just the decision's own summary
fields. If `analyze_asset` returned only the `InvestmentDecision`, that
supporting context would be discarded the moment `analyze_asset` returns,
and Epic 7 would have no way to recover it without re-running the analysis
(which would fetch fresh, different market data — defeating the point, and
violating "must not reconstruct historical context later" in the Epic
prompt). Returning the richer result now costs nothing (every field is
already constructed in memory during the call) and directly unblocks Epic
7 without requiring `analyze_asset`'s signature to change later.

### `application/models.py`

```python
from __future__ import annotations

from dataclasses import dataclass

from investment_committee.committee import InvestmentDecision
from investment_committee.domain import Asset, MarketSnapshot
from investment_committee.indicators import IndicatorSnapshot


@dataclass(frozen=True)
class AnalysisResult:
    """Everything one ApplicationService.analyze_asset() call produced.

    Preserves the exact MarketSnapshot, IndicatorSnapshot, and
    InvestmentDecision instances from a single analysis pass, unmodified —
    this is what a future Epic 7 ledger will persist verbatim.
    """

    market_snapshot: MarketSnapshot
    indicator_snapshot: IndicatorSnapshot
    decision: InvestmentDecision

    def __post_init__(self) -> None:
        if not (
            self.market_snapshot.asset
            == self.indicator_snapshot.asset
            == self.decision.asset
        ):
            raise ValueError(
                "AnalysisResult requires market_snapshot, indicator_snapshot, "
                "and decision to share the same asset"
            )

    @property
    def asset(self) -> Asset:
        """The asset this result's analysis was performed for."""
        return self.decision.asset
```

- No separate top-level `generated_at`/metadata field is added.
  `market_snapshot.generated_at`, `indicator_snapshot.generated_at`, and
  `decision.generated_at` already exist and already capture each stage's
  own timing; a fourth, Application-layer timestamp would be redundant and
  could silently drift from the three real ones. See "Reproducibility."
- `asset` is a computed property, not a stored field — exactly the pattern
  `IndicatorSnapshot.asset` already uses (`indicators/models.py`) to avoid
  a field that could only ever equal a value already derivable from another
  field.
- The `__post_init__` cross-check is defensive, matching this codebase's
  existing convention that every frozen dataclass validates its own
  invariants at construction (`domain/`, `committee/models.py`,
  `indicators/models.py`). In practice this can only fail if
  `ApplicationService` itself has a bug — `IndicatorEngine.calculate`
  always returns a snapshot for the same `MarketSnapshot.asset`, and
  `DeterministicCommitteeEngine.run` already raises `CommitteeEngineError`
  if a report's asset diverges from the `IndicatorSnapshot`'s. Flagged in
  "Design decisions requiring approval" as arguably-redundant-but-cheap
  defensive validation, consistent with the rest of the codebase's style
  rather than strictly load-bearing.
- `AnalysisResult` never modifies `InvestmentDecision` or any of its
  fields — it only aggregates references to already-fully-constructed,
  frozen objects. This is why `AnalysisResult` is a thin wrapper rather
  than, say, a subclass or a mutated copy of `InvestmentDecision`: the
  contract that `InvestmentDecision` is "immutable historical truth"
  (product principle 2) must not be blurred by an Application-layer type
  that looks like it could carry extra mutable state.

## Dependency injection design

`ApplicationService.__init__` takes exactly three constructor-injected
collaborators, reusing existing public types with no new abstractions:

| Parameter | Type | Reused from |
|---|---|---|
| `market_intelligence_service` | `MarketIntelligenceService` | Epic 2 |
| `indicator_engine` | `IndicatorEngine` | Epic 3 |
| `committee_engine` | `DeterministicCommitteeEngine` | Epic 5 |

No asset factory / symbol parser class is introduced — ticker
normalization (see above) is four lines of `str` logic with no state and no
reason to be its own injected collaborator; introducing one now would be
speculative abstraction the Epic prompt explicitly warns against.

No clock is injected at this layer. Every stage already produces its own
`generated_at` (`MarketSnapshot` via `YFinanceProvider`,
`IndicatorSnapshot` via `IndicatorEngine`, `InvestmentDecision` via
`DeterministicCommitteeEngine`'s own injected `Clock`), and
`AnalysisResult` adds no fourth timestamp of its own to keep in sync. A
clock genuinely would be needed here only if `AnalysisResult` gained an
Application-layer timestamp field — which this spec deliberately does not
add.

**No `isinstance` validation of injected dependencies.** Unlike
`DeterministicCommitteeEngine.__init__`, which validates each analyst
parameter's `role` (because three same-typed `Analyst` parameters could
have their roles accidentally swapped), `ApplicationService`'s three
parameters are three *different* types with no analogous swap risk, so
`__init__` simply stores the references. This also means test doubles do
not need to subclass the concrete `MarketIntelligenceService` /
`IndicatorEngine` / `DeterministicCommitteeEngine` classes — any
duck-typed fake exposing the right method signature works, which keeps the
unit test plan below simple. Flagged in "Design decisions requiring
approval" as a deliberate divergence from `committee/engine.py`'s stricter
pattern.

## Composition root

### `application/factory.py`

```python
from __future__ import annotations

from investment_committee.committee import (
    DeterministicCommitteeChair,
    DeterministicCommitteeEngine,
    DeterministicDevilsAdvocate,
    DeterministicRiskManager,
    DeterministicTechnicalAnalyst,
)
from investment_committee.indicators import IndicatorEngine
from investment_committee.market_intelligence import MarketIntelligenceService

from .service import ApplicationService


def build_default_application_service() -> ApplicationService:
    """Assemble the production ApplicationService with its default collaborators.

    Constructs MarketIntelligenceService with its default provider
    (YFinanceProvider), a plain IndicatorEngine, and a
    DeterministicCommitteeEngine wired with the three deterministic
    analysts and chair. No network access or other I/O happens during
    construction — only during a subsequent analyze_asset() call.
    """
    return ApplicationService(
        market_intelligence_service=MarketIntelligenceService(),
        indicator_engine=IndicatorEngine(),
        committee_engine=DeterministicCommitteeEngine(
            technical_analyst=DeterministicTechnicalAnalyst(),
            risk_manager=DeterministicRiskManager(),
            devils_advocate=DeterministicDevilsAdvocate(),
            chair=DeterministicCommitteeChair(),
        ),
    )
```

Object construction (this function) is kept entirely separate from
orchestration logic (`ApplicationService.analyze_asset`) — the factory
never calls `analyze_asset` and `ApplicationService` never constructs its
own collaborators. `MarketIntelligenceService()` with no `provider`
argument already defaults to `YFinanceProvider()` (`market_intelligence/service.py`,
unchanged from Epic 2); the factory relies on that existing default rather
than duplicating it.

`build_default_application_service` is the only place in this Epic allowed
to import concrete implementations
(`YFinanceProvider` indirectly via `MarketIntelligenceService`,
`DeterministicTechnicalAnalyst`, etc.) for production wiring purposes. This
belongs in `application/factory.py`, not `bootstrap.py` or a
project-root script, matching the package structure proposed in the Epic
prompt and keeping every application-layer file inside `application/`.

## Orchestration sequence

`ApplicationService.analyze_asset(symbol)`, in exact order:

1. Normalize `symbol` (see "Input normalization"). Raise
   `InvalidTickerError` and return immediately on blank or malformed input
   — no collaborator is called yet.
2. Call `self._market_intelligence_service.get_daily_history(ticker)`
   exactly once. On `MarketDataError`, raise `MarketDataUnavailableError`
   from it (see "Error model") and stop — no further collaborator is
   called.
3. Call `self._indicator_engine.calculate(market_snapshot)` exactly once,
   passing the exact `MarketSnapshot` object returned in step 2 unchanged.
   On `IndicatorCalculationError`, raise `IndicatorCalculationFailedError`
   from it and stop.
4. Call `self._committee_engine.run(indicator_snapshot)` exactly once,
   passing the exact `IndicatorSnapshot` object returned in step 3
   unchanged. On `CommitteeEngineError`, raise
   `CommitteeDecisionFailedError` from it and stop.
5. Construct and return
   `AnalysisResult(market_snapshot, indicator_snapshot, decision)` from the
   exact three objects produced above — nothing is recomputed, nothing is
   re-fetched.

`ApplicationService` never recalculates an indicator itself, never
encodes an analyst rule itself, never accesses persistence, and never
calls anything in `interfaces/`. It calls each of its three collaborators
exactly once per `analyze_asset` call, in this fixed order, and passes
each stage's output to the next unchanged.

## Error model

### Decision: translate into a small Application Service hierarchy

`ApplicationService` catches each lower layer's typed exception and
re-raises a corresponding `ApplicationServiceError` subclass, always using
`raise NewError(...) from original_exc` so the original exception is
preserved on `__cause__` and never destroyed. It does not let
`MarketDataError`, `IndicatorCalculationError`, or `CommitteeEngineError`
propagate directly.

Rationale: `docs/architecture.md` product principle 4 says interfaces call
the Application Service only — never `market_intelligence`, `indicators`,
or `committee` directly. If those layers' own exception types propagated
unmodified, every interface (the dev CLI today; Telegram in Epic 10; a
future web interface) would still need to import and handle
`market_intelligence.MarketDataError`, `indicators.IndicatorCalculationError`,
and `committee.CommitteeEngineError` individually to give the user any
differentiated feedback — quietly reintroducing the exact coupling
product principle 4 exists to prevent, just at the exception-handling
level instead of the call level. A small, stable
`ApplicationServiceError` hierarchy is the one exception vocabulary every
interface needs to know.

### `application/errors.py`

```python
from __future__ import annotations


class ApplicationServiceError(Exception):
    """Base class for every error ApplicationService.analyze_asset can raise."""


class InvalidTickerError(ApplicationServiceError):
    """Raised when the input symbol fails Application Service input validation.

    Raised before any collaborator is called - no network access has
    happened yet when this is raised.
    """


class MarketDataUnavailableError(ApplicationServiceError):
    """Wraps a market_intelligence.MarketDataError from get_daily_history."""


class IndicatorCalculationFailedError(ApplicationServiceError):
    """Wraps an indicators.IndicatorCalculationError from IndicatorEngine.calculate."""


class CommitteeDecisionFailedError(ApplicationServiceError):
    """Wraps a committee.CommitteeEngineError from DeterministicCommitteeEngine.run."""
```

### Behavior table

| Failure | Raised by | ApplicationService raises | Original exception |
|---|---|---|---|
| Blank/whitespace-only symbol | Application Service itself | `InvalidTickerError` | n/a — no lower-layer call made |
| Symbol contains internal whitespace | Application Service itself | `InvalidTickerError` | n/a |
| Invalid ticker / no data / provider failure | `market_intelligence` (`MarketDataError`) | `MarketDataUnavailableError` | chained via `from` |
| Empty/unordered/invalid `MarketSnapshot` | `indicators` (`IndicatorCalculationError`) | `IndicatorCalculationFailedError` | chained via `from` |
| Analyst/chair wiring or role/asset mismatch | `committee` (`CommitteeEngineError`) | `CommitteeDecisionFailedError` | chained via `from` |

**Anything else propagates unmodified.** In particular,
`ApplicationService` does **not** catch bare `ValueError` (e.g. from a
`domain/` or `committee/models.py` frozen dataclass's own
`__post_init__`). Under normal operation this path is not expected to
raise — `market_intelligence` and `committee` already validate their own
outputs before returning them — so a stray `ValueError` here would
indicate a real bug upstream, not an expected runtime failure a user
should see a friendly message for. Swallowing or wrapping it would hide
that bug instead of surfacing it. This is a deliberate scope boundary: the
error hierarchy above translates exactly the three named typed exceptions
and nothing else.

The CLI (below) is the only place allowed to turn an
`ApplicationServiceError` into a user-facing string; `ApplicationService`
itself stays interface-neutral — its exceptions carry the original typed
cause but no formatting or presentation logic.

## CLI design

### Invocation

```
python -m investment_committee analyze MSFT
```

Matches `docs/roadmap.md`'s Epic 6 description and
`docs/architecture.md`'s "User Experience" section exactly.

### `src/investment_committee/cli.py`

```python
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from investment_committee.application import ApplicationService, build_default_application_service
from investment_committee.application.errors import ApplicationServiceError


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    service = build_default_application_service()
    return _run_analyze(service, args.symbol)


def _run_analyze(service: ApplicationService, symbol: str) -> int:
    try:
        result = service.analyze_asset(symbol)
    except ApplicationServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(_format_result(result))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="investment_committee")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="Analyze one ticker")
    analyze_parser.add_argument("symbol", help="Ticker symbol, e.g. MSFT")

    return parser


def _format_result(result) -> str:
    decision = result.decision
    return (
        f"{decision.asset.ticker}: {decision.action.value} "
        f"(confidence {decision.confidence})\n"
        f"{decision.summary}"
    )
```

### `src/investment_committee/__main__.py`

```python
from __future__ import annotations

import sys

from investment_committee.cli import main

if __name__ == "__main__":
    sys.exit(main())
```

### CLI design points

- `main(argv=None)` accepts an optional explicit `argv` (defaulting to
  `argparse`'s own `sys.argv[1:]` behavior) purely so tests can call
  `main(["analyze", "MSFT"])` directly without subprocessing.
- `argparse` subparsers are used even though there is only one subcommand
  today, so that `python -m investment_committee analyze MSFT` (the exact
  invocation named in the roadmap) works and future dev-CLI commands don't
  require redesigning the parser. Flagged in "Design decisions requiring
  approval" as slightly more structure than the bare minimum a single
  command would need.
- The CLI builds exactly one `ApplicationService` per process (via
  `build_default_application_service()`), calls
  `analyze_asset(args.symbol)` exactly once, and does nothing else with
  domain data — no formatting logic beyond string interpolation of fields
  already present on `AnalysisResult`/`InvestmentDecision`, no retry logic,
  no fallback provider selection.
- Missing the `symbol` argument, or an unrecognized subcommand, is handled
  by `argparse` itself (prints usage to stderr, raises `SystemExit(2)`) —
  the CLI adds no custom handling for that case.
- Exit codes: `0` on success, `1` on any `ApplicationServiceError`, `2` on
  an argument-parsing error (argparse's own default). No other exit code
  is defined.
- The CLI never imports `market_intelligence`, `indicators`, or
  `committee` directly, and never catches `MarketDataError`,
  `IndicatorCalculationError`, or `CommitteeEngineError` — only
  `investment_committee.application` and
  `investment_committee.application.errors`.

## Reproducibility and Epic 7 preparation

- `analyze_asset` calls `market_intelligence_service.get_daily_history`
  exactly once per invocation — there is no retry, no caching, and no
  second fetch anywhere in this Epic. Every `MarketSnapshot` returned in
  an `AnalysisResult` is fetched fresh, exactly once, for that call.
- The `MarketSnapshot`, `IndicatorSnapshot`, and `InvestmentDecision`
  objects placed into `AnalysisResult` are the literal objects returned by
  `market_intelligence`, `indicators`, and `committee` respectively — never
  copies, never reconstructions from summary data. `AnalysisResult`'s
  `__post_init__` performs a read-only consistency check (see
  `models.py` above); it does not transform or replace any field.
- `application/` never reconstructs a past analysis from stored data —
  there is no persistence to reconstruct from yet, and no code path here
  reads anything other than what the current call itself produced.
- **This is deliberately what Epic 7 (Recommendation Ledger / Decision
  Ledger) needs.** Once `memory/` exists, its write path can take an
  `AnalysisResult` (or its three fields individually) directly from a
  single `analyze_asset()` call and persist it verbatim — no
  Application-layer change is required to support that; Epic 7 only needs
  to add a fourth collaborator (a ledger/repository) to `ApplicationService`
  and call it once, after step 5 of the orchestration sequence, with the
  already-fully-formed `AnalysisResult`. This spec does not implement that
  write path (out of scope, see "Non-goals") but the shape of
  `AnalysisResult` is chosen specifically so that addition requires no
  redesign.
- The Epic prompt refers to the next Epic as "Recommendation Ledger,
  currently... likely to become Decision Ledger." This spec does not
  rename that concept — `docs/roadmap.md` still says "Epic 7 —
  Recommendation Ledger" and this document leaves it untouched, per the
  Epic prompt's own instruction. Any renaming is a separate, deliberate
  follow-up flagged in "Unresolved questions," not something this spec
  performs.

## Testing plan

### `tests/application/test_service.py`

Using duck-typed fakes for all three collaborators (a `FakeMarketIntelligenceService`
with a scriptable `get_daily_history`, a `FakeIndicatorEngine` with a
scriptable `calculate`, a `FakeCommitteeEngine` with a scriptable `run` —
none need to subclass the concrete classes, per "no `isinstance`
validation" above):

- valid ticker orchestration: given fakes that return a fixed
  `MarketSnapshot` → `IndicatorSnapshot` → `InvestmentDecision` chain,
  `analyze_asset("MSFT")` returns an `AnalysisResult` wrapping exactly
  those three objects (`is` identity, not just equality);
- input trimming and normalization: `"  msft  "`, `"msft"`, `" MSFT"`,
  `"MSFT"` all normalize to the ticker `"MSFT"` passed to
  `get_daily_history` (assert the fake's recorded argument);
- empty input (`""`, `"   "`) raises `InvalidTickerError` and never calls
  any fake (assert zero calls on all three fakes);
- internal whitespace (`"AA PL"`) raises `InvalidTickerError` and never
  calls any fake;
- exact dependency call order: a shared call-order-recording fixture
  proves `get_daily_history` is called before `calculate`, which is called
  before `run`;
- market data fetched exactly once per `analyze_asset` call (assert the
  fake's call count == 1);
- snapshots passed unchanged between layers: the fake `IndicatorEngine`
  records the exact `MarketSnapshot` object it received and the test
  asserts it `is` the object the fake `MarketIntelligenceService`
  returned; likewise the fake `CommitteeEngine` records the exact
  `IndicatorSnapshot` object it received;
- returned `AnalysisResult`'s `market_snapshot`, `indicator_snapshot`, and
  `decision` fields are exactly (`is`) the three fakes' return values;
- dependency injection with fakes: `ApplicationService` can be
  constructed entirely from fakes with no reference to
  `YFinanceProvider`/`IndicatorEngine`/`DeterministicCommitteeEngine`,
  proving the class needs no concrete production types to be testable;
- provider failure: a fake `MarketIntelligenceService` that raises
  `MarketDataError` causes `analyze_asset` to raise
  `MarketDataUnavailableError` with the original `MarketDataError` as
  `__cause__` (`exc.__cause__ is original`); `calculate` and `run` are
  never called;
- indicator failure: a fake `IndicatorEngine` that raises
  `IndicatorCalculationError` causes `analyze_asset` to raise
  `IndicatorCalculationFailedError` chained to the original; `run` is
  never called;
- committee failure: a fake `CommitteeEngine` that raises
  `CommitteeEngineError` causes `analyze_asset` to raise
  `CommitteeDecisionFailedError` chained to the original;
- exception chaining: for each of the three wrapped-error cases above,
  assert `isinstance(exc.__cause__, <original exception type>)` explicitly
  — not just that some cause is set;
- an unexpected exception type (e.g. a fake that raises a bare
  `RuntimeError` or `ValueError` instead of its expected typed exception)
  propagates from `analyze_asset` unmodified — proving
  `ApplicationService` does not over-broadly catch `Exception`;
- no persistence side effects: `ApplicationService` (constructed with
  fakes) never imports or references `investment_committee.memory`;
  `analyze_asset` performs no filesystem or database writes (this can be
  checked at the level of "the fakes are the only I/O the test performed"
  since `application/service.py` itself has no I/O of its own — see also
  the static import check below).

### `tests/application/test_models.py`

- valid `AnalysisResult` construction with matching-asset
  `market_snapshot`/`indicator_snapshot`/`decision`;
- mismatched asset on any one of the three fields raises `ValueError`;
- `AnalysisResult.asset` returns `decision.asset` (property, not a stored
  field);
- `AnalysisResult` is frozen (attempting to reassign a field raises
  `dataclasses.FrozenInstanceError` — matches the convention already
  tested for `MarketSnapshot`/`InvestmentDecision` elsewhere).

### `tests/application/test_errors.py`

- every `ApplicationServiceError` subclass is a subclass of
  `ApplicationServiceError` and of `Exception`;
- each subclass can be constructed with a message and that message is
  preserved (`str(exc)`).

### `tests/application/test_factory.py`

- `build_default_application_service()` returns an `ApplicationService`
  instance;
- the returned service's collaborators are instances of the expected
  concrete types (`MarketIntelligenceService`, `IndicatorEngine`,
  `DeterministicCommitteeEngine`) — inspecting the private attributes is
  acceptable here since this test exists specifically to pin the
  composition root's wiring;
- no network call happens during construction: monkeypatch
  `YFinanceProvider.get_daily_history` (or the underlying `yfinance.Ticker`)
  to raise if called, then call `build_default_application_service()` and
  assert no error — proving construction alone never fetches data.

### `tests/test_cli.py`

- success path: `main(["analyze", "MSFT"])` with a monkeypatched
  `build_default_application_service` (or a monkeypatched
  `ApplicationService.analyze_asset`) returning a fixed `AnalysisResult`
  → returns `0` and prints a summary containing the ticker, action, and
  confidence (`capsys`);
- failure path: the same setup but raising each
  `ApplicationServiceError` subclass in turn → returns `1` and prints an
  `error:`-prefixed message to stderr, not stdout;
- readable output: assert the printed summary is human-readable text
  (contains the ticker and the action name), not a raw `repr(...)` dump of
  the dataclass;
- calls only `ApplicationService`/injected callable: assert `cli.py`'s
  module source contains no import of `market_intelligence`, `indicators`,
  or `committee` (a lightweight static check, e.g. via `ast.parse` over
  `inspect.getsource(cli)`) — enforcing "no business logic in the CLI" at
  the level the acceptance criteria describe it;
- missing argument handling: `main(["analyze"])` (no symbol) and
  `main([])` (no subcommand) both raise `SystemExit` with code `2`
  (argparse's own behavior) rather than crashing with an unhandled
  traceback.

### Composition-root / architecture check

A small static test (e.g. `tests/application/test_no_forbidden_imports.py`)
scanning `application/*.py` and `cli.py` source for `import` statements
referencing `investment_committee.memory`, `investment_committee.portfolio`,
`investment_committee.simulation`, `investment_committee.evaluation`, or
`investment_committee.interfaces` — none should appear, matching the "must
not include persistence" non-goal and the "never call interfaces"
orchestration rule. Mirrors the same style of check already implied (but
not automated) by spec-002/spec-003's acceptance criteria
("No import of `market_intelligence`... anywhere in `committee/`").

### Existing test suites

No changes are needed to `tests/domain/`, `tests/indicators/`,
`tests/committee/`, or `tests/market_intelligence/` — this Epic adds a new
consumer of those layers' existing public APIs and changes none of them.

## Acceptance criteria

- `src/investment_committee/application/{__init__,service,models,errors,factory}.py`
  exist and implement exactly the public API in this spec.
- `src/investment_committee/cli.py` and
  `src/investment_committee/__main__.py` exist and implement exactly the
  CLI design in this spec.
- `ApplicationService.analyze_asset(symbol: str) -> AnalysisResult` fetches
  market data exactly once, computes indicators exactly once, and runs the
  committee exactly once per call, in that fixed order, passing each
  stage's output to the next unchanged.
- `AnalysisResult` is a frozen dataclass with `market_snapshot`,
  `indicator_snapshot`, and `decision` fields, a computed `asset` property,
  and a same-asset consistency check in `__post_init__`.
- Blank or internally-whitespaced ticker input raises `InvalidTickerError`
  without calling any injected collaborator.
- `MarketDataError`, `IndicatorCalculationError`, and `CommitteeEngineError`
  are each translated into a distinct `ApplicationServiceError` subclass
  with the original exception preserved as `__cause__`; no other exception
  type is caught or translated.
- `build_default_application_service()` returns a fully-wired
  `ApplicationService` using `YFinanceProvider` (via
  `MarketIntelligenceService`'s own default), a plain `IndicatorEngine`,
  and a `DeterministicCommitteeEngine` wired with the three deterministic
  analysts and chair — with no network access during construction.
- `python -m investment_committee analyze <SYMBOL>` runs one analysis,
  prints a readable summary to stdout, and exits `0`; any
  `ApplicationServiceError` is printed to stderr and exits `1`; missing
  arguments exit `2` via `argparse`'s own handling.
- The CLI contains no business logic and imports only
  `investment_committee.application` (and its `errors` submodule).
- No import of `memory`, `portfolio`, `simulation`, `evaluation`, or
  `interfaces` anywhere in `application/` or `cli.py`.
- No new external dependencies; standard library only for all new code.
- `pytest` passes, including all new tests under `tests/application/` and
  `tests/test_cli.py`, with no regressions in existing suites.
- `docs/architecture.md` and `docs/roadmap.md` are left untouched by this
  Epic.

## Explicit design decisions

- **E1 — `ApplicationService` is the core API; `analyze_asset` is an
  optional module-level convenience function.** See "Public API."
- **E2 — Input is a raw ticker string only; no `Asset`-accepting entry
  point is added**, because `market_intelligence` has no lower-layer
  support for a caller-supplied `Asset` today. See "Input normalization."
- **E3 — `analyze_asset` returns a richer `AnalysisResult`, not a bare
  `InvestmentDecision`**, specifically to prepare Epic 7's ledger
  requirement to preserve exact supporting context. See `AnalysisResult`.
- **E4 — No clock is injected into `ApplicationService`.** Every stage
  already stamps its own `generated_at`; `AnalysisResult` adds no fourth
  timestamp. See "Dependency injection design."
- **E5 — No `isinstance` validation of injected dependencies**, unlike
  `DeterministicCommitteeEngine`. See "Dependency injection design."
- **E6 — Lower-layer exceptions are translated into a small
  `ApplicationServiceError` hierarchy, not propagated directly and not
  merged into one generic error type.** See "Error model."
- **E7 — The dev CLI lives at the package root
  (`cli.py`/`__main__.py`), not inside `interfaces/`.** `interfaces/`
  stays reserved for Epic 10's Telegram bot and later product-facing
  surfaces. See "Package structure."
- **E8 — CLI uses `argparse` subparsers for a single `analyze` command**,
  slightly more structure than the bare minimum, to match the roadmap's
  literal invocation and leave room for future dev-CLI commands. See "CLI
  design."
- **E9 — The name `AnalysisResult` was reviewed against `AnalysisSession`,
  `AnalysisContext`, `AnalysisSnapshot`, and `AnalysisBundle`, and
  intentionally retained; this is not the class's first-draft name
  surviving by default.** `AnalysisResult` is an application-layer
  use-case output structure, not a new domain entity — it represents the
  immutable result of one completed `analyze_asset` call, nothing more.
  Each alternative was rejected for a specific reason: `AnalysisSession`
  implies duration, mutable state, or a sequence of operations, none of
  which apply to a single frozen object produced once and never touched
  again; `AnalysisContext` is backwards — a context is normally supplied
  *into* an operation, not returned *from* one; `AnalysisSnapshot` would
  overload a word this codebase already uses for something narrower and
  different (`MarketSnapshot` and `IndicatorSnapshot` both mean "captured
  external/derived state at a point in time," not "the bundled output of
  a use case"); `AnalysisBundle` only communicates that the object is a
  container, saying nothing about its role as a use-case's output.
  `AnalysisResult` also pairs cleanly with the future ledger record:
  `AnalysisResult` is the transient, in-memory use-case output, while the
  eventual `DecisionRecord` (Epic 7) is the durable, persisted ledger
  entry derived from it — see "Unresolved questions." This naming
  decision should not be silently revisited during implementation; it
  should only be reopened if `AnalysisResult`'s responsibility changes
  materially (e.g. if it stops being a single call's immutable output).

## Design decisions requiring approval

Gathered here because each involves a genuine judgment call this spec
made without an unambiguous instruction to resolve it:

1. **Whether the convenience `analyze_asset()` module-level function
   should exist at all**, versus requiring every caller — including the
   dev CLI — to call `build_default_application_service().analyze_asset(...)`
   explicitly. Including it adds a second public surface for the same
   operation; omitting it is more minimal but slightly more verbose for
   the simplest callers.
2. **Rejecting tickers with internal whitespace (`InvalidTickerError`) is
   a new validation rule** not currently enforced by `Asset`,
   `MarketIntelligenceService`, or `YFinanceProvider`. It is narrow (only
   rejects a string no real ticker format ever contains) but is still new
   behavior introduced at this layer rather than reused from an existing
   one.
3. **`AnalysisResult.__post_init__`'s same-asset consistency check is
   defensive, not currently reachable through the public API** given
   `IndicatorEngine` and `DeterministicCommitteeEngine`'s existing
   guarantees. It matches this codebase's "every frozen dataclass
   validates itself" convention but could be considered unnecessary
   given `ApplicationService` is the only caller ever expected to
   construct an `AnalysisResult`.
4. **No `isinstance` validation of `ApplicationService`'s three
   constructor parameters** — a deliberate divergence from
   `DeterministicCommitteeEngine`'s stricter pattern (see E5). If a future
   reviewer wants parity with that pattern, `ApplicationService.__init__`
   would need `isinstance` checks against `MarketIntelligenceService`,
   `IndicatorEngine`, and `DeterministicCommitteeEngine` specifically —
   which would also block duck-typed fakes from being passed directly
   and require fakes to subclass the concrete classes instead.
5. **`argparse` subparsers for a single current subcommand** (E8) is more
   structure than a bare `parser.add_argument("symbol")` would need for
   exactly one command today. The subparser shape is chosen for the
   literal `analyze MSFT` invocation and anticipated future dev-CLI
   commands, not because Epic 6 itself needs more than one command.

## Unresolved questions

Not required for this Epic's implementation, but noted for future epics:

1. **Whether a future Epic (an ETF/asset-metadata lookup, or multi-ticker
   batch analysis) will need `ApplicationService` to accept something
   richer than a raw ticker string** — e.g. an already-resolved `Asset`,
   or a list of symbols. Nothing in this Epic blocks that; it would be an
   additive method (e.g. `analyze_assets(symbols: Sequence[str])`), not a
   change to `analyze_asset`'s existing contract.
2. **Whether Epic 7's ledger will persist `AnalysisResult` directly, or
   wrap it in its own richer record type** (e.g. adding a run id, a
   storage timestamp, or schema version metadata). This spec only
   guarantees `AnalysisResult` carries everything Epic 7 needs verbatim;
   it does not decide Epic 7's own storage shape.
3. **Strategy and version metadata is deliberately outside Epic 6 — a
   documented deferral, not a missing requirement.** `AnalysisResult`
   must not contain a committee version, an indicator engine version, a
   rules/specification version, a system version, a prompt version, a
   model version, or any persistence identifier (e.g. a run id or ledger
   row id). None of these are added in this Epic. This is deliberate, not
   an oversight, for two reasons:
   - **No meaningful versioning convention currently exists to reference
     truthfully.** `DeterministicCommitteeEngine`, `IndicatorEngine`,
     ADR-001, and the current domain models define no version identifier
     of any kind. Inventing a placeholder string or a speculative version
     field in this Epic would create *false* reproducibility — a value
     that looks authoritative but is arbitrary — rather than real
     reproducibility.
   - **`docs/architecture.md` already assigns this responsibility
     elsewhere.** The `memory/` layer description explicitly lists
     "prompt versions, model versions, and audit history" as things that
     layer stores. Version tracking is already an intended Epic 7
     (ledger) concern, not an `application/`-layer one.

   Epic 7 should define version metadata as part of its own persisted
   `DecisionRecord` (or equivalent ledger model), not by retrofitting
   storage metadata into `AnalysisResult`. That ledger record should wrap
   or derive from `AnalysisResult` — reading the exact `market_snapshot`,
   `indicator_snapshot`, and `decision` it already carries — rather than
   `AnalysisResult` growing storage-shaped fields it has no way to
   populate correctly today. When that design work happens, it will
   likely need to identify more than one axis of "version": the overall
   strategy configuration (deterministic vs. future LLM vs. hybrid); the
   composition root / factory configuration that built the
   `ApplicationService` for a given run; per-analyst implementation
   versions, since a hybrid committee (Epic 12) may mix deterministic and
   LLM-backed analysts within one committee; and prompt/model versions
   specifically for any LLM-backed analyst (Epic 11). This spec does not
   resolve any of that — it only confirms none of it belongs on
   `AnalysisResult` or `ApplicationService` in Epic 6.
4. **The three independently-stamped `generated_at` timestamps
   (`MarketSnapshot`, `IndicatorSnapshot`, `InvestmentDecision`) are not
   unified by a single Application-layer clock in this Epic.** This is
   inherited, pre-existing behavior from Epics 2, 3, and 5, not something
   introduced here — flagged in case a future Epic (e.g. Epic 9's
   look-ahead-bias-sensitive evaluation) needs them to be provably close
   together or wants a single canonical "analysis performed at" instant.
5. **Ticker normalization logic (`strip().upper()`) now exists in three
   places** — `Asset.__post_init__` (`domain/assets.py`),
   `YFinanceProvider.get_daily_history` (`market_intelligence/providers/yfinance_provider.py`),
   and this Epic's `ApplicationService.analyze_asset`. A future refactor
   could introduce one shared normalization helper; this spec does not
   perform that refactor, since it would touch already-shipped,
   already-tested files outside this Epic's stated scope.
6. **Naming**: this spec does not rename "Recommendation Ledger" to
   "Decision Ledger" anywhere (`docs/roadmap.md` still uses the former).
   The Epic prompt notes the latter is "likely" — any rename is a
   separate, deliberate documentation change for whoever starts Epic 7,
   not something implied by this spec.
