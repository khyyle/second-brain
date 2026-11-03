"""Ollama diagnostics for the settings API."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from second_brain.pipeline_config import PipelineConfig

PROBE_TIMEOUT_SECONDS = 5.0


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
        """Check whether the server is reachable and all required models are installed.

        Returns
        -------
        bool
            True only when both conditions hold.
        """
        return self.reachable and not self.missing_models

    def message(self) -> str:
        """A human-readable, actionable description of the current state.

        Returns
        -------
        str
            Healthy status or instructions for starting Ollama and pulling missing models.
        """
        if not self.reachable:
            return (
                f"Ollama isn't running at {self.host}. "
                "Open the Ollama app or run 'ollama serve', then try again."
            )
        if self.missing_models:
            noun = "model" if len(self.missing_models) == 1 else "models"
            models = " and ".join(self.missing_models)
            pulls = " and ".join(f"'ollama pull {model}'" for model in self.missing_models)
            return f"Ollama is missing the {noun} {models}. Run {pulls}, then try again."
        return "Ollama is running with the required models."


def required_models(config: PipelineConfig) -> tuple[str, ...]:
    """The Ollama models the pipeline depends on (triage + embeddings).

    Parameters
    ----------
    config: PipelineConfig
        Preferences containing triage and embedding model names.

    Returns
    -------
    tuple[str, ...]
        Triage model followed by embedding model, regardless of whether triage is enabled.
    """
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


def check_ollama(config: PipelineConfig) -> OllamaStatus:
    """Probe Ollama for reachability and the presence of required models.

    Parameters
    ----------
    config: PipelineConfig
        Ollama host and required model settings.

    Returns
    -------
    OllamaStatus
        Server reachability and missing models. Transport or invalid-JSON failures report
        the server as unreachable.
    """
    host = config.triage.ollama_host
    needed = required_models(config)
    installed = _installed_models(host)
    if installed is None:
        return OllamaStatus(
            host=host, reachable=False, required_models=needed, missing_models=needed
        )
    missing = tuple(model for model in needed if not _is_present(model, installed))
    return OllamaStatus(host=host, reachable=True, required_models=needed, missing_models=missing)
