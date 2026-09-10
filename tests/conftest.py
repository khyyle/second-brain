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
