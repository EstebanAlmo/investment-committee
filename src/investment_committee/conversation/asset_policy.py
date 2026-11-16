"""Asset-class eligibility, decided from the ticker string itself.

See docs/specs/spec-006-conversational-interface.md S13. Only equities,
ETFs, and Forex currency pairs are supported; crypto, commodities
(including precious-metal "FX" codes like XAUUSD=X), options, and futures
are rejected before any application/ call. Deny-by-default: any shape not
positively recognized is UNSUPPORTED_*.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from enum import Enum


class AssetClassDecision(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED_CRYPTO = "UNSUPPORTED_CRYPTO"
    UNSUPPORTED_COMMODITY = "UNSUPPORTED_COMMODITY"
    UNSUPPORTED_DERIVATIVE = "UNSUPPORTED_DERIVATIVE"
    UNSUPPORTED_UNRECOGNIZED = "UNSUPPORTED_UNRECOGNIZED"


class AssetClassPolicy(ABC):
    @abstractmethod
    def classify(self, ticker: str) -> AssetClassDecision:
        """Never raises. Deny-by-default: any ticker shape not positively
        recognized as an equity/ETF or a genuine Forex pair is UNSUPPORTED_*.
        """
        raise NotImplementedError


_METAL_CODES = frozenset({"XAU", "XAG", "XPT", "XPD"})
_CRYPTO_SUFFIXES = ("-USD", "-USDT", "-EUR", "-GBP", "-BTC", "-ETH")
_FX_PATTERN = re.compile(r"^([A-Z]{3})([A-Z]{3})=X$")
_EQUITY_OR_ETF_PATTERN = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


class TickerPatternAssetClassPolicy(AssetClassPolicy):
    """Deterministic, standard-library ticker-shape classifier - no
    dependency on domain.AssetType (S13, "Why not AssetType.FOREX").
    """

    def classify(self, ticker: str) -> AssetClassDecision:
        symbol = ticker.strip().upper()
        if any(symbol.endswith(suffix) for suffix in _CRYPTO_SUFFIXES):
            return AssetClassDecision.UNSUPPORTED_CRYPTO
        if symbol.endswith("=F"):
            return AssetClassDecision.UNSUPPORTED_COMMODITY
        fx_match = _FX_PATTERN.match(symbol)
        if fx_match:
            base, quote = fx_match.group(1), fx_match.group(2)
            if base in _METAL_CODES or quote in _METAL_CODES:
                # XAU/XAG/XPT/XPD are ISO 4217 codes for precious metals, not
                # currencies - must not be classified as Forex just because
                # the ticker shape matches.
                return AssetClassDecision.UNSUPPORTED_COMMODITY
            return AssetClassDecision.SUPPORTED
        if _EQUITY_OR_ETF_PATTERN.match(symbol):
            return AssetClassDecision.SUPPORTED
        return AssetClassDecision.UNSUPPORTED_UNRECOGNIZED
