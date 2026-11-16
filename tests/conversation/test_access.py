"""Tests for investment_committee.conversation.access.

See docs/specs/spec-006-conversational-interface.md S11, S26.
"""

from __future__ import annotations

import pytest

from investment_committee.conversation.access import AccessPolicy, AllowlistAccessPolicy
from investment_committee.conversation.models import UserIdentity


def _user(user_id: str) -> UserIdentity:
    return UserIdentity(user_id=user_id, display_name=None)


class TestAllowlistAccessPolicy:
    def test_accepts_exactly_the_configured_ids(self) -> None:
        policy = AllowlistAccessPolicy(frozenset({"111", "222"}))

        assert policy.is_authorized(_user("111")) is True
        assert policy.is_authorized(_user("222")) is True

    def test_rejects_an_unrecognized_id(self) -> None:
        policy = AllowlistAccessPolicy(frozenset({"111"}))

        assert policy.is_authorized(_user("999")) is False

    def test_rejects_everything_for_an_empty_allowlist(self) -> None:
        policy = AllowlistAccessPolicy(frozenset())

        assert policy.is_authorized(_user("111")) is False


def test_access_policy_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        AccessPolicy()  # type: ignore[abstract]
