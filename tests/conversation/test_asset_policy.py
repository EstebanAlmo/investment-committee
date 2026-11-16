"""Tests for conversation.asset_policy. See spec-006 S13, S26."""

from __future__ import annotations

import pytest

from investment_committee.conversation.asset_policy import (
    AssetClassDecision,
    TickerPatternAssetClassPolicy,
)


@pytest.mark.parametrize(
    ("ticker", "expected"),
    [
        ("AAPL", AssetClassDecision.SUPPORTED),
        ("aapl", AssetClassDecision.SUPPORTED),
        ("BRK.B", AssetClassDecision.SUPPORTED),
        ("SPY", AssetClassDecision.SUPPORTED),
        ("EURUSD=X", AssetClassDecision.SUPPORTED),
        ("GBPJPY=X", AssetClassDecision.SUPPORTED),
        ("XAUUSD=X", AssetClassDecision.UNSUPPORTED_COMMODITY),
        ("XAGUSD=X", AssetClassDecision.UNSUPPORTED_COMMODITY),
        ("BTC-USD", AssetClassDecision.UNSUPPORTED_CRYPTO),
        ("ETH-USD", AssetClassDecision.UNSUPPORTED_CRYPTO),
        ("GC=F", AssetClassDecision.UNSUPPORTED_COMMODITY),
        ("CL=F", AssetClassDecision.UNSUPPORTED_COMMODITY),
        ("AAPL240119C00150000", AssetClassDecision.UNSUPPORTED_UNRECOGNIZED),
        ("^GSPC", AssetClassDecision.UNSUPPORTED_UNRECOGNIZED),
        ("", AssetClassDecision.UNSUPPORTED_UNRECOGNIZED),
        ("not a ticker", AssetClassDecision.UNSUPPORTED_UNRECOGNIZED),
    ],
)
def test_classify(ticker: str, expected: AssetClassDecision) -> None:
    assert TickerPatternAssetClassPolicy().classify(ticker) is expected
