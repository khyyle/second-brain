"""Messages API client for one compilation turn."""

from __future__ import annotations

import os

import anthropic

from second_brain.llm.errors import ModelError, ModelErrorKind, classify_status
from second_brain.llm.providers import ProviderProfile


def create_client(profile: ProviderProfile) -> anthropic.Anthropic:
    """
    Build a Messages API client for a resolved provider profile.

    Parameters
    ----------
    profile: ProviderProfile
        Transport settings, including which environment variable holds the
        API key and an optional base URL.

    Returns
    -------
    anthropic.Anthropic
        Client configured from the profile and the current environment.
    """
    return anthropic.Anthropic(**profile.client_kwargs())


def require_api_key(profile: ProviderProfile) -> None:
    """
    Raise when the profile's API key is missing from the environment.

    Parameters
    ----------
    profile: ProviderProfile
        Provider whose key variable is checked.

    Raises
    ------
    ModelError
        Kind ``ACCOUNT`` when the variable is unset or empty.
    """
    if not os.environ.get(profile.api_key_env):
        raise ModelError(
            ModelErrorKind.ACCOUNT,
            f"{profile.api_key_env} is not set. Add your {profile.name} API key "
            "in the app's Settings, or to a .env file at the repository root, "
            "then build again.",
        )


def _error_code(body: object) -> str | None:
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    details = error.get("details") if isinstance(error, dict) else None
    error_code = details.get("error_code") if isinstance(details, dict) else None
    return error_code if isinstance(error_code, str) else None


def request_turn(
    client: anthropic.Anthropic,
    *,
    model: str,
    max_tokens: int,
    system: str | list[dict],
    tools: list[dict],
    messages: list[dict],
) -> anthropic.types.Message:
    """
    Send one Messages API turn and return the provider's own response.

    Provider failures are raised only as ``ModelError``. The return value
    is the SDK response object, unchanged.

    Parameters
    ----------
    client: anthropic.Anthropic
        Client for the selected provider.
    model: str
        Model id.
    max_tokens: int
        Per-turn output cap.
    system: str | list[dict]
        System prompt, as a string or as content blocks.
    tools: list[dict]
        Tool definitions for this turn.
    messages: list[dict]
        Conversation so far.

    Returns
    -------
    anthropic.types.Message
        The SDK response from the provider.

    Raises
    ------
    ModelError
        When the provider rejects the request or cannot be reached,
        including timeouts.
    """
    try:
        return client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )
    except anthropic.APIStatusError as exc:
        raise classify_status(exc.status_code, exc.message, _error_code(exc.body)) from exc
    except anthropic.APIConnectionError as exc:
        raise classify_status(None, str(exc)) from exc
