"""AICommitteeService: the /committee entry point.

Composes: the deterministic ApplicationService (daily data, indicators,
baseline decision - not recorded in the Decision Ledger here), the
simulated-trading strategy's live reading for Forex (TradingService.preview_signal
- never recorded in the trade journal, so an AI opinion never becomes a
simulated trade), a MacroDataProvider, the AI coordinator, and the AI audit
ledger. Without a configured LLM provider the service reports itself
unavailable and nothing else changes: /analyze keeps working.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from investment_committee.committee.ai import AICommitteeCoordinator, AICommitteeReport
from investment_committee.market_intelligence.macro import MacroDataProvider, UnavailableMacroProvider
from investment_committee.memory import LedgerError
from investment_committee.memory.ai_ledger import AICommitteeLedger

from .ai_evidence import build_evidence_packet
from .errors import ApplicationServiceError, MarketDataUnavailableError
from .service import ApplicationService
from .trading import TradingNotAvailableError, TradingService

_logger = logging.getLogger(__name__)

_ACTIONS = {"BUY": "COMPRAR", "SELL": "VENDER", "HOLD": "MANTENER"}


class AICommitteeUnavailableError(ApplicationServiceError):
    """The AI committee cannot run (no API key or invalid configuration)."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"AI committee unavailable: {reason}")
        self.reason = reason


class AICommitteeRecordingError(ApplicationServiceError):
    """The analysis ran but could not be written to the audit ledger."""


class AICommitteeService:
    def __init__(
        self,
        application_service: ApplicationService,
        ledger: AICommitteeLedger,
        coordinator: AICommitteeCoordinator | None,
        trading_service: TradingService | None = None,
        macro_provider: MacroDataProvider | None = None,
        unavailable_reason: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._application_service = application_service
        self._ledger = ledger
        self._coordinator = coordinator
        self._trading = trading_service
        self._macro = macro_provider or UnavailableMacroProvider()
        self._unavailable_reason = unavailable_reason or "el proveedor de IA no está configurado"
        self._clock = clock

    @property
    def available(self) -> bool:
        return self._coordinator is not None

    @property
    def unavailable_reason(self) -> str | None:
        return None if self.available else self._unavailable_reason

    def analyze(self, symbol: str) -> AICommitteeReport:
        if self._coordinator is None:
            raise AICommitteeUnavailableError(self._unavailable_reason)
        analysis = self._application_service.analyze_asset(symbol)
        canonical = analysis.asset.ticker
        is_forex = canonical.endswith("=X")

        trade_signal = None
        trading_reason = None
        if is_forex and self._trading is not None:
            try:
                trade_signal = self._trading.preview_signal(canonical)
            except TradingNotAvailableError:
                trading_reason = "la estrategia simulada no está configurada para este par"
            except MarketDataUnavailableError:
                trading_reason = "el proveedor no entregó velas de 1 hora"

        now = self._clock()
        packet = build_evidence_packet(
            symbol=canonical,
            display_name=f"{canonical[:3]}/{canonical[3:6]}" if is_forex else canonical,
            is_forex=is_forex,
            analysis=analysis,
            trade_signal=trade_signal,
            trading_unavailable_reason=trading_reason,
            documents=self._macro.documents(canonical, now),
            macro_source=self._macro.name,
            now=now,
        )
        baseline = f"{_ACTIONS.get(analysis.decision.action.value, analysis.decision.action.value)} (confianza interna {str(analysis.decision.confidence).replace('.', ',')})"
        report = self._coordinator.run(packet, deterministic_baseline=baseline)
        try:
            self._ledger.append(report)
        except LedgerError as exc:
            raise AICommitteeRecordingError(f"failed to record AI analysis {report.analysis_id}: {exc}") from exc
        _logger.info(
            "AI committee analysis %s for %s: status=%s calls=%d tokens=%d",
            report.analysis_id, canonical, report.validation_status.value, report.calls, report.usage.total_tokens,
        )
        return report
