"""LLM provider adapters behind the LLMClient port (docs/architecture.md,
layer 8). DeepSeek is the first and only adapter; nothing outside this
package imports httpx for LLM calls or knows DeepSeek exists."""

from __future__ import annotations

from .base import (
    LLMAuthenticationError,
    LLMBudgetExceededError,
    LLMCallInfo,
    LLMClient,
    LLMError,
    LLMMessage,
    LLMNotConfiguredError,
    LLMQuotaError,
    LLMRequestError,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
    LLMUsage,
)
from .deepseek import DeepSeekClient, DeepSeekConfig

__all__ = [
    "DeepSeekClient",
    "DeepSeekConfig",
    "LLMAuthenticationError",
    "LLMBudgetExceededError",
    "LLMCallInfo",
    "LLMClient",
    "LLMError",
    "LLMMessage",
    "LLMNotConfiguredError",
    "LLMQuotaError",
    "LLMRequestError",
    "LLMResponse",
    "LLMResponseError",
    "LLMTimeoutError",
    "LLMUnavailableError",
    "LLMUsage",
]
