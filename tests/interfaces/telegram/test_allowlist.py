from __future__ import annotations

from investment_committee.interfaces.telegram.allowlist import is_authorized


def test_is_authorized_true_for_member() -> None:
    assert is_authorized(111, frozenset({111, 222})) is True


def test_is_authorized_false_for_non_member() -> None:
    assert is_authorized(999, frozenset({111, 222})) is False


def test_is_authorized_false_for_empty_allowlist() -> None:
    assert is_authorized(111, frozenset()) is False
