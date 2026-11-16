"""User-facing text shared by more than one Tool."""

from __future__ import annotations

from ..asset_policy import AssetClassDecision
from .base import PolicyDeclined

_NOTHING_DONE = "No se analizó ni se registró nada."


def unsupported_asset(symbol: str, decision: AssetClassDecision) -> PolicyDeclined:
    if decision is AssetClassDecision.UNSUPPORTED_UNRECOGNIZED:
        message = (
            f"{symbol} no parece un símbolo que yo pueda analizar. Envía el símbolo (ticker) de una "
            f"acción o ETF (por ejemplo AAPL, BRK.B) o un par Forex (por ejemplo EUR/USD o EURUSD=X). "
            f"{_NOTHING_DONE}"
        )
    else:
        message = (
            f"{symbol} no es una clase de activo admitida por ahora: este asistente cubre solo acciones, "
            f"ETF y pares de divisas Forex (todavía no criptomonedas, materias primas como el oro, "
            f"opciones ni futuros). {_NOTHING_DONE}"
        )
    return PolicyDeclined(reason=decision.value.lower(), message=message)
