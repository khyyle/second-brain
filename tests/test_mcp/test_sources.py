"""Tests for raw source-file resolution behind get_sources."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.test_mcp.conftest import WikiToolsHarness


def _write_page(wiki: Path, stem: str, source_ref: str) -> None:
    path = wiki / "concepts" / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\ntitle: {stem}\ntype: concept\nsources:\n  - "{source_ref}"\n---\nBody.\n',
        encoding="utf-8",
    )


def test_get_sources_resolves_data_dir_relative_path(wiki_harness: WikiToolsHarness) -> None:
    source = wiki_harness.raw_dir / "documents" / "swaps and etfs.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("SWAP NOTES", encoding="utf-8")
    # Frontmatter stores the path relative to the data dir, with raw/ prefix.
    _write_page(wiki_harness.wiki_dir, "equity-swaps", "raw/documents/swaps and etfs.md")

    out = wiki_harness.tools.get_sources("equity-swaps")
    assert "SWAP NOTES" in out
    assert "source file not found" not in out


def test_get_sources_falls_back_to_basename(wiki_harness: WikiToolsHarness) -> None:
    source = wiki_harness.raw_dir / "chatgpt" / "deep-dive-123.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("CHAT NOTES", encoding="utf-8")
    # Frontmatter records only the bare filename.
    _write_page(wiki_harness.wiki_dir, "topic", "deep-dive-123.md")

    out = wiki_harness.tools.get_sources("topic")
    assert "CHAT NOTES" in out


def test_get_sources_reports_missing(wiki_harness: WikiToolsHarness) -> None:
    _write_page(wiki_harness.wiki_dir, "topic", "raw/documents/nope.md")
    assert "source file not found" in wiki_harness.tools.get_sources("topic")
