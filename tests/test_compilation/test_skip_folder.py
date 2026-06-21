"""The skipped-source holding folder is excluded from builds and purged."""

from __future__ import annotations

from pathlib import Path

from second_brain.compilation import compiler
from second_brain.config import Config
from second_brain.ingestion.manifest import Manifest


def _config(tmp_path: Path) -> Config:
    config = Config(data_dir=tmp_path / "sb")
    config.ensure_directories()
    return config


def test_find_new_sources_ignores_skipped(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manifest = Manifest(config.manifest_db_path)

    (config.raw_dir / "documents").mkdir(parents=True, exist_ok=True)
    (config.raw_dir / "documents" / "keep.md").write_text("x", encoding="utf-8")
    skipped = config.raw_dir / ".skipped" / "chatgpt"
    skipped.mkdir(parents=True, exist_ok=True)
    (skipped / "junk.md").write_text("y", encoding="utf-8")

    sources = compiler._find_new_sources(config, manifest)

    assert "documents/keep.md" in sources
    assert all(".skipped" not in source for source in sources)


def test_purge_skipped_removes_folder(tmp_path: Path) -> None:
    config = _config(tmp_path)
    skipped = config.raw_dir / ".skipped" / "chatgpt"
    skipped.mkdir(parents=True, exist_ok=True)
    (skipped / "junk.md").write_text("y", encoding="utf-8")

    compiler._purge_skipped(config.raw_dir)

    assert not (config.raw_dir / ".skipped").exists()


def test_purge_skipped_noop_when_absent(tmp_path: Path) -> None:
    config = _config(tmp_path)
    compiler._purge_skipped(config.raw_dir)  # must not raise
