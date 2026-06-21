"""Tests for raw source-file resolution behind get_sources."""

from __future__ import annotations

from pathlib import Path

from second_brain.config import SearchConfig
from second_brain.mcp_server.search import SearchIndex
from second_brain.mcp_server.tools import WikiTools


def _make_tools(tmp_path: Path) -> tuple[WikiTools, Path]:
    data_dir = tmp_path / "data"
    raw = data_dir / "raw"
    wiki = data_dir / "wiki"
    raw.mkdir(parents=True)
    wiki.mkdir(parents=True)
    index = SearchIndex(data_dir / "search.db", SearchConfig(semantic_enabled=False))
    return WikiTools(wiki, raw, index), data_dir


def _write_page(wiki: Path, stem: str, source_ref: str) -> None:
    path = wiki / "concepts" / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\ntitle: {stem}\ntype: concept\nsources:\n  - "{source_ref}"\n---\nBody.\n',
        encoding="utf-8",
    )


def test_get_sources_resolves_data_dir_relative_path(tmp_path: Path) -> None:
    tools, data_dir = _make_tools(tmp_path)
    source = data_dir / "raw" / "documents" / "swaps and etfs.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("SWAP NOTES", encoding="utf-8")
    # Frontmatter stores the path relative to the data dir, with raw/ prefix.
    _write_page(tools._wiki, "equity-swaps", "raw/documents/swaps and etfs.md")

    out = tools.get_sources("equity-swaps")
    assert "SWAP NOTES" in out
    assert "source file not found" not in out


def test_get_sources_falls_back_to_basename(tmp_path: Path) -> None:
    tools, data_dir = _make_tools(tmp_path)
    source = data_dir / "raw" / "chatgpt" / "deep-dive-123.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("CHAT NOTES", encoding="utf-8")
    # Frontmatter records only the bare filename.
    _write_page(tools._wiki, "topic", "deep-dive-123.md")

    out = tools.get_sources("topic")
    assert "CHAT NOTES" in out


def test_get_sources_reports_missing(tmp_path: Path) -> None:
    tools, _ = _make_tools(tmp_path)
    _write_page(tools._wiki, "topic", "raw/documents/nope.md")
    assert "source file not found" in tools.get_sources("topic")
