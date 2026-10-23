"""Provider responses shared by agent and compiler behavior tests."""

from __future__ import annotations

from typing import Any

import anthropic
import httpx
import pytest

from second_brain.compilation.agent import loop


class FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeResponse:
    def __init__(self, stop_reason: str, usage: FakeUsage, content: list[Any]) -> None:
        self.stop_reason = stop_reason
        self.usage = usage
        self.content = content


class FakeMessages:
    """Return ``response`` for the first ``successful_calls``, then raise ``error``."""

    def __init__(
        self,
        response: FakeResponse,
        *,
        successful_calls: int = 0,
        error: Exception | None = None,
    ) -> None:
        self._response = response
        self._successful_calls = successful_calls
        self._error = error
        self.calls = 0

    def create(self, **_kwargs: Any) -> FakeResponse:
        self.calls += 1
        if self._error is not None and self.calls > self._successful_calls:
            raise self._error
        return self._response


class FakeClient:
    def __init__(
        self,
        response: FakeResponse,
        *,
        successful_calls: int = 0,
        error: Exception | None = None,
    ) -> None:
        self.messages = FakeMessages(response, successful_calls=successful_calls, error=error)


def status_error(status_code: int, message: str) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.invalid/v1/messages")
    response = httpx.Response(status_code, request=request)
    return anthropic.APIStatusError(message, response=response, body=None)


def install_fake(
    monkeypatch: pytest.MonkeyPatch,
    response: FakeResponse,
    *,
    successful_calls: int = 0,
    error: Exception | None = None,
) -> FakeClient:
    client = FakeClient(response, successful_calls=successful_calls, error=error)
    monkeypatch.setattr(loop, "create_client", lambda *_args, **_kwargs: client)
    return client
