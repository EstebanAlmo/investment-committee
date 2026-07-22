"""Tests for investment_committee.application.errors."""

from __future__ import annotations

import pytest

from investment_committee.application.errors import (
    ApplicationServiceError,
    CommitteeDecisionFailedError,
    IndicatorCalculationFailedError,
    InvalidTickerError,
    MarketDataUnavailableError,
)

_SUBCLASSES = (
    InvalidTickerError,
    MarketDataUnavailableError,
    IndicatorCalculationFailedError,
    CommitteeDecisionFailedError,
)


@pytest.mark.parametrize("error_class", _SUBCLASSES)
def test_is_subclass_of_application_service_error(error_class: type[Exception]) -> None:
    assert issubclass(error_class, ApplicationServiceError)
    assert issubclass(error_class, Exception)


@pytest.mark.parametrize("error_class", (*_SUBCLASSES, ApplicationServiceError))
def test_message_is_preserved(error_class: type[Exception]) -> None:
    exc = error_class("something went wrong")
    assert str(exc) == "something went wrong"


def test_hierarchy_is_flat_one_level_below_base() -> None:
    for error_class in _SUBCLASSES:
        assert error_class.__bases__ == (ApplicationServiceError,)
