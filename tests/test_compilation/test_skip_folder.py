"""The skipped-source holding folder is excluded from builds."""

from __future__ import annotations

from second_brain.compilation import compiler
from second_brain.config import Config
from second_brain.ingestion.manifest import Manifest


def test_find_new_sources_ignores_skipped(config: Config, manifest: Manifest) -> None:
    (config.raw_dir / "documents").mkdir(parents=True, exist_ok=True)
    (config.raw_dir / "documents" / "keep.md").write_text("x", encoding="utf-8")
    skipped = config.raw_dir / ".skipped" / "chatgpt"
    skipped.mkdir(parents=True, exist_ok=True)
    (skipped / "junk.md").write_text("y", encoding="utf-8")

    sources = compiler.find_new_sources(config, manifest)

    assert "documents/keep.md" in sources
    assert all(".skipped" not in source for source in sources)
