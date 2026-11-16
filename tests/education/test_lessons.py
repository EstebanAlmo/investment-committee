"""Tests for education.lessons: concept lookup and grounded, Spanish lessons."""

from __future__ import annotations

from decimal import Decimal

import pytest

from investment_committee.education import (
    TRADE_LESSON_KEYS,
    LessonContext,
    build_lesson,
    concept_keys,
    concept_titles,
    find_concept,
)

REQUIRED_CONCEPTS = {
    "tendencia", "medias_moviles", "rsi", "soportes_resistencias", "volatilidad_atr", "spread_pips",
    "entrada_stop_take", "riesgo_beneficio", "tamano_posicion", "drawdown", "confianza", "inversion_trading_cfd",
}


def test_every_required_concept_exists_once() -> None:
    assert set(concept_keys()) == REQUIRED_CONCEPTS
    assert len(concept_titles()) == len(REQUIRED_CONCEPTS)
    assert set(TRADE_LESSON_KEYS) <= REQUIRED_CONCEPTS


@pytest.mark.parametrize(
    ("query", "key"),
    [
        ("RSI", "rsi"), ("sobrecompra", "rsi"), ("SMA200", "medias_moviles"), ("medias móviles", "medias_moviles"),
        ("stop-loss", "entrada_stop_take"), ("Take Profit", "entrada_stop_take"), ("riesgo/beneficio", "riesgo_beneficio"),
        ("pips", "spread_pips"), ("ATR", "volatilidad_atr"), ("apalancamiento", "tamano_posicion"),
        ("margen", "tamano_posicion"), ("drawdown", "drawdown"), ("CFD", "inversion_trading_cfd"),
        ("soportes y resistencias", "soportes_resistencias"), ("tendencia", "tendencia"), ("confianza", "confianza"),
    ],
)
def test_find_concept_accepts_natural_spellings(query: str, key: str) -> None:
    assert find_concept(query) == key


def test_unknown_concept_is_not_guessed() -> None:
    assert find_concept("astrologia") is None


@pytest.mark.parametrize("key", sorted(REQUIRED_CONCEPTS))
def test_general_lesson_has_all_sections_and_no_invented_value(key: str) -> None:
    lesson = build_lesson(key)

    for section in (lesson.title, lesson.what_it_is, lesson.how_to_read, lesson.example, lesson.limitations):
        assert section.strip()
    assert lesson.current_value is None and lesson.for_this_asset is None


def _context(**values: Decimal | str) -> LessonContext:
    return LessonContext(asset_label="EUR/USD", source="una prueba", values=values)


@pytest.mark.parametrize(
    ("rsi", "fragment"),
    [("75", "sobrecompra"), ("25", "sobreventa"), ("60", "impulso reciente es comprador"), ("40", "impulso reciente es vendedor")],
)
def test_rsi_reading_follows_the_usual_thresholds(rsi: str, fragment: str) -> None:
    lesson = build_lesson("rsi", _context(rsi14=Decimal(rsi)))

    assert lesson.current_value == f"RSI14 = {rsi},0"
    assert lesson.for_this_asset is not None and fragment in lesson.for_this_asset


def test_missing_values_are_reported_as_not_available() -> None:
    lesson = build_lesson("rsi", _context(sma20=Decimal("1.1")))

    assert lesson.current_value is None
    assert lesson.context_source == "una prueba"


def test_reward_risk_lesson_derives_the_breakeven_hit_rate() -> None:
    lesson = build_lesson("riesgo_beneficio", _context(gross_reward_risk=Decimal("2"), net_reward_risk=Decimal("2")))

    assert lesson.for_this_asset is not None and "alrededor de 33 %" in lesson.for_this_asset


def test_no_trade_levels_lesson_says_there_are_no_levels() -> None:
    lesson = build_lesson("entrada_stop_take", _context(direction="NO_TRADE"))

    assert lesson.current_value is not None and lesson.current_value.startswith("dirección NO OPERAR")


def test_committee_drawdown_is_shown_as_a_percentage() -> None:
    lesson = build_lesson("drawdown", _context(max_drawdown=Decimal("-0.12")))

    assert lesson.current_value == "caída máxima -12,00 %"


def test_confidence_lesson_warns_it_is_not_a_probability() -> None:
    lesson = build_lesson("confianza", _context(committee_confidence=Decimal("0.90")))

    assert lesson.current_value == "confianza interna 0,90 (90 %)"
    assert "No es la probabilidad de ganar" in (lesson.for_this_asset or "")
    assert "no significa un 90 % de probabilidad" in lesson.limitations
