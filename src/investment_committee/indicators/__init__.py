"""Indicators: deterministic technical and risk indicator calculations.

The IndicatorEngine turns a MarketSnapshot into an IndicatorSnapshot using
only Python's standard library. It performs no I/O, calls no external
services or LLMs, and produces no investment recommendations.
"""

from .engine import IndicatorCalculationError, IndicatorEngine
from .models import IndicatorSnapshot

__all__ = [
    "IndicatorCalculationError",
    "IndicatorEngine",
    "IndicatorSnapshot",
]
