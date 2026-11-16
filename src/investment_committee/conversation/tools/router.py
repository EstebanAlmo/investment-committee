"""ToolRouter: deterministic IntentType -> Tool dispatch. See spec-006 S14."""

from __future__ import annotations

from typing import Mapping

from ..models import IntentType
from .base import Tool


class ToolRouter:
    """Deterministic IntentType -> Tool dispatch table. Not an ABC - there is
    exactly one sane way to map a fixed enum to a fixed set of tools.
    """

    def __init__(self, tools_by_intent: Mapping[IntentType, Tool]) -> None:
        missing = set(IntentType) - set(tools_by_intent)
        if missing:
            raise ValueError(f"ToolRouter is missing a tool for: {sorted(m.value for m in missing)}")
        self._tools_by_intent = dict(tools_by_intent)

    def tool_for(self, intent_type: IntentType) -> Tool:
        return self._tools_by_intent[intent_type]
