"""Runtime dependency checks for the local model stack.

Ollama hosts the local models the pipeline needs (Gemma for triage, the
embedding model for semantic search and clustering). It is a hard requirement.
This module reports whether the server is up and the required models are
installed before a run starts. A server that stops answering during a run
raises ``OllamaUnavailableError`` so later work does not continue blindly.
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
