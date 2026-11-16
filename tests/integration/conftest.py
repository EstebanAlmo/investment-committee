"""Network guard for integration tests: no real API calls, ever.

Python-level sockets may only connect to loopback (asyncio's Windows event
loop uses a localhost socket pair internally). yfinance bypasses Python
sockets (curl_cffi), so the real provider is additionally made to fail
loudly if anything ever constructs and calls it.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest

from investment_committee.market_intelligence.providers.yfinance_provider import YFinanceProvider

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    original_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOOPBACK:
            raise AssertionError(f"integration test attempted a real network connection to {host!r}")
        return original_connect(self, address)

    def forbidden_fetch(self: YFinanceProvider, ticker: str) -> Any:
        raise AssertionError(f"integration test attempted a real yfinance fetch for {ticker!r}")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(YFinanceProvider, "get_daily_history", forbidden_fetch)
