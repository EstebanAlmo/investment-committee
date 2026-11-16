"""DeepSeekClient: LLMClient over DeepSeek's official OpenAI-compatible API.

Verified against https://api-docs.deepseek.com on 2026-10-13:
- POST {base_url}/chat/completions, header "Authorization: Bearer <key>";
- models "deepseek-flash" (recommended) and "deepseek-v4-pro";
- JSON Output via response_format {"type": "json_object"} - the prompt must
  mention JSON, and the API may occasionally return empty content;
- "thinking" defaults to enabled; this client always sends
  {"type": "disabled"} so no hidden reasoning is requested, billed, or kept
  (any reasoning_content in a reply is discarded unread);
- finish_reason: "stop", "length" (max_tokens reached), "content_filter",
  "insufficient_system_resource" (inference capacity ran out);
- usage: prompt_tokens, completion_tokens, prompt_cache_hit_tokens,
  prompt_cache_miss_tokens;
- errors: 400/422 invalid request, 401 bad key, 402 no balance, 429 rate
  limit, 500 server error, 503 overloaded.
Pricing (USD per 1M tokens, same date), peak = 01:00-04:00 and 06:00-10:00
UTC Monday-Friday, off-peak = half: deepseek-flash input cache hit 0.006,
cache miss 0.30, output 1.20 (peak); deepseek-v4-pro 0.044 / 1.32 / 3.96.

This is the only module that talks to DeepSeek. It never logs the key,
prompts, or responses - only one structured line per attempt (model, agent
tag, attempt, outcome, HTTP status, request id, finish_reason, token
counts). Not verifiable offline: how often deepseek-flash really returns
empty JSON-mode content or "insufficient_system_resource"; both are handled
as the docs describe.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Mapping

import httpx

from .base import (
    LLMAuthenticationError,
    LLMCallInfo,
    LLMClient,
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

_logger = logging.getLogger(__name__)

DEEPSEEK_API_KEY = "DEEPSEEK_API_KEY"
DEEPSEEK_MODEL = "DEEPSEEK_MODEL"
DEEPSEEK_BASE_URL = "DEEPSEEK_BASE_URL"
DEEPSEEK_TIMEOUT_SECONDS = "DEEPSEEK_TIMEOUT_SECONDS"
DEEPSEEK_MAX_RETRIES = "DEEPSEEK_MAX_RETRIES"
DEEPSEEK_MAX_OUTPUT_TOKENS = "DEEPSEEK_MAX_OUTPUT_TOKENS"

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
KNOWN_MODELS = ("deepseek-flash", "deepseek-v4-pro")
PRICING_VERIFIED_ON = "2026-10-13"
# Was 900. In the real runs of 2026-10-13 (data/ledger/ai_committee.jsonl)
# the technical analyst and the coordinator used 794-891 completion tokens
# when they succeeded, and they were exactly the agents that failed with an
# unusable reply: 900 left no headroom. 1600 keeps a full five-call analysis
# well inside AnalysisBudget (40 000 tokens, 0.05 USD).
DEFAULT_MAX_OUTPUT_TOKENS = 1600

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_RETRYABLE_FINISH = "insufficient_system_resource"
_MILLION = Decimal("1000000")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


@dataclass(frozen=True)
class ModelPricing:
    """Peak prices in USD per 1M tokens; off-peak is half."""

    input_cache_hit: Decimal
    input_cache_miss: Decimal
    output: Decimal


PRICING: Mapping[str, ModelPricing] = {
    "deepseek-flash": ModelPricing(Decimal("0.006"), Decimal("0.30"), Decimal("1.20")),
    "deepseek-v4-pro": ModelPricing(Decimal("0.044"), Decimal("1.32"), Decimal("3.96")),
}


def is_peak(moment: datetime) -> bool:
    utc = moment.astimezone(timezone.utc)
    return utc.weekday() < 5 and (1 <= utc.hour < 4 or 6 <= utc.hour < 10)


@dataclass(frozen=True)
class DeepSeekConfig:
    api_key: str = field(repr=False)
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    timeout_seconds: float = 30.0
    max_retries: int = 2
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "DeepSeekConfig":
        """Raises LLMNotConfiguredError (naming the variable, never its
        value) when the key is missing or a setting is malformed."""
        api_key = env.get(DEEPSEEK_API_KEY, "").strip()
        if not api_key:
            raise LLMNotConfiguredError(f"{DEEPSEEK_API_KEY} is not set")
        model = env.get(DEEPSEEK_MODEL, "").strip() or DEFAULT_MODEL
        if model not in KNOWN_MODELS:
            raise LLMNotConfiguredError(f"{DEEPSEEK_MODEL} must be one of {', '.join(KNOWN_MODELS)}")
        try:
            timeout = float(env.get(DEEPSEEK_TIMEOUT_SECONDS, "") or 30)
            retries = int(env.get(DEEPSEEK_MAX_RETRIES, "") or 2)
            max_output = int(env.get(DEEPSEEK_MAX_OUTPUT_TOKENS, "") or DEFAULT_MAX_OUTPUT_TOKENS)
        except ValueError as exc:
            raise LLMNotConfiguredError(f"a DeepSeek numeric setting is malformed: {type(exc).__name__}") from None
        if not (0 < timeout <= 300 and 0 <= retries <= 5 and 1 <= max_output <= 8000):
            raise LLMNotConfiguredError("DeepSeek timeout/retries/max output tokens are out of range")
        base_url = (env.get(DEEPSEEK_BASE_URL, "").strip() or DEFAULT_BASE_URL).rstrip("/")
        return cls(api_key=api_key, model=model, base_url=base_url, timeout_seconds=timeout, max_retries=retries, max_output_tokens=max_output)


class DeepSeekClient(LLMClient):
    provider_name = "deepseek"

    def __init__(
        self,
        config: DeepSeekConfig,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        backoff_seconds: float = 1.0,
    ) -> None:
        self._config = config
        self._sleep = sleep
        self._clock = clock
        self._backoff = backoff_seconds
        self._http = httpx.Client(
            base_url=config.base_url,
            timeout=httpx.Timeout(config.timeout_seconds),
            transport=transport,
            headers={"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"},
        )

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def max_output_tokens(self) -> int:
        return self._config.max_output_tokens

    def estimate_cost_usd(self, prompt_tokens: int, completion_tokens: int) -> Decimal | None:
        pricing = PRICING.get(self._config.model)
        if pricing is None:
            return None
        return _cost(pricing, 0, prompt_tokens, completion_tokens, peak=True)

    def complete_json(
        self,
        messages: list[LLMMessage],
        max_output_tokens: int | None = None,
        *,
        max_attempts: int | None = None,
        tag: str | None = None,
    ) -> LLMResponse:
        """At most min(1 + DEEPSEEK_MAX_RETRIES, max_attempts) requests.

        Retried: timeouts, network errors, HTTP 429/5xx, empty content (a
        documented JSON Output quirk) and finish_reason
        "insufficient_system_resource". Never retried: 401, 402, 400/422 and
        other HTTP errors (credentials, balance or configuration will not
        fix themselves), truncation (the same request would be cut again;
        the caller decides), malformed JSON, content_filter and any other
        finish_reason. Errors carry every attempt's usage, since failed
        replies are billed too.
        """
        limit = min(max_output_tokens or self._config.max_output_tokens, self._config.max_output_tokens)
        payload = {
            "model": self._config.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "max_tokens": limit,
            "temperature": 0.2,
            "stream": False,
        }
        allowed = 1 + self._config.max_retries
        if max_attempts is not None:
            allowed = max(1, min(allowed, max_attempts))
        usage = LLMUsage(estimated_cost_usd=Decimal("0") if self._config.model in PRICING else None)
        attempt = 0
        while True:
            attempt += 1
            log = _AttemptLog(self._config.model, tag, attempt, allowed, limit)
            can_retry = attempt < allowed
            try:
                reply = self._http.post("/chat/completions", json=payload)
            except httpx.TimeoutException:
                if log.emit("timeout", retry=can_retry):
                    self._wait(attempt)
                    continue
                raise LLMTimeoutError(f"DeepSeek timed out after {attempt} attempt(s)", usage=usage, attempts=attempt, info=log.info()) from None
            except httpx.TransportError as exc:
                if log.emit(f"network_{type(exc).__name__}", retry=can_retry):
                    self._wait(attempt)
                    continue
                raise LLMUnavailableError(
                    f"DeepSeek unreachable after {attempt} attempt(s): {type(exc).__name__}", usage=usage, attempts=attempt, info=log.info()
                ) from None

            status = reply.status_code
            log.http_status = status
            log.request_id = _header_request_id(reply)
            if status != 200:
                retryable = status in _RETRYABLE_STATUS
                if log.emit(f"http_{status}", retry=retryable and can_retry):
                    self._wait(attempt)
                    continue
                context: dict[str, Any] = {"usage": usage, "attempts": attempt, "info": log.info()}
                if retryable:
                    raise LLMUnavailableError(f"DeepSeek returned HTTP {status} after {attempt} attempt(s)", **context)
                if status == 401:
                    raise LLMAuthenticationError("DeepSeek rejected the API key (HTTP 401)", **context)
                if status == 402:
                    raise LLMQuotaError("DeepSeek account has insufficient balance (HTTP 402)", **context)
                raise LLMRequestError(f"DeepSeek rejected the request (HTTP {status})", **context)

            try:
                body = reply.json()
            except ValueError:
                body = None
            if isinstance(body, dict):
                if log.request_id is None:
                    log.request_id = _safe(body.get("id"))
                raw_usage = body.get("usage")
                log.usage = self._usage(raw_usage if isinstance(raw_usage, dict) else {})
                usage = usage + log.usage
            try:
                data = _parse(body, log)
            except LLMResponseError as exc:
                retryable = exc.reason == LLMResponseError.EMPTY_CONTENT or log.finish_reason == _RETRYABLE_FINISH
                if log.emit(exc.reason, retry=retryable and can_retry):
                    continue
                raise LLMResponseError(str(exc), reason=exc.reason, usage=usage, attempts=attempt, info=log.info()) from None
            log.emit("ok", retry=False)
            return LLMResponse(data=data, usage=usage, model=self._config.model, attempts=attempt)

    def _wait(self, attempt: int) -> None:
        self._sleep(self._backoff * (2 ** (attempt - 1)))

    def _usage(self, raw: Mapping[str, Any]) -> LLMUsage:
        prompt = int(raw.get("prompt_tokens") or 0)
        completion = int(raw.get("completion_tokens") or 0)
        hit = int(raw.get("prompt_cache_hit_tokens") or 0)
        miss = int(raw.get("prompt_cache_miss_tokens") or (prompt - hit))
        pricing = PRICING.get(self._config.model)
        cost = None if pricing is None else _cost(pricing, hit, miss, completion, peak=is_peak(self._clock()))
        return LLMUsage(prompt, completion, hit, miss, cost)

    def close(self) -> None:
        self._http.close()


class _AttemptLog:
    """One structured, safe log line per attempt: never the key, the
    prompt, or the content - only status, ids, finish_reason and counts."""

    def __init__(self, model: str, tag: str | None, attempt: int, allowed: int, max_tokens: int) -> None:
        self.model, self.tag, self.attempt, self.allowed, self.max_tokens = model, tag, attempt, allowed, max_tokens
        self.http_status: int | None = None
        self.request_id: str | None = None
        self.finish_reason: str | None = None
        self.usage: LLMUsage | None = None

    def info(self) -> LLMCallInfo:
        return LLMCallInfo(self.http_status, self.request_id, self.finish_reason, self.model)

    def emit(self, outcome: str, *, retry: bool) -> bool:
        """Log this attempt's outcome; returns `retry` for the caller's if."""
        _logger.log(
            logging.INFO if outcome == "ok" else logging.WARNING,
            "llm_call provider=deepseek model=%s agent=%s attempt=%d/%d outcome=%s http_status=%s request_id=%s "
            "finish_reason=%s max_tokens=%d prompt_tokens=%s completion_tokens=%s will_retry=%s",
            self.model,
            self.tag or "-",
            self.attempt,
            self.allowed,
            outcome,
            self.http_status if self.http_status is not None else "-",
            self.request_id or "-",
            self.finish_reason or "-",
            self.max_tokens,
            self.usage.prompt_tokens if self.usage else "-",
            self.usage.completion_tokens if self.usage else "-",
            retry,
        )
        return retry


def _safe(value: Any) -> str | None:
    return value if isinstance(value, str) and _SAFE_TOKEN.match(value) else None


def _header_request_id(reply: httpx.Response) -> str | None:
    return next((v for v in (_safe(reply.headers.get(h)) for h in ("x-request-id", "x-ds-trace-id")) if v), None)


def _parse(body: Any, log: _AttemptLog) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise LLMResponseError("DeepSeek reply body is not a JSON object", reason=LLMResponseError.INVALID_BODY)
    try:
        choice = body["choices"][0]
        content = choice["message"].get("content")
        finish = choice.get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        raise LLMResponseError("DeepSeek reply has no choices/message", reason=LLMResponseError.INVALID_BODY) from None
    log.finish_reason = None if finish is None else (_safe(finish) or "unrecognised")
    if finish == "length":
        raise LLMResponseError("DeepSeek reply was truncated (max_tokens reached)", reason=LLMResponseError.TRUNCATED)
    if finish not in (None, "stop"):
        raise LLMResponseError(f"DeepSeek reply finished with {log.finish_reason}", reason=LLMResponseError.UNEXPECTED_FINISH)
    if not content or not str(content).strip():
        raise LLMResponseError("DeepSeek returned empty content", reason=LLMResponseError.EMPTY_CONTENT)
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        raise LLMResponseError("DeepSeek content is not valid JSON", reason=LLMResponseError.MALFORMED_JSON) from None
    if not isinstance(data, dict):
        raise LLMResponseError("DeepSeek content is not a JSON object", reason=LLMResponseError.NOT_OBJECT)
    return data


def _cost(pricing: ModelPricing, hit: int, miss: int, completion: int, *, peak: bool) -> Decimal:
    factor = Decimal("1") if peak else Decimal("0.5")
    raw = (
        Decimal(hit) * pricing.input_cache_hit + Decimal(miss) * pricing.input_cache_miss + Decimal(completion) * pricing.output
    ) / _MILLION
    return (raw * factor).quantize(Decimal("0.000001"))
