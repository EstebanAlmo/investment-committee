"""Analyst: the interface every committee seat implements.

See docs/specs/spec-003-deterministic-committee-engine.md, section 1.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import ClassVar

from investment_committee.indicators import IndicatorSnapshot

from ..models import AgentReport, CommitteeRole


class Analyst(ABC):
    """One committee seat that turns an IndicatorSnapshot into an AgentReport."""

    role: ClassVar[CommitteeRole]

    @abstractmethod
    def analyze(self, snapshot: IndicatorSnapshot, generated_at: datetime) -> AgentReport:
        """Return this analyst's AgentReport for `snapshot`.

        `generated_at` is supplied by the caller (the engine), not read from
        a clock inside the analyst. The returned AgentReport.role must equal
        this analyst's `role` class attribute and AgentReport.asset must
        equal `snapshot.asset`; the engine verifies both after calling this
        method.
        """
        raise NotImplementedError
