"""Access control: who may use the assistant at all.

See docs/specs/spec-006-conversational-interface.md S11.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import UserIdentity


class AccessPolicy(ABC):
    """Decides whether a UserIdentity may use the assistant at all."""

    @abstractmethod
    def is_authorized(self, user: UserIdentity) -> bool:
        """Never raises - an unrecognized or malformed identity is simply unauthorized."""
        raise NotImplementedError


class AllowlistAccessPolicy(AccessPolicy):
    """The only adapter this Epic ships: a fixed set of allowed user ids.

    Matches the project's stated scope (CLAUDE.md: "personal, local"
    system) - there is exactly one owner, not a multi-tenant user base.
    Where the allowed id(s) are read from (an environment variable, a local
    config file - never a committed secret) is a composition-root detail,
    deferred to implementation (S30), not decided by this module.
    """

    def __init__(self, allowed_user_ids: frozenset[str]) -> None:
        self._allowed_user_ids = allowed_user_ids

    def is_authorized(self, user: UserIdentity) -> bool:
        return user.user_id in self._allowed_user_ids
