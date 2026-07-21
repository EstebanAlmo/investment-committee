"""Sanity check that the investment_committee package is importable."""

import investment_committee


def test_package_has_version() -> None:
    assert isinstance(investment_committee.__version__, str)
    assert investment_committee.__version__ != ""
