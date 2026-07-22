"""DeterministicCommitteeEngine: orchestrates the three analysts and the chair.

See docs/specs/spec-003-deterministic-committee-engine.md, section 6.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from investment_committee.indicators import IndicatorSnapshot

from .analysts.base import Analyst
from .chair import DeterministicCommitteeChair
from .errors import CommitteeEngineError
from .models import CommitteeRole, InvestmentDecision

Clock = Callable[[], datetime]


class DeterministicCommitteeEngine:
    """Runs the three analysts and the chair over one IndicatorSnapshot."""

    def __init__(
        self,
        technical_analyst: Analyst,
        risk_manager: Analyst,
        devils_advocate: Analyst,
        chair: DeterministicCommitteeChair,
        clock: Clock = lambda: datetime.now(timezone.utc),
    ) -> None:
        _validate_analyst(technical_analyst, CommitteeRole.TECHNICAL_ANALYST, "technical_analyst")
        _validate_analyst(risk_manager, CommitteeRole.RISK_MANAGER, "risk_manager")
        _validate_analyst(devils_advocate, CommitteeRole.DEVILS_ADVOCATE, "devils_advocate")
        if not isinstance(chair, DeterministicCommitteeChair):
            raise CommitteeEngineError("chair must be a DeterministicCommitteeChair instance")

        self._technical_analyst = technical_analyst
        self._risk_manager = risk_manager
        self._devils_advocate = devils_advocate
        self._chair = chair
        self._clock = clock

    def run(self, snapshot: IndicatorSnapshot) -> InvestmentDecision:
        generated_at = self._clock()
        analysts = (self._technical_analyst, self._risk_manager, self._devils_advocate)
        reports = [analyst.analyze(snapshot, generated_at) for analyst in analysts]

        for analyst, report in zip(analysts, reports):
            if report.role != analyst.role:
                raise CommitteeEngineError(
                    f"{type(analyst).__name__} returned an AgentReport with role "
                    f"{report.role}, expected {analyst.role}"
                )
            if report.asset != snapshot.asset:
                raise CommitteeEngineError(
                    f"{type(analyst).__name__} returned an AgentReport for a different asset "
                    "than the snapshot"
                )

        return self._chair.decide(asset=snapshot.asset, reports=reports, generated_at=generated_at)


def _validate_analyst(analyst: Analyst, expected_role: CommitteeRole, parameter_name: str) -> None:
    if not isinstance(analyst, Analyst):
        raise CommitteeEngineError(f"{parameter_name} must be an Analyst instance")
    if analyst.role != expected_role:
        raise CommitteeEngineError(
            f"{parameter_name} must declare role {expected_role}, got {analyst.role}"
        )
