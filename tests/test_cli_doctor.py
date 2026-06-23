"""Tests for the Ollama preflight gate used by ingest/compile/mcp."""

from __future__ import annotations

from pathlib import Path

import pytest
from click import ClickException

from second_brain import cli, dependencies
from second_brain.config import Config


def _status(*, reachable: bool, missing: tuple[str, ...]) -> dependencies.OllamaStatus:
    return dependencies.OllamaStatus(
        host="http://localhost:11434",
        reachable=reachable,
        required_models=("gemma4:12b", "nomic-embed-text"),
        missing_models=missing,
    )


def test_require_ollama_raises_when_unhealthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        dependencies, "check_ollama", lambda _config: _status(reachable=False, missing=("x",))
    )
    with pytest.raises(ClickException):
        cli._require_ollama(Config(data_dir=tmp_path))


def test_require_ollama_passes_when_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        dependencies, "check_ollama", lambda _config: _status(reachable=True, missing=())
    )
    # Should not raise.
    cli._require_ollama(Config(data_dir=tmp_path))
