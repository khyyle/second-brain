from __future__ import annotations

from pathlib import Path

import pytest

from second_brain.config import Config
from second_brain.ingestion.manifest import Manifest


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """Isolated default Config rooted under a temporary directory."""
    cfg = Config(data_dir=tmp_path / "second-brain")
    cfg.ensure_directories()
    return cfg


@pytest.fixture
def manifest(config: Config) -> Manifest:
    """Fresh manifest matching the test's isolated config."""
    return Manifest(config.manifest_db_path)


@pytest.fixture(autouse=True)
def _block_unmocked_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure tests fail immediately if an unmocked HTTP request is attempted."""

    def _fail_network(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Unmocked network request attempted in test suite!")

    monkeypatch.setattr("httpx.Client.request", _fail_network)
    monkeypatch.setattr("httpx.AsyncClient.request", _fail_network)


@pytest.fixture(autouse=True)
def _isolate_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate Git operations in tests from user config and host environment."""
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test Author")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test Committer")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
