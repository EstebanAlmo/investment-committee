"""Tests for investment_committee.conversation.tools's package-level exports.

See docs/specs/spec-006-conversational-interface.md S24, S26.
"""

from __future__ import annotations

import investment_committee.conversation.tools as tools


def test_public_exports_are_exactly_the_expected_set() -> None:
    expected = {
        "Tool",
        "ToolOutcome",
        "HelpContent",
        "NotYetAvailable",
        "PolicyDeclined",
        "ToolRouter",
        "AnalyzeAssetTool",
        "ExplainDecisionTool",
        "DecisionHistoryTool",
        "CompareAssetsTool",
        "PortfolioSummaryTool",
        "PerformanceSummaryTool",
        "HelpTool",
        "UnsupportedTradeRequestTool",
    }
    assert set(tools.__all__) == expected
    for name in expected:
        assert hasattr(tools, name)
