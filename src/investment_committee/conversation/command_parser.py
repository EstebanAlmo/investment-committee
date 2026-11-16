"""CommandParser: deterministic parsing of explicit slash commands.

See docs/specs/spec-006-conversational-interface.md S12. Standard library
only - never invokes an LLM, IntentRouter, application/, or any network or
Telegram dependency. `parse` never raises: an unrecognized command still
returns a valid Intent(type=UNKNOWN, ...), never None, and a missing
argument resolves to an Intent with no "symbol"/"symbols" parameter rather
than raising - Intent itself validates nothing about parameter meaning
(S10); a downstream Tool (a later phase) is responsible for handling its
own missing expected keys.
"""

from __future__ import annotations

import re

from .models import Intent, IntentType
from .symbol_resolver import canonical_symbol

_COMMAND_INTENTS: dict[str, IntentType] = {
    "start": IntentType.HELP,
    "help": IntentType.HELP,
    "analyze": IntentType.ANALYZE_ASSET,
    "history": IntentType.DECISION_HISTORY,
    "explain": IntentType.EXPLAIN_DECISION,
    "compare": IntentType.COMPARE_ASSETS,
    "portfolio": IntentType.PORTFOLIO_SUMMARY,
    "performance": IntentType.PERFORMANCE_SUMMARY,
    # Simulated trading and lessons (Spanish command names).
    "trade": IntentType.TRADE_SIGNAL,
    "explicar_trade": IntentType.EXPLAIN_TRADE,
    "backtest": IntentType.BACKTEST,
    "operaciones": IntentType.SIMULATED_TRADES,
    "aprender": IntentType.LEARN_CONCEPT,
    "committee": IntentType.AI_COMMITTEE,
    "comite": IntentType.AI_COMMITTEE,
    "oportunidades": IntentType.OPPORTUNITIES,
    "opportunities": IntentType.OPPORTUNITIES,
    "simular_mes": IntentType.REPLAY_MONTH,
    "seguimiento": IntentType.OPPORTUNITY_TRACKER,
    # "/portfolio" keeps its old meaning (summary, coming soon); "/portafolio" is the guardian.
    "portafolio": IntentType.PORTFOLIO_GUARDIAN,
    "alertas": IntentType.PORTFOLIO_ALERTS,
}

_SYMBOL_SPLIT_PATTERN = re.compile(r"[,\s]+")


class CommandParser:
    """Deterministic parser for explicit bot commands (/help, /analyze AAPL,
    /history AAPL, ...). Symbol resolution for a command's argument is
    literal (the argument is used as a ticker directly, in its canonical
    spelling - canonical_symbol, so "EUR/USD" becomes "EURUSD=X") -
    SymbolResolver's name lookup (S13) is an IntentRouter-only convenience
    for free-form text, not extended to commands, since a command's
    argument is expected to already be a ticker.
    """

    def parse(self, text: str) -> Intent | None:
        """Returns None if `text` does not start with "/" - the caller
        (ConversationService, a later phase) falls through to IntentRouter
        only in that case. Text that *does* start with "/" but names an
        unrecognized command still returns an Intent (UNKNOWN) - it is
        never silently handed to IntentRouter or the LLM.
        """
        if not text.startswith("/"):
            return None

        parts = text[1:].split(maxsplit=1)
        command = parts[0].lower() if parts else ""
        argument = parts[1].strip() if len(parts) > 1 else ""

        intent_type = _COMMAND_INTENTS.get(command)
        if intent_type is None:
            return Intent(type=IntentType.UNKNOWN, parameters={}, raw_text=text)

        if intent_type is IntentType.COMPARE_ASSETS:
            parameters = self._compare_parameters(argument)
        elif intent_type in (
            IntentType.OPPORTUNITIES, IntentType.REPLAY_MONTH, IntentType.OPPORTUNITY_TRACKER,
            IntentType.PORTFOLIO_GUARDIAN, IntentType.PORTFOLIO_ALERTS,
        ):
            # Scans the configured universe: an argument is not a symbol filter.
            parameters = {}
        elif intent_type is IntentType.LEARN_CONCEPT:
            # A concept name ("RSI", "stop-loss"), not a ticker: kept as typed.
            parameters = {"concept": argument} if argument else {}
        else:
            parameters = {"symbol": canonical_symbol(argument)} if argument else {}
        return Intent(type=intent_type, parameters=parameters, raw_text=text)

    @staticmethod
    def _compare_parameters(argument: str) -> dict[str, str]:
        """Normalizes a /compare argument ("MSFT AAPL" or "MSFT,AAPL", with
        arbitrary internal whitespace/comma mixing) to the comma-joined
        "symbols" form CompareAssetsTool expects (S12's note under the
        CommandParser code block) - a literal string transform, not a
        second symbol-resolution mechanism. A missing or single-symbol
        argument resolves safely: {} or {"symbols": "<ONE>"} respectively,
        never a raise.
        """
        symbols = [canonical_symbol(token) for token in _SYMBOL_SPLIT_PATTERN.split(argument) if token]
        return {"symbols": ",".join(symbols)} if symbols else {}
