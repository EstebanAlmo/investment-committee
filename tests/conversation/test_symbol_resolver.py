"""Tests for conversation.symbol_resolver. See spec-006 S13, S26."""

from __future__ import annotations

from investment_committee.conversation.symbol_resolver import StaticSymbolResolver
from investment_committee.conversation.factory import DEFAULT_NAME_TO_TICKER


def test_resolves_known_names_case_insensitively_and_trimmed() -> None:
    resolver = StaticSymbolResolver({"Apple": "AAPL", "nvidia": "NVDA"})

    assert resolver.resolve("apple") == "AAPL"
    assert resolver.resolve("  APPLE ") == "AAPL"
    assert resolver.resolve("Nvidia") == "NVDA"


def test_unknown_name_returns_none_not_an_exception() -> None:
    resolver = StaticSymbolResolver({"apple": "AAPL"})

    assert resolver.resolve("foo corp") is None
    assert resolver.resolve("") is None


def test_default_table_resolves_spec_examples() -> None:
    resolver = StaticSymbolResolver(DEFAULT_NAME_TO_TICKER)

    assert resolver.resolve("Apple") == "AAPL"
    assert resolver.resolve("nvidia") == "NVDA"
    assert resolver.resolve("tesla") == "TSLA"
    assert resolver.resolve("microsoft") == "MSFT"
    assert resolver.resolve("google") == "GOOGL"
    assert resolver.resolve("alphabet") == "GOOGL"
    assert resolver.resolve("eur usd") == "EURUSD=X"


def test_default_table_contains_no_crypto_or_commodity_entries() -> None:
    excluded_names = {"bitcoin", "btc", "ethereum", "eth", "gold", "silver", "oil", "crude"}
    assert excluded_names.isdisjoint(DEFAULT_NAME_TO_TICKER)
    for ticker in DEFAULT_NAME_TO_TICKER.values():
        assert not ticker.endswith(("-USD", "=F"))
        assert not ticker.startswith(("XAU", "XAG", "XPT", "XPD"))


# --- canonical_symbol: one canonical spelling per asset (EUR/USD -> EURUSD=X) ---

import pytest  # noqa: E402

from investment_committee.conversation.symbol_resolver import canonical_symbol  # noqa: E402


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("EUR/USD", "EURUSD=X"),
        ("eur/usd", "EURUSD=X"),
        (" EUR / USD ", "EURUSD=X"),
        ("EURUSD=X", "EURUSD=X"),
        ("eurusd=x", "EURUSD=X"),
        ("EURUSD", "EURUSD=X"),
        ("EUR/USD=X", "EURUSD=X"),
        ("GBP/JPY", "GBPJPY=X"),
        ("USD/COP", "USDCOP=X"),
        ("XAU/USD", "XAUUSD=X"),
    ],
)
def test_forex_spellings_normalize_to_the_yahoo_pair_form(text: str, expected: str) -> None:
    assert canonical_symbol(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("aapl", "AAPL"),
        (" SPY ", "SPY"),
        ("brk.b", "BRK.B"),
        ("BTC-USD", "BTC-USD"),
        ("GC=F", "GC=F"),
        ("ABCDEF", "ABCDEF"),  # six letters, but not two known currency codes
        ("", ""),
    ],
)
def test_non_forex_symbols_are_only_trimmed_and_uppercased(text: str, expected: str) -> None:
    assert canonical_symbol(text) == expected


def test_both_forex_spellings_share_one_canonical_symbol() -> None:
    assert canonical_symbol("EUR/USD") == canonical_symbol("EURUSD=X")
