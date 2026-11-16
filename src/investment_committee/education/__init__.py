"""Beginner-oriented explanations in Colombian Spanish (es-CO).

Turns already-computed analysis values into lessons: what a concept is, its
current value, how it is read, what it means for the asset, and its limits.
Never fetches or computes market data itself (docs/architecture.md:
education/ is composed by application/).
"""

from __future__ import annotations

from .lessons import (
    TRADE_LESSON_KEYS,
    Lesson,
    LessonContext,
    build_lesson,
    concept_keys,
    concept_titles,
    find_concept,
)

__all__ = [
    "TRADE_LESSON_KEYS",
    "Lesson",
    "LessonContext",
    "build_lesson",
    "concept_keys",
    "concept_titles",
    "find_concept",
]
