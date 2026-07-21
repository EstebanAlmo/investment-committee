"""Framework-independent domain model shared by every layer.

Entities here have no dependency on external providers, storage, or
TradingAgents. See docs/architecture.md for how this layer fits in.
"""

from .assets import Asset, AssetType
from .market import MarketSnapshot, PriceBar
from .recommendations import Recommendation, RecommendationAction

__all__ = [
    "Asset",
    "AssetType",
    "MarketSnapshot",
    "PriceBar",
    "Recommendation",
    "RecommendationAction",
]
