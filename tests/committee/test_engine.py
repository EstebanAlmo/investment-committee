"""Tests for investment_committee.committee.engine."""

from __future__ import annotations

import socket
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_committee.committee.analysts import (
    Analyst,
    DeterministicDevilsAdvocate,
    DeterministicRiskManager,
    DeterministicTechnicalAnalyst,
)
from investment_committee.committee.chair import DeterministicCommitteeChair
from investment_committee.committee.engine import DeterministicCommitteeEngine
from investment_committee.committee.errors import CommitteeEngineError
from investment_committee.committee.models import AgentStance, CommitteeRole

from ._support import make_asset, make_report, make_snapshot

FIXED_TIME = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


class FakeAnalyst(Analyst):
    """A minimal, injectable Analyst stand-in for engine-level tests."""

    def __init__(self, role: CommitteeRole, report_builder=None) -> None:
        self.role = role
        self._report_builder = report_builder
        self.calls: list[tuple[object, datetime]] = []

    def analyze(self, snapshot, generated_at):
        self.calls.append((snapshot, generated_at))
        if self._report_builder is not None:
            return self._report_builder(snapshot, generated_at)
        return make_report(self.role, AgentStance.NEUTRAL, Decimal("0.50"), asset=snapshot.asset, generated_at=generated_at)


def _engine(**overrides) -> DeterministicCommitteeEngine:
    defaults = dict(
        technical_analyst=DeterministicTechnicalAnalyst(),
        risk_manager=DeterministicRiskManager(),
        devils_advocate=DeterministicDevilsAdvocate(),
        chair=DeterministicCommitteeChair(),
        clock=lambda: FIXED_TIME,
    )
    defaults.update(overrides)
    return DeterministicCommitteeEngine(**defaults)


def test_successful_full_execution() -> None:
    snapshot = make_snapshot(
        latest_close=Decimal("130"),
        sma_20=Decimal("120"),
        sma_50=Decimal("110"),
        sma_200=Decimal("100"),
        return_30d=Decimal("0.05"),
        return_90d=Decimal("0.10"),
        return_ytd=Decimal("0.15"),
        annualized_volatility=Decimal("0.15"),
        maximum_drawdown=Decimal("-0.05"),
        rsi_14=Decimal("55"),
    )

    decision = _engine().run(snapshot)

    assert decision.asset == snapshot.asset
    assert len(decision.agent_reports) == 3
    assert {report.role for report in decision.agent_reports} == {
        CommitteeRole.TECHNICAL_ANALYST,
        CommitteeRole.RISK_MANAGER,
        CommitteeRole.DEVILS_ADVOCATE,
    }


def test_clock_is_called_exactly_once_per_run() -> None:
    call_count = 0

    def counting_clock() -> datetime:
        nonlocal call_count
        call_count += 1
        return FIXED_TIME

    engine = _engine(clock=counting_clock)
    engine.run(make_snapshot())

    assert call_count == 1


def test_same_timestamp_on_all_reports_and_decision() -> None:
    decision = _engine().run(make_snapshot())

    for report in decision.agent_reports:
        assert report.generated_at == FIXED_TIME
    assert decision.generated_at == FIXED_TIME


def test_dependency_injection_with_fakes() -> None:
    technical = FakeAnalyst(CommitteeRole.TECHNICAL_ANALYST)
    risk = FakeAnalyst(CommitteeRole.RISK_MANAGER)
    devils_advocate = FakeAnalyst(CommitteeRole.DEVILS_ADVOCATE)
    engine = _engine(technical_analyst=technical, risk_manager=risk, devils_advocate=devils_advocate)
    snapshot = make_snapshot()

    decision = engine.run(snapshot)

    assert len(technical.calls) == 1
    assert len(risk.calls) == 1
    assert len(devils_advocate.calls) == 1
    assert technical.calls[0] == (snapshot, FIXED_TIME)
    assert decision.agent_reports[0].confidence == Decimal("0.50")


def test_duplicate_analyst_roles_rejected() -> None:
    duplicate = FakeAnalyst(CommitteeRole.RISK_MANAGER)

    with pytest.raises(CommitteeEngineError):
        _engine(technical_analyst=duplicate)


def test_wrong_role_in_a_slot_rejected() -> None:
    with pytest.raises(CommitteeEngineError):
        _engine(risk_manager=FakeAnalyst(CommitteeRole.TECHNICAL_ANALYST))


def test_committee_chair_role_as_analyst_rejected() -> None:
    with pytest.raises(CommitteeEngineError):
        _engine(devils_advocate=FakeAnalyst(CommitteeRole.COMMITTEE_CHAIR))


def test_non_analyst_instance_rejected() -> None:
    with pytest.raises(CommitteeEngineError):
        _engine(technical_analyst=object())


def test_non_chair_instance_rejected() -> None:
    with pytest.raises(CommitteeEngineError):
        _engine(chair=object())


def test_analyst_returning_wrong_role_rejected() -> None:
    def wrong_role_report(snapshot, generated_at):
        return make_report(
            CommitteeRole.RISK_MANAGER, AgentStance.NEUTRAL, Decimal("0.5"), asset=snapshot.asset, generated_at=generated_at
        )

    engine = _engine(technical_analyst=FakeAnalyst(CommitteeRole.TECHNICAL_ANALYST, wrong_role_report))

    with pytest.raises(CommitteeEngineError):
        engine.run(make_snapshot())


def test_analyst_returning_wrong_asset_rejected() -> None:
    other_asset = make_asset("MSFT")

    def wrong_asset_report(snapshot, generated_at):
        return make_report(
            CommitteeRole.TECHNICAL_ANALYST, AgentStance.NEUTRAL, Decimal("0.5"), asset=other_asset, generated_at=generated_at
        )

    engine = _engine(technical_analyst=FakeAnalyst(CommitteeRole.TECHNICAL_ANALYST, wrong_asset_report))

    with pytest.raises(CommitteeEngineError):
        engine.run(make_snapshot(asset=make_asset("AAPL")))


def test_malformed_report_raises_value_error_from_agent_report() -> None:
    def blank_summary_report(snapshot, generated_at):
        from investment_committee.committee.models import AgentReport

        return AgentReport(
            asset=snapshot.asset,
            role=CommitteeRole.TECHNICAL_ANALYST,
            stance=AgentStance.NEUTRAL,
            confidence=Decimal("0.5"),
            summary="   ",  # blank -> AgentReport.__post_init__ raises ValueError
            reasoning="reasoning",
            evidence=(),
            generated_at=generated_at,
        )

    engine = _engine(technical_analyst=FakeAnalyst(CommitteeRole.TECHNICAL_ANALYST, blank_summary_report))

    with pytest.raises(ValueError):
        engine.run(make_snapshot())


def test_no_network_access(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("DeterministicCommitteeEngine must not access the network")

    monkeypatch.setattr(socket.socket, "connect", _blocked)

    decision = _engine().run(make_snapshot())

    assert decision is not None
