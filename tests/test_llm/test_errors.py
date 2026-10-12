"""Tests for classifying provider HTTP failures."""

from __future__ import annotations

import pytest

from second_brain.llm import ModelErrorKind, classify_status

_UNREACHABLE_REASON = "Couldn't reach the model provider. Check your internet connection."
_KEY_REJECTED_REASON = "Your API key was rejected. Check it in Settings."
_OUT_OF_CREDITS_REASON = "Your API account is out of credits."
_NO_MODEL_ACCESS_REASON = "Your API key doesn't have access to this model."
_MODEL_UNAVAILABLE_REASON = "This model isn't available to your API account."
_TOO_LARGE_REASON = "The source is too large for the model."
_RATE_LIMIT_REASON = "The model provider is rate limiting requests. Try again later."
_PROVIDER_UNAVAILABLE_REASON = "The model provider is unavailable. Try again later."
_REJECTED_REASON = "The model provider rejected the request. For details, see logs/pipeline.log."


@pytest.mark.parametrize(
    ("status_code", "message", "kind", "reason"),
    [
        (None, "timed out", ModelErrorKind.NETWORK, _UNREACHABLE_REASON),
        (401, "invalid x-api-key", ModelErrorKind.ACCOUNT, _KEY_REJECTED_REASON),
        (402, "payment required", ModelErrorKind.ACCOUNT, _OUT_OF_CREDITS_REASON),
        (400, "Your credit balance is too low", ModelErrorKind.ACCOUNT, _OUT_OF_CREDITS_REASON),
        (400, "Your CREDIT BALANCE is too low", ModelErrorKind.ACCOUNT, _OUT_OF_CREDITS_REASON),
        (403, "forbidden", ModelErrorKind.ACCOUNT, _NO_MODEL_ACCESS_REASON),
        (404, "model not found", ModelErrorKind.ACCOUNT, _MODEL_UNAVAILABLE_REASON),
        (
            400,
            "prompt is too long: 210000 tokens > 200000 maximum",
            ModelErrorKind.TOO_LARGE,
            _TOO_LARGE_REASON,
        ),
        (400, "PROMPT IS TOO LONG", ModelErrorKind.TOO_LARGE, _TOO_LARGE_REASON),
        (413, "payload too large", ModelErrorKind.TOO_LARGE, _TOO_LARGE_REASON),
        (
            400,
            "You have reached your specified API usage limits. You will regain access on "
            "2026-11-01 at 00:00 UTC.",
            ModelErrorKind.ACCOUNT,
            "Your API account reached the spend limit you set.",
        ),
        (
            400,
            "You have reached your specified workspace API usage limits.",
            ModelErrorKind.ACCOUNT,
            "Your API workspace reached the spend limit you set.",
        ),
        (
            408,
            "request timeout",
            ModelErrorKind.NETWORK,
            "The model provider timed out. Try again later.",
        ),
        (429, "slow down", ModelErrorKind.RATE_LIMIT, _RATE_LIMIT_REASON),
        (529, "overloaded", ModelErrorKind.NETWORK, _PROVIDER_UNAVAILABLE_REASON),
        (504, "timeout_error", ModelErrorKind.NETWORK, _PROVIDER_UNAVAILABLE_REASON),
        (
            422,
            "Invalid Parameters",
            ModelErrorKind.REJECTED,
            _REJECTED_REASON,
        ),
        (500, "internal error", ModelErrorKind.NETWORK, _PROVIDER_UNAVAILABLE_REASON),
        (503, "unavailable", ModelErrorKind.NETWORK, _PROVIDER_UNAVAILABLE_REASON),
        (
            400,
            "invalid schema",
            ModelErrorKind.REJECTED,
            _REJECTED_REASON,
        ),
        (
            418,
            "I'm a teapot",
            ModelErrorKind.REJECTED,
            _REJECTED_REASON,
        ),
    ],
)
def test_classify_status(
    status_code: int | None, message: str, kind: ModelErrorKind, reason: str
) -> None:
    error = classify_status(status_code, message)
    assert error.kind is kind
    assert error.reason == reason
    assert str(error) == reason


def test_tier_spend_cap_429_is_an_account_failure_not_a_rate_limit() -> None:
    error = classify_status(
        429,
        "You have reached your API usage limits: your organization has crossed its "
        "monthly API usage threshold.",
        error_code="enforced_spend_limit_reached",
    )
    assert error.kind is ModelErrorKind.ACCOUNT
    assert error.reason == "Your API account reached its monthly spend limit."


def test_rejected_request_logs_the_provider_message(caplog: pytest.LogCaptureFixture) -> None:
    error = classify_status(418, "teapot refuses to brew")
    assert error.reason == _REJECTED_REASON
    assert "teapot refuses to brew" in caplog.text
