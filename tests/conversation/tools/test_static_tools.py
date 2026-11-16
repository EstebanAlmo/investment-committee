"""Tests for the collaborator-free tools and ToolRouter. See spec-006 S14, S15.5-S15.8, S26."""

from __future__ import annotations

import pytest

from investment_committee.conversation.models import IntentType
from investment_committee.conversation.tools import (
    HelpContent,
    HelpTool,
    NotYetAvailable,
    PerformanceSummaryTool,
    PolicyDeclined,
    PortfolioSummaryTool,
    ToolRouter,
    UnsupportedTradeRequestTool,
)

from ._support import make_intent, make_session


def test_help_lists_commands() -> None:
    outcome = HelpTool().execute(make_intent(IntentType.HELP), make_session())

    assert isinstance(outcome, HelpContent)
    joined = "\n".join(outcome.available_intents)
    for command in ("/analyze", "/history", "/explain", "/compare", "/help"):
        assert command in joined
    assert "nunca ejecuto operaciones" in outcome.description


@pytest.mark.parametrize(
    ("tool", "feature"),
    [(PortfolioSummaryTool(), "El portafolio"), (PerformanceSummaryTool(), "La evaluación de rendimiento")],
)
def test_not_yet_available_tools(tool: object, feature: str) -> None:
    outcome = tool.execute(make_intent(IntentType.PORTFOLIO_SUMMARY), make_session())  # type: ignore[attr-defined]

    assert isinstance(outcome, NotYetAvailable)
    assert outcome.feature_name == feature


def test_unsupported_trade_is_declined_and_offers_analysis() -> None:
    outcome = UnsupportedTradeRequestTool().execute(
        make_intent(IntentType.UNSUPPORTED_TRADE_REQUEST, symbol="AAPL"), make_session()
    )

    assert isinstance(outcome, PolicyDeclined)
    assert outcome.reason == "no_broker_integration"
    assert "AAPL" in outcome.message


def _all_tools() -> dict[IntentType, object]:
    help_tool = HelpTool()
    tools: dict[IntentType, object] = {intent_type: help_tool for intent_type in IntentType}
    return tools


def test_tool_router_requires_every_intent_type() -> None:
    tools = _all_tools()
    del tools[IntentType.UNKNOWN]

    with pytest.raises(ValueError, match="UNKNOWN"):
        ToolRouter(tools)  # type: ignore[arg-type]


def test_tool_router_returns_registered_tool() -> None:
    tools = _all_tools()
    trade_tool = UnsupportedTradeRequestTool()
    tools[IntentType.UNSUPPORTED_TRADE_REQUEST] = trade_tool
    router = ToolRouter(tools)  # type: ignore[arg-type]

    assert router.tool_for(IntentType.UNSUPPORTED_TRADE_REQUEST) is trade_tool
    assert router.tool_for(IntentType.HELP) is router.tool_for(IntentType.UNKNOWN)
