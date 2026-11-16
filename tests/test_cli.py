"""Tests for investment_committee.cli."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from investment_committee import cli
from investment_committee.application.errors import (
    ApplicationServiceError,
    CommitteeDecisionFailedError,
    IndicatorCalculationFailedError,
    InvalidTickerError,
    MarketDataUnavailableError,
)
from investment_committee.application.models import AnalysisResult
from investment_committee.committee import (
    AgentReport,
    AgentStance,
    CommitteeRole,
    InvestmentAction,
    InvestmentDecision,
)
from investment_committee.domain import Asset, AssetType, MarketSnapshot, PriceBar
from investment_committee.indicators import IndicatorSnapshot

UTC_NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


def _make_analysis_result() -> AnalysisResult:
    asset = Asset(ticker="MSFT", name=None, asset_type=AssetType.STOCK, exchange=None, currency=None)
    bar = PriceBar(
        timestamp=UTC_NOW,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=1_000,
    )
    market_snapshot = MarketSnapshot(asset=asset, generated_at=UTC_NOW, price_bars=[bar])
    indicator_snapshot = IndicatorSnapshot(
        market_snapshot=market_snapshot,
        generated_at=UTC_NOW,
        latest_close=Decimal("100"),
        sma_20=None,
        sma_50=None,
        sma_200=None,
        return_1d=None,
        return_30d=None,
        return_90d=None,
        return_ytd=None,
        annualized_volatility=None,
        maximum_drawdown=None,
        rsi_14=None,
    )

    def _report(role: CommitteeRole) -> AgentReport:
        return AgentReport(
            asset=asset,
            role=role,
            stance=AgentStance.NEUTRAL,
            confidence=Decimal("0.50"),
            summary=f"{role.value} summary",
            reasoning=f"{role.value} reasoning",
            evidence=(),
            generated_at=UTC_NOW,
        )

    decision = InvestmentDecision(
        asset=asset,
        action=InvestmentAction.HOLD,
        confidence=Decimal("0.90"),
        summary="HOLD MSFT: unanimous neutral.",
        agent_reports=[
            _report(CommitteeRole.TECHNICAL_ANALYST),
            _report(CommitteeRole.RISK_MANAGER),
            _report(CommitteeRole.DEVILS_ADVOCATE),
        ],
        dissenting_opinions=(),
        risks=(),
        generated_at=UTC_NOW,
    )
    return AnalysisResult(market_snapshot=market_snapshot, indicator_snapshot=indicator_snapshot, decision=decision)


class _StubService:
    """A fake ApplicationService (duck-typed) for CLI-level tests - no real network access."""

    def __init__(self, result: AnalysisResult | None = None, error: ApplicationServiceError | None = None) -> None:
        self._result = result
        self._error = error

    def analyze_asset(self, symbol: str) -> AnalysisResult:
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def test_success_exit_code_and_output(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    result = _make_analysis_result()
    monkeypatch.setattr(cli, "build_default_application_service", lambda: _StubService(result=result))

    exit_code = cli.main(["analyze", "MSFT"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "MSFT" in captured.out
    assert "HOLD" in captured.out
    assert captured.err == ""


@pytest.mark.parametrize(
    "error",
    [
        InvalidTickerError("ticker symbol must not be blank"),
        MarketDataUnavailableError("no price history returned for XYZ"),
        IndicatorCalculationFailedError("MarketSnapshot contains no PriceBars"),
        CommitteeDecisionFailedError("chair requires exactly one AgentReport per role"),
    ],
)
def test_failure_exit_code_and_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: ApplicationServiceError,
) -> None:
    monkeypatch.setattr(cli, "build_default_application_service", lambda: _StubService(error=error))

    exit_code = cli.main(["analyze", "MSFT"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.startswith("error:")
    assert str(error) in captured.err


def test_readable_output_is_not_a_raw_repr_dump(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result = _make_analysis_result()
    monkeypatch.setattr(cli, "build_default_application_service", lambda: _StubService(result=result))

    cli.main(["analyze", "MSFT"])

    captured = capsys.readouterr()
    assert "AnalysisResult(" not in captured.out
    assert "InvestmentDecision(" not in captured.out


def test_missing_symbol_exits_2() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["analyze"])

    assert exc_info.value.code == 2


def test_missing_command_exits_2() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main([])

    assert exc_info.value.code == 2


def test_cli_uses_only_the_injected_stub_service_no_real_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves the CLI is testable without real network access: build_default_application_service
    is the only seam it calls, and swapping it in is enough to exercise the whole CLI.
    """
    result = _make_analysis_result()
    monkeypatch.setattr(cli, "build_default_application_service", lambda: _StubService(result=result))

    exit_code = cli.main(["analyze", "MSFT"])

    assert exit_code == 0


def test_cli_imports_no_lower_layers_directly() -> None:
    source = Path(cli.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    forbidden_prefixes = (
        "investment_committee.market_intelligence",
        "investment_committee.indicators",
        "investment_committee.committee",
    )
    for module in imported_modules:
        assert not module.startswith(forbidden_prefixes), f"cli.py must not import {module} directly"
