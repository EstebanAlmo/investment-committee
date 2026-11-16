"""LLMClient: the port every LLM provider adapter implements.

Business logic (committee/ai/) depends on this port only - never on a
provider SDK or HTTP API - so DeepSeek stays replaceable (CLAUDE.md,
docs/architecture.md layer 8). Messages are plain role/content pairs;
responses come back as already-parsed JSON objects plus token usage.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping


@dataclass(frozen=True)
class LLMCallInfo:
    """Safe diagnostics of the last provider attempt: never the key, the
    prompt, or the model's output."""

    http_status: int | None = None
    request_id: str | None = None
    finish_reason: str | None = None
    model: str | None = None


class LLMError(Exception):
    """Base class. Messages never contain the API key, the prompt, or the
    model's output - they are safe to log.

    `usage` and `attempts` cover every request made before the failure
    (failed replies are still billed), so callers can keep their budget
    honest; `info` holds the last attempt's safe diagnostics."""

    def __init__(self, message: str = "", *, usage: "LLMUsage | None" = None, attempts: int = 1, info: LLMCallInfo | None = None) -> None:
        super().__init__(message)
        self.usage = usage if usage is not None else LLMUsage()
        self.attempts = attempts
        self.info = info or LLMCallInfo()


class LLMNotConfiguredError(LLMError):
    """No API key (or no usable configuration): the AI committee is off."""


class LLMAuthenticationError(LLMError):
    """The provider rejected the credentials (HTTP 401)."""


class LLMQuotaError(LLMError):
    """The provider account has no balance left (HTTP 402)."""


class LLMRequestError(LLMError):
    """The provider rejected the request itself (HTTP 400/422), or another
    non-retryable HTTP error."""


class LLMUnavailableError(LLMError):
    """Transient failures (timeouts, 429, 5xx, network) that persisted after
    the allowed retries."""


class LLMTimeoutError(LLMUnavailableError):
    """The last attempt timed out."""


class LLMResponseError(LLMError):
    """The provider answered HTTP 200, but not with a usable JSON object.
    `reason` says which way (one of the constants below)."""

    INVALID_BODY = "invalid_body"  # the HTTP body is not JSON / has no choices
    TRUNCATED = "truncated"  # finish_reason "length": max_tokens reached
    UNEXPECTED_FINISH = "unexpected_finish"  # content_filter or another finish_reason
    EMPTY_CONTENT = "empty_content"  # finished normally with no content
    MALFORMED_JSON = "malformed_json"  # content is not valid JSON
    NOT_OBJECT = "not_object"  # valid JSON, but not an object

    def __init__(self, message: str = "", *, reason: str = INVALID_BODY, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.reason = reason


class LLMBudgetExceededError(LLMError):
    """A call would exceed the per-analysis call, token, or cost budget."""


@dataclass(frozen=True)
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    estimated_cost_usd: Decimal | None = None  # None: pricing unknown

    def __add__(self, other: "LLMUsage") -> "LLMUsage":
        cost = None
        if self.estimated_cost_usd is not None and other.estimated_cost_usd is not None:
            cost = self.estimated_cost_usd + other.estimated_cost_usd
        return LLMUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cache_hit_tokens=self.cache_hit_tokens + other.cache_hit_tokens,
            cache_miss_tokens=self.cache_miss_tokens + other.cache_miss_tokens,
            estimated_cost_usd=cost,
        )

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class LLMMessage:
    role: str  # "system" | "user"
    content: str


@dataclass(frozen=True)
class LLMResponse:
    data: Mapping[str, Any]  # the parsed JSON object
    usage: LLMUsage
    model: str
    attempts: int


class LLMClient(ABC):
    """Synchronous JSON-object completion."""

    provider_name: str = "unknown"

    @property
    @abstractmethod
    def model(self) -> str: ...

    @property
    @abstractmethod
    def max_output_tokens(self) -> int: ...

    @abstractmethod
    def complete_json(
        self,
        messages: list[LLMMessage],
        max_output_tokens: int | None = None,
        *,
        max_attempts: int | None = None,
        tag: str | None = None,
    ) -> LLMResponse:
        """Return the model's reply parsed as a JSON object. Raises an
        LLMError subclass on any failure; never returns partial text.
        `max_attempts` caps the requests this call may make (retries
        included), so a caller's call budget holds; `tag` labels the logs
        (e.g. the agent role)."""

    def estimate_cost_usd(self, prompt_tokens: int, completion_tokens: int) -> Decimal | None:
        """Worst-case cost (no cache hits) of a call this size, or None if
        the provider's pricing is unknown."""
        return None
