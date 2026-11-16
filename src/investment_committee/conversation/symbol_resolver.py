"""Symbol resolution: free-form company names to ticker symbols.

See docs/specs/spec-006-conversational-interface.md S13. The production
name table (DEFAULT_NAME_TO_TICKER) lives in conversation/factory.py, next
to its one production use (S25).
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Mapping


class SymbolResolver(ABC):
    """Best-effort resolution of a free-form name to a ticker symbol."""

    @abstractmethod
    def resolve(self, text: str) -> str | None:
        """Return a ticker symbol, or None if `text` cannot be resolved.

        Never raises. A None result is not an error - the caller falls back
        to treating `text` itself as a literal ticker attempt.
        """
        raise NotImplementedError


class StaticSymbolResolver(SymbolResolver):
    """A small, hand-maintained name -> ticker table, restricted to
    supported asset classes. Case-insensitive, whitespace-trimmed lookup
    only; no fuzzy matching. Deliberately excludes crypto/commodity names
    (S13): excluded-class names simply fail to resolve.
    """

    def __init__(self, name_to_ticker: Mapping[str, str]) -> None:
        self._name_to_ticker = {k.strip().lower(): v for k, v in name_to_ticker.items()}

    def resolve(self, text: str) -> str | None:
        return self._name_to_ticker.get(text.strip().lower())


# ISO 4217 codes recognized for a bare six-letter pair ("EURUSD"). The
# precious-metal codes are included on purpose so "XAUUSD" normalizes to
# "XAUUSD=X" and AssetClassPolicy then rejects it as a commodity, rather
# than it being reported as an unrecognized ticker.
_CURRENCY_CODES = frozenset(
    {
        "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "SEK", "NOK", "DKK",
        "CNY", "HKD", "SGD", "INR", "KRW", "MXN", "BRL", "COP", "CLP", "PEN", "ARS",
        "ZAR", "TRY", "PLN", "CZK", "HUF", "ILS", "THB",
        "XAU", "XAG", "XPT", "XPD",
    }
)
_SLASH_PAIR_PATTERN = re.compile(r"^([A-Z]{3})\s*/\s*([A-Z]{3})(?:=X)?$")
_BARE_PAIR_PATTERN = re.compile(r"^([A-Z]{3})([A-Z]{3})$")


def canonical_symbol(text: str) -> str:
    """The one canonical spelling of a user-typed symbol, so every layer
    (asset-class policy, market data, ledger, history lookups) sees the same
    ticker for the same asset. Forex pairs use the "EURUSD=X" convention
    market_intelligence already passes to the provider (S13): "EUR/USD",
    "eurusd=x", and "EURUSD" all become "EURUSD=X". Anything else is only
    trimmed and uppercased. Never raises.
    """
    symbol = text.strip().upper()
    slash_pair = _SLASH_PAIR_PATTERN.match(symbol)
    if slash_pair:
        return f"{slash_pair.group(1)}{slash_pair.group(2)}=X"
    bare_pair = _BARE_PAIR_PATTERN.match(symbol)
    if bare_pair and bare_pair.group(1) in _CURRENCY_CODES and bare_pair.group(2) in _CURRENCY_CODES:
        return f"{symbol}=X"
    return symbol
