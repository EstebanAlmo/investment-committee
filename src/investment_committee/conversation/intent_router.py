"""IntentRouter port and its deterministic baseline, RuleBasedIntentRouter.

See docs/specs/spec-006-conversational-interface.md S12. LLMIntentRouter and
FallbackIntentRouter (the optional LLM path) are not built - the
deterministic Telegram MVP runs on RuleBasedIntentRouter alone, with no
network access and no LLM.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

from .models import ConversationSession, Intent, IntentType
from .symbol_resolver import SymbolResolver, canonical_symbol


class IntentRouter(ABC):
    """Turns free-form text (never an explicit "/command" - CommandParser
    already handled that, S9) into a structured Intent. Never raises - an
    unrecognized message resolves to Intent(type=UNKNOWN, ...), not an
    exception. `session` is read-only context (e.g. session.last_asset for
    a follow-up like "explain that") - IntentRouter never mutates it.
    """

    @abstractmethod
    def route(self, text: str, session: ConversationSession) -> Intent:
        raise NotImplementedError


# \w (not [A-Za-z]) so Spanish words keep their accents ("Muéstrame");
# "/" so a Forex pair typed as "EUR/USD" stays one token.
_TOKEN_PATTERN = re.compile(r"[\w.\-=^/]+")
_TICKER_PATTERN = re.compile(r"^[A-Z0-9.\-=^/]{1,12}$")

# English and Spanish keywords side by side: es-CO is the default interface
# language, and English phrasing keeps working.
_TRADE_VERBS = frozenset({"buy", "sell", "purchase", "short", "compra", "comprar", "vende", "vender"})
_TRADE_ORDER_PREFIXES = ("place an order", "pon una orden", "coloca una orden", "haz una orden")
_HEDGES = (
    "should", "would", "do you think", "could", "?",
    "debería", "deberia", "crees", "podría", "podria", "conviene", "¿",
)

_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "uno": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5",
    "seis": "6", "siete": "7", "ocho": "8", "nueve": "9", "diez": "10",
}

# Words that are never a symbol, even when typed in capitals ("Why was Apple
# a BUY?", "Should I ..."). Checked case-insensitively before any token is
# treated as a literal ticker.
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "i", "me", "my", "we", "us", "our", "you", "your", "it", "its",
        "that", "this", "those", "these", "there", "is", "are", "was", "were", "be", "been",
        "do", "does", "did", "done", "doing", "have", "has", "had", "should", "would", "could",
        "can", "will", "what", "why", "how", "who", "which", "when", "about", "for", "of", "on",
        "in", "to", "and", "or", "with", "now", "please", "pls", "think", "committee",
        "analyze", "analyse", "analysis", "analyses", "explain", "history", "recommended",
        "recommend", "recommendation", "recommendations", "last", "show", "list", "compare",
        "versus", "vs", "buy", "sell", "hold", "purchase", "short", "place", "order", "trade",
        "stock", "stocks", "share", "shares", "decision", "decisions", "many", "much", "tell",
        "give", "check", "look", "at", "opinion", "hello", "hi", "hey", "there", "thanks",
        "say", "says", "said", "get", "let", "lets", "let's", "go", "up", "so", "if", "not",
        # Spanish
        "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "al", "y", "o", "en",
        "que", "qué", "por", "porque", "para", "con", "sin", "mi", "mis", "me", "yo", "tu", "tus",
        "eso", "esto", "ese", "esa", "es", "son", "fue", "fueron", "quedó", "quedo", "ha", "han",
        "sido", "va", "cómo", "como", "cuál", "cual", "cuáles", "cuales", "cuántas", "cuantas",
        "cuántos", "cuantos", "sobre", "ahora", "ya", "hola", "gracias", "favor", "puedes", "hacer",
        "analiza", "analizar", "analices", "análisis", "analisis", "explica", "explicar", "explícame",
        "explicame", "historial", "recomendado", "recomendaste", "recomendamos", "compara", "comparar",
        "contra", "ayuda", "compra", "comprar", "vende", "vender", "mantener", "debería", "deberia",
        "crees", "opinas", "piensas", "muestra", "muéstrame", "muestrame", "últimas", "ultimas",
        "último", "ultimo", "última", "ultima", "últimos", "ultimos", "decisión", "decision",
        "decisiones", "acción", "accion", "acciones", "orden", "revisa", "mira", "portafolio",
        "cartera", "rendimiento", "desempeño", "comité", "comite", "dime", "dame",
    }
    | set(_NUMBER_WORDS)
)

_HISTORY_WORDS = frozenset(
    {"history", "recommended", "last", "historial", "recomendado", "recomendaste", "recomendamos",
     "últimas", "ultimas", "último", "ultimo", "última", "ultima", "últimos", "ultimos"}
)
_EXPLAIN_WORDS = frozenset({"why", "explain", "explica", "explicar", "explícame", "explicame"})

_ANALYZE_HINTS = ("analy", "think about", "opinion", "look at", "check", "analiz", "análisis", "opinas", "revisa")
_HELP_PHRASES = ("what can you do", "puedes hacer")
_EXPLAIN_PHRASES = ("por qué", "por que", "porqué")


class RuleBasedIntentRouter(IntentRouter):
    """Deterministic, standard-library baseline (S12): keyword patterns per
    intent, an explicit imperative-vs-interrogative rule for trade
    language, and symbol resolution via SymbolResolver. Anything matching
    no pattern but naming a resolvable symbol defaults to ANALYZE_ASSET.
    """

    def __init__(self, symbol_resolver: SymbolResolver) -> None:
        self._symbol_resolver = symbol_resolver

    def route(self, text: str, session: ConversationSession) -> Intent:
        raw_text = text if text.strip() else "(empty message)"
        lowered = " ".join(text.lower().split())
        words = [token.lower() for token in _TOKEN_PATTERN.findall(text)]

        if self._is_trade_imperative(lowered, words):
            return self._intent(IntentType.UNSUPPORTED_TRADE_REQUEST, self._symbol_parameter(text), raw_text)
        if {"help", "ayuda"} & set(words) or any(phrase in lowered for phrase in _HELP_PHRASES):
            return self._intent(IntentType.HELP, {}, raw_text)
        if {"compare", "vs", "versus", "compara", "comparar"} & set(words):
            symbols = self._symbols(text)
            if len(symbols) >= 2:
                return self._intent(IntentType.COMPARE_ASSETS, {"symbols": ",".join(symbols[:2])}, raw_text)
        if {"portfolio", "portafolio", "cartera"} & set(words):
            return self._intent(IntentType.PORTFOLIO_SUMMARY, {}, raw_text)
        if {"performance", "rendimiento", "desempeño"} & set(words) or "how did we do" in lowered:
            return self._intent(IntentType.PERFORMANCE_SUMMARY, {}, raw_text)
        if _HISTORY_WORDS & set(words):
            parameters = self._symbol_parameter(text)
            limit = self._limit(words)
            if limit is not None:
                parameters["limit"] = limit
            return self._intent(IntentType.DECISION_HISTORY, parameters, raw_text)
        if _EXPLAIN_WORDS & set(words) or any(phrase in lowered for phrase in _EXPLAIN_PHRASES):
            return self._intent(IntentType.EXPLAIN_DECISION, self._symbol_parameter(text), raw_text)

        symbol_parameter = self._symbol_parameter(text)
        if symbol_parameter:
            return self._intent(IntentType.ANALYZE_ASSET, symbol_parameter, raw_text)
        if any(hint in lowered for hint in _ANALYZE_HINTS):
            name = " ".join(word for word in words if word not in _STOPWORDS) or "eso"
            return self._intent(
                IntentType.UNKNOWN,
                {
                    "clarification": f"No reconozco '{name}'. Prueba con su símbolo (ticker), por ejemplo "
                    "AAPL, o con un par Forex como EUR/USD."
                },
                raw_text,
            )
        return self._intent(IntentType.UNKNOWN, {}, raw_text)

    @staticmethod
    def _intent(intent_type: IntentType, parameters: dict[str, str], raw_text: str) -> Intent:
        return Intent(type=intent_type, parameters=parameters, raw_text=raw_text)

    @staticmethod
    def _is_trade_imperative(lowered: str, words: list[str]) -> bool:
        """A bare imperative opening with an execution verb and no hedging
        ("Buy Bitcoin.") - never "Should I buy Nvidia?" (S12, step 2)."""
        if any(hedge in lowered for hedge in _HEDGES):
            return False
        return bool(words) and (words[0] in _TRADE_VERBS or lowered.startswith(_TRADE_ORDER_PREFIXES))

    def _symbol_parameter(self, text: str) -> dict[str, str]:
        symbols = self._symbols(text)
        return {"symbol": symbols[0]} if symbols else {}

    def _symbols(self, text: str) -> list[str]:
        """Resolvable names (two-word names first) or tokens typed as an
        uppercase ticker, in order of appearance. Never guesses: a lowercase
        word the resolver doesn't know is not treated as a ticker."""
        tokens = _TOKEN_PATTERN.findall(text)
        symbols: list[str] = []
        index = 0
        while index < len(tokens):
            if index + 1 < len(tokens):
                pair = self._symbol_resolver.resolve(f"{tokens[index]} {tokens[index + 1]}")
                if pair is not None:
                    symbols.append(pair)
                    index += 2
                    continue
            token = tokens[index].strip(".")
            index += 1
            if not token or token.lower() in _STOPWORDS:
                continue
            resolved = self._symbol_resolver.resolve(token)
            if resolved is not None:
                symbols.append(resolved)
            elif token.isupper() and _TICKER_PATTERN.match(token):
                symbols.append(canonical_symbol(token))
            elif "/" in token and canonical_symbol(token).endswith("=X"):
                symbols.append(canonical_symbol(token))  # "eur/usd": a slash pair is unambiguous
        return list(dict.fromkeys(symbols))

    @staticmethod
    def _limit(words: list[str]) -> str | None:
        for word in words:
            if word.isdigit() and 0 < int(word) <= 100:
                return str(int(word))
            if word in _NUMBER_WORDS:
                return _NUMBER_WORDS[word]
        return None
