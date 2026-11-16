"""Tests for integrations.llm.deepseek - httpx.MockTransport only, never a
real (paid) request."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import httpx
import pytest

from investment_committee.integrations.llm import (
    DeepSeekClient,
    DeepSeekConfig,
    LLMAuthenticationError,
    LLMMessage,
    LLMNotConfiguredError,
    LLMQuotaError,
    LLMRequestError,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from investment_committee.integrations.llm.deepseek import is_peak

FAKE_KEY = "sk-test-not-a-real-key-0000"
OFF_PEAK = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)  # Tuesday 12:00 UTC
PEAK = datetime(2026, 10, 13, 7, 0, tzinfo=timezone.utc)
MESSAGES = [LLMMessage("system", "Responde en JSON."), LLMMessage("user", "Evidencia: ...")]


def _reply(content: str | None = '{"postura": "NEUTRAL"}', finish: str = "stop", status: int = 200, usage: dict | None = None) -> httpx.Response:
    body = {
        "choices": [{"message": {"role": "assistant", "content": content, "reasoning_content": "hidden"}, "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 200, "prompt_cache_hit_tokens": 400, "prompt_cache_miss_tokens": 600},
    }
    return httpx.Response(status, json=body)


def _client(handler: Callable[[httpx.Request], httpx.Response], clock: datetime = OFF_PEAK, **config: object):
    sleeps: list[float] = []
    requests: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = DeepSeekClient(
        DeepSeekConfig(api_key=FAKE_KEY, **config),  # type: ignore[arg-type]
        transport=httpx.MockTransport(recording),
        sleep=sleeps.append,
        clock=lambda: clock,
    )
    return client, requests, sleeps


# --- configuration ---


def test_missing_api_key_means_not_configured() -> None:
    with pytest.raises(LLMNotConfiguredError, match="DEEPSEEK_API_KEY is not set"):
        DeepSeekConfig.from_env({})


def test_config_from_env_with_defaults_and_overrides() -> None:
    config = DeepSeekConfig.from_env({"DEEPSEEK_API_KEY": FAKE_KEY})
    assert (config.model, config.base_url, config.timeout_seconds, config.max_retries, config.max_output_tokens) == (
        "deepseek-flash", "https://api.deepseek.com", 30.0, 2, 1600,
    )

    custom = DeepSeekConfig.from_env(
        {"DEEPSEEK_API_KEY": FAKE_KEY, "DEEPSEEK_MODEL": "deepseek-v4-pro", "DEEPSEEK_TIMEOUT_SECONDS": "10", "DEEPSEEK_MAX_RETRIES": "1", "DEEPSEEK_MAX_OUTPUT_TOKENS": "500"}
    )
    assert (custom.model, custom.timeout_seconds, custom.max_retries, custom.max_output_tokens) == ("deepseek-v4-pro", 10.0, 1, 500)


@pytest.mark.parametrize(
    "env",
    [
        {"DEEPSEEK_MODEL": "deepseek-chat-9000"},
        {"DEEPSEEK_TIMEOUT_SECONDS": "abc"},
        {"DEEPSEEK_MAX_RETRIES": "50"},
        {"DEEPSEEK_MAX_OUTPUT_TOKENS": "0"},
    ],
)
def test_invalid_settings_are_rejected_without_echoing_values(env: dict[str, str]) -> None:
    with pytest.raises(LLMNotConfiguredError) as raised:
        DeepSeekConfig.from_env({"DEEPSEEK_API_KEY": FAKE_KEY, **env})
    assert FAKE_KEY not in str(raised.value)


def test_api_key_is_not_in_the_config_repr() -> None:
    assert FAKE_KEY not in repr(DeepSeekConfig(api_key=FAKE_KEY))


# --- a correct call ---


def test_request_shape_json_mode_and_thinking_disabled() -> None:
    client, requests, _ = _client(lambda r: _reply())

    response = client.complete_json(MESSAGES, max_output_tokens=300)

    (request,) = requests
    body = json.loads(request.content)
    assert request.url == "https://api.deepseek.com/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert body["model"] == "deepseek-flash"
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "disabled"}
    assert body["max_tokens"] == 300
    assert body["messages"][0] == {"role": "system", "content": "Responde en JSON."}
    assert response.data == {"postura": "NEUTRAL"}
    assert "reasoning_content" not in json.dumps(dict(response.data))


def test_output_token_limit_cannot_exceed_the_configured_maximum() -> None:
    client, requests, _ = _client(lambda r: _reply(), max_output_tokens=400)

    client.complete_json(MESSAGES, max_output_tokens=5000)

    assert json.loads(requests[0].content)["max_tokens"] == 400


def test_usage_and_off_peak_cost() -> None:
    client, _, _ = _client(lambda r: _reply())

    usage = client.complete_json(MESSAGES).usage

    assert (usage.prompt_tokens, usage.completion_tokens, usage.cache_hit_tokens, usage.cache_miss_tokens) == (1000, 200, 400, 600)
    # off-peak flash: (400 x 0.003 + 600 x 0.15 + 200 x 0.6) / 1M
    assert usage.estimated_cost_usd == Decimal("0.000211")


def test_peak_cost_is_double() -> None:
    client, _, _ = _client(lambda r: _reply(), clock=PEAK)

    assert client.complete_json(MESSAGES).usage.estimated_cost_usd == Decimal("0.000422")


def test_peak_hours_follow_the_published_schedule() -> None:
    assert is_peak(datetime(2026, 10, 13, 1, 0, tzinfo=timezone.utc))
    assert not is_peak(datetime(2026, 10, 13, 4, 0, tzinfo=timezone.utc))
    assert is_peak(datetime(2026, 10, 13, 9, 59, tzinfo=timezone.utc))
    assert not is_peak(datetime(2026, 10, 17, 7, 0, tzinfo=timezone.utc))  # Saturday


# --- failures ---


def test_timeouts_are_retried_with_backoff_then_raised() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client, requests, sleeps = _client(timeout, max_retries=2)

    with pytest.raises(LLMTimeoutError):
        client.complete_json(MESSAGES)
    assert len(requests) == 3
    assert sleeps == [1.0, 2.0]


def test_transient_errors_recover_within_the_retry_limit() -> None:
    replies = iter([httpx.Response(503), httpx.Response(429), _reply()])
    client, requests, sleeps = _client(lambda r: next(replies), max_retries=2)

    response = client.complete_json(MESSAGES)

    assert response.attempts == 3 and len(requests) == 3 and sleeps == [1.0, 2.0]


def test_persistent_server_errors_raise_unavailable() -> None:
    client, requests, _ = _client(lambda r: httpx.Response(500), max_retries=1)

    with pytest.raises(LLMUnavailableError, match="HTTP 500"):
        client.complete_json(MESSAGES)
    assert len(requests) == 2


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, LLMAuthenticationError), (402, LLMQuotaError), (400, LLMRequestError), (422, LLMRequestError)],
)
def test_client_errors_are_not_retried(status: int, error: type[Exception]) -> None:
    client, requests, sleeps = _client(lambda r: httpx.Response(status, json={"error": {"message": "x"}}))

    with pytest.raises(error):
        client.complete_json(MESSAGES)
    assert len(requests) == 1 and sleeps == []


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        (_reply(content="esto no es json"), LLMResponseError.MALFORMED_JSON),
        (_reply(content="[1, 2]"), LLMResponseError.NOT_OBJECT),
        (_reply(content='{"postura": "NEU', finish="length"), LLMResponseError.TRUNCATED),
        (_reply(finish="content_filter"), LLMResponseError.UNEXPECTED_FINISH),
        (httpx.Response(200, json={"unexpected": True}), LLMResponseError.INVALID_BODY),
        (httpx.Response(200, text="<html>bad gateway</html>"), LLMResponseError.INVALID_BODY),
    ],
)
def test_unusable_replies_are_classified_and_not_retried(reply: httpx.Response, reason: str) -> None:
    client, requests, sleeps = _client(lambda r: reply)

    with pytest.raises(LLMResponseError) as raised:
        client.complete_json(MESSAGES)

    assert raised.value.reason == reason
    assert len(requests) == 1 and sleeps == []
    assert raised.value.attempts == 1 and raised.value.info.http_status == 200


def test_truncation_reports_finish_reason_and_the_billed_tokens() -> None:
    usage = {"prompt_tokens": 3000, "completion_tokens": 900, "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 3000}
    client, _, _ = _client(lambda r: _reply(content='{"postura": "ALC', finish="length", usage=usage))

    with pytest.raises(LLMResponseError) as raised:
        client.complete_json(MESSAGES)

    error = raised.value
    assert error.reason == LLMResponseError.TRUNCATED
    assert error.info.finish_reason == "length" and error.info.model == "deepseek-flash"
    assert (error.usage.prompt_tokens, error.usage.completion_tokens) == (3000, 900)
    assert error.usage.estimated_cost_usd is not None and error.usage.estimated_cost_usd > 0


@pytest.mark.parametrize("content", ["", None, "   "])
def test_empty_content_is_retried_within_the_retry_limit(content: str | None) -> None:
    client, requests, _ = _client(lambda r: _reply(content=content), max_retries=1)

    with pytest.raises(LLMResponseError) as raised:
        client.complete_json(MESSAGES)

    assert raised.value.reason == LLMResponseError.EMPTY_CONTENT
    assert len(requests) == 2 and raised.value.attempts == 2
    assert raised.value.usage.prompt_tokens == 2000  # both empty replies were billed


def test_empty_reply_then_valid_reply_succeeds_and_sums_usage() -> None:
    replies = iter([_reply(content=""), _reply()])
    client, _, _ = _client(lambda r: next(replies))

    response = client.complete_json(MESSAGES)

    assert response.data == {"postura": "NEUTRAL"} and response.attempts == 2
    assert response.usage.prompt_tokens == 2000


def test_insufficient_system_resource_is_retried_as_transient() -> None:
    replies = iter([_reply(content="", finish="insufficient_system_resource"), _reply()])
    client, requests, _ = _client(lambda r: next(replies))

    assert client.complete_json(MESSAGES).attempts == 2 and len(requests) == 2


def test_unknown_finish_reason_is_not_echoed_verbatim() -> None:
    client, _, _ = _client(lambda r: _reply(finish="weird finish <script>"))

    with pytest.raises(LLMResponseError) as raised:
        client.complete_json(MESSAGES)

    assert raised.value.reason == LLMResponseError.UNEXPECTED_FINISH
    assert raised.value.info.finish_reason == "unrecognised" and "script" not in str(raised.value)


def test_max_attempts_caps_retries_below_the_configured_limit() -> None:
    client, requests, sleeps = _client(lambda r: httpx.Response(503), max_retries=3)

    with pytest.raises(LLMUnavailableError) as raised:
        client.complete_json(MESSAGES, max_attempts=2)

    assert len(requests) == 2 and sleeps == [1.0] and raised.value.attempts == 2


@pytest.mark.parametrize("status", [401, 402, 400])
def test_non_retryable_errors_carry_status_and_request_id(status: int) -> None:
    client, requests, _ = _client(lambda r: httpx.Response(status, headers={"x-request-id": "req-123"}, json={}), max_retries=3)

    with pytest.raises(Exception) as raised:
        client.complete_json(MESSAGES, max_attempts=4)

    assert len(requests) == 1
    assert raised.value.info.http_status == status and raised.value.info.request_id == "req-123"


def test_structured_log_line_has_diagnostics(caplog: pytest.LogCaptureFixture) -> None:
    body = {
        "id": "chatcmpl-abc",
        "choices": [{"message": {"content": '{"a": "SECRETO'}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }
    client, _, _ = _client(lambda r: httpx.Response(200, json=body))

    with caplog.at_level(logging.INFO), pytest.raises(LLMResponseError):
        client.complete_json(MESSAGES, tag="TECHNICAL_ANALYST")

    line = caplog.records[-1].getMessage()
    for expected in ("agent=TECHNICAL_ANALYST", "outcome=truncated", "http_status=200", "request_id=chatcmpl-abc",
                     "finish_reason=length", "prompt_tokens=10", "completion_tokens=20", "model=deepseek-flash"):
        assert expected in line
    assert "SECRETO" not in caplog.text


def test_nothing_secret_is_logged_or_raised(caplog: pytest.LogCaptureFixture) -> None:
    secret_prompt = [LLMMessage("user", "PROMPT-SENSIBLE-123")]
    replies = iter([httpx.Response(503), _reply(content='{"x": "RESPUESTA-SENSIBLE-456"}')])
    client, _, _ = _client(lambda r: next(replies))

    with caplog.at_level(logging.DEBUG):
        client.complete_json(secret_prompt)
        failing, _, _ = _client(lambda r: httpx.Response(401))
        with pytest.raises(LLMAuthenticationError) as raised:
            failing.complete_json(secret_prompt)

    logged = caplog.text + str(raised.value)
    for secret in (FAKE_KEY, "PROMPT-SENSIBLE-123", "RESPUESTA-SENSIBLE-456"):
        assert secret not in logged
