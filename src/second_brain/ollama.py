"""The one place that talks to the local Ollama server.

Holds the health probe used before a run, the error raised when the
server stops serving requests, and the request helper.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from second_brain.config import Config

PROBE_TIMEOUT_SECONDS = 5.0


class OllamaUnavailableError(RuntimeError):
    """Ollama could not serve a request (server unreachable, timed out, or the
    model is not installed), so every later request in the run would fail the
    same way."""


@dataclass(frozen=True)
class OllamaStatus:
    """Result of probing the local Ollama server.

    Distinguishes the two failure modes that need different fixes: the server
    not running at all, versus running but missing a required model.
    """

    host: str
    reachable: bool
    required_models: tuple[str, ...]
    missing_models: tuple[str, ...]

    @property
    def healthy(self) -> bool:
        return self.reachable and not self.missing_models

    def message(self) -> str:
        """A human-readable, actionable description of the current state."""
        if not self.reachable:
            return (
                f"Ollama is not running at {self.host}. Start it with 'ollama serve' "
                "(or open the Ollama app), then try again."
            )
        if self.missing_models:
            pulls = " ; ".join(f"ollama pull {model}" for model in self.missing_models)
            return (
                f"Ollama is running but missing required model(s): "
                f"{', '.join(self.missing_models)}. Pull them with: {pulls}"
            )
        return f"Ollama is healthy at {self.host}."


def required_models(config: Config) -> tuple[str, ...]:
    """The Ollama models the pipeline depends on (triage + embeddings)."""
    return (config.triage.model, config.search.embedding_model)


def _installed_models(host: str) -> set[str] | None:
    """Return the set of installed model tags, or ``None`` if unreachable."""
    try:
        response = httpx.get(f"{host}/api/tags", timeout=PROBE_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    names: set[str] = set()
    for entry in payload.get("models", []):
        name = entry.get("name") or entry.get("model")
        if name:
            names.add(name)
    return names


def _is_present(required: str, installed: set[str]) -> bool:
    """Whether a required model is installed.

    A required name with an explicit tag (``gemma4:12b``) must match exactly. An
    untagged name (``nomic-embed-text``) matches any installed tag of that repo.
    """
    if ":" in required:
        return required in installed
    return any(name.split(":", 1)[0] == required for name in installed)


def check_ollama(config: Config) -> OllamaStatus:
    """Probe Ollama for reachability and the presence of required models."""
    host = config.triage.ollama_host
    needed = required_models(config)
    installed = _installed_models(host)
    if installed is None:
        return OllamaStatus(
            host=host, reachable=False, required_models=needed, missing_models=needed
        )
    missing = tuple(model for model in needed if not _is_present(model, installed))
    return OllamaStatus(host=host, reachable=True, required_models=needed, missing_models=missing)


def post_to_ollama(host: str, path: str, payload: dict, timeout: float) -> dict:
    """POST JSON to the local Ollama server and return the parsed body.

    A connection failure or HTTP 404 means later requests would fail the
    same way, so those raise ``OllamaUnavailableError``. Any other HTTP
    error status propagates as ``httpx.HTTPStatusError`` so the caller
    can apply its own policy. A body that is not JSON raises ``ValueError``.

    Parameters
    ----------
    host: str
        Server origin, including scheme and port.
    path: str
        Request path appended to ``host``, starting with a slash.
    payload: dict
        JSON request body.
    timeout: float
        Seconds to wait for the response.

    Returns
    -------
    dict
        Parsed JSON response body.

    Raises
    ------
    OllamaUnavailableError
        When the connection fails, times out, or the server returns 404.
        The original error is chained.
    httpx.HTTPStatusError
        When the server returns any other error status.
    ValueError
        When the response body is not JSON.
    """
    try:
        response = httpx.post(f"{host}{path}", json=payload, timeout=timeout)
        response.raise_for_status()
    except httpx.TransportError as exc:
        raise OllamaUnavailableError("Ollama could not serve a request") from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise OllamaUnavailableError("Ollama could not serve a request") from exc
        raise
    return response.json()
