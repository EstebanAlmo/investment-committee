"""Macro-economic and news context: the port a future news or economic
calendar provider implements.

No provider is integrated yet. UnavailableMacroProvider is the default and
returns nothing, so the macro analyst must report the information as not
available - never fill the gap with the model's general knowledge.
Anything a provider returns is EXTERNAL, UNTRUSTED text: it is data to be
analysed, never instructions to follow.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence


@dataclass(frozen=True)
class ExternalDocument:
    source: str  # provider / publisher name
    retrieved_at: datetime
    title: str
    text: str
    url: str | None = None


class MacroDataProvider(ABC):
    name: str = "unknown"

    @abstractmethod
    def documents(self, symbol: str, as_of: datetime) -> Sequence[ExternalDocument]:
        """News, rate decisions, inflation or employment releases relevant to
        `symbol`, retrieved by `as_of`. Empty when nothing is available."""


class UnavailableMacroProvider(MacroDataProvider):
    """Placeholder until a real news / economic-calendar source is added."""

    name = "no integrado"

    def documents(self, symbol: str, as_of: datetime) -> Sequence[ExternalDocument]:
        return ()
