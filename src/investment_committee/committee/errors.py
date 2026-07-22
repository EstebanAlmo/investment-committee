"""CommitteeEngineError: raised for committee orchestration/validation failures."""

from __future__ import annotations


class CommitteeEngineError(Exception):
    """Raised when committee orchestration input is structurally invalid.

    Covers analyst/chair wiring problems the engine's constructor and run()
    detect, and the chair's own pre-aggregation role-coverage check.
    Distinct from the plain ValueError every committee/models.py dataclass
    raises for its own shape invariants (spec-002).
    """
