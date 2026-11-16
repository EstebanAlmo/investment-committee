"""LedgerError hierarchy: typed failures for DecisionLedger operations and serialization.

Mirrors the existing per-layer error hierarchy pattern (MarketDataError,
IndicatorCalculationError, CommitteeEngineError): one base class, narrow
named subclasses per distinguishable failure mode. See
docs/specs/spec-005-decision-ledger.md S14.
"""

from __future__ import annotations


class LedgerError(Exception):
    """Base class for every error a DecisionLedger operation can raise."""


class DuplicateDecisionError(LedgerError):
    """Raised by append() when a record with the same record_id already exists.

    Also raised by JsonLinesDecisionLedger's constructor if the ledger file
    itself already contains two lines with the same record_id.
    """


class DecisionNotFoundError(LedgerError):
    """Raised by get() when no record with the given record_id exists."""


class LedgerWriteError(LedgerError):
    """Raised when append() fails for a reason other than duplication (e.g. an I/O failure)."""


class LedgerReadError(LedgerError):
    """Raised when get()/find_by_asset()/list_all() fail for a reason other than not-found."""


class DecisionRecordDeserializationError(LedgerReadError):
    """Raised when a persisted record cannot be reconstructed.

    Covers malformed JSON, a missing or unrecognized required field
    (including an EvidenceItem's value_type), an unrecognized
    schema_version, or a value that fails a domain dataclass's own
    __post_init__ validation on read. Also raised by
    JsonLinesDecisionLedger's constructor during eager validation.
    """
