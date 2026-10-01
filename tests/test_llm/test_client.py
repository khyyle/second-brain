"""Tests for the Messages API client seam."""

from __future__ import annotations

import anthropic
import httpx
import pytest

from second_brain.llm import (
    ModelError,
    ModelErrorKind,
    request_turn,
    require_api_key,
    resolve_profile,
)


class _FakeMessages:
    def __init__(self, error: Exception) -> None:
        self._error = error
        self.kwargs: dict | None = None

    def create(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        raise self._error


class _FakeClient:
    def __init__(self, error: Exception) -> None:
        self.messages = _FakeMessages(error)


def _status_error(status_code: int, message: str) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status_code, request=request)
    return anthropic.APIStatusError(message, response=response, body=None)


def test_request_turn_translates_status_402_to_account() -> None:
    error = _status_error(402, "credit balance is too low")
    client = _FakeClient(error)

    with pytest.raises(ModelError) as exc_info:
        request_turn(
            client,
            model="claude-sonnet-4-6",
            max_tokens=16,
            system="system",
            tools=[],
            messages=[{"role": "user", "content": "hi"}],
        )

    assert exc_info.value.kind is ModelErrorKind.ACCOUNT
    assert exc_info.value.reason == "Out of API credits"
    assert exc_info.value.__cause__ is error
    assert client.messages.kwargs == {
        "model": "claude-sonnet-4-6",
        "max_tokens": 16,
        "system": "system",
        "tools": [],
        "messages": [{"role": "user", "content": "hi"}],
    }


def test_request_turn_reads_spend_cap_code_from_error_body() -> None:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(429, request=request)
    # Anthropic's documented tier spend-cap body; only the error code separates it
    # from an ordinary rate limit.
    body = {
        "type": "error",
        "error": {
            "type": "rate_limit_error",
            "message": "You have reached your API usage limits.",
            "details": {"error_code": "enforced_spend_limit_reached"},
        },
    }
    error = anthropic.RateLimitError("rate limited", response=response, body=body)
    client = _FakeClient(error)

    with pytest.raises(ModelError) as exc_info:
        request_turn(
            client,
            model="claude-sonnet-4-6",
            max_tokens=16,
            system="system",
            tools=[],
            messages=[],
        )

    assert exc_info.value.kind is ModelErrorKind.ACCOUNT


def test_request_turn_translates_connection_error_to_network() -> None:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.APIConnectionError(request=request)
    client = _FakeClient(error)

    with pytest.raises(ModelError) as exc_info:
        request_turn(
            client,
            model="claude-sonnet-4-6",
            max_tokens=16,
            system="system",
            tools=[],
            messages=[],
        )

    assert exc_info.value.kind is ModelErrorKind.NETWORK
    assert exc_info.value.reason == "Couldn't reach the model provider"
    assert exc_info.value.__cause__ is error


def test_require_api_key_raises_account_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    profile = resolve_profile("anthropic", "claude-sonnet-4-6")

    with pytest.raises(ModelError) as exc_info:
        require_api_key(profile)

    assert exc_info.value.kind is ModelErrorKind.ACCOUNT
    assert exc_info.value.reason == (
        "ANTHROPIC_API_KEY is not set. Add your anthropic API key "
        "in the app's Settings, or to a .env file at the repository root, "
        "then build again."
    )
    assert str(exc_info.value) == exc_info.value.reason
