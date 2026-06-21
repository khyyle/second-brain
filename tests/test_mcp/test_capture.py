"""Tests for capture_note: chat content enters the pipeline as a source."""

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


def test_capture_writes_markdown_to_drops_documents(tmp_path: Path) -> None:
    tools, data_dir = _make_tools(tmp_path)
    msg = tools.capture_note("Bonds with embedded options need OAS, not YTM.", title="OAS vs YTM")

    drops = data_dir / "drops" / "documents"
    files = list(drops.glob("*.md"))
    assert len(files) == 1
    assert "drops/documents" in msg

    text = files[0].read_text(encoding="utf-8")
    assert "origin: chat-capture" in text
    assert 'title: "OAS vs YTM"' in text
    assert "Bonds with embedded options need OAS" in text


def test_capture_records_topic_hint(tmp_path: Path) -> None:
    tools, data_dir = _make_tools(tmp_path)
    tools.capture_note("Some content.", title="t", topic="fixed-income")
    text = next((data_dir / "drops" / "documents").glob("*.md")).read_text(encoding="utf-8")
    assert 'suggested_topic: "fixed-income"' in text


def test_capture_defaults_title_to_first_line(tmp_path: Path) -> None:
    tools, data_dir = _make_tools(tmp_path)
    tools.capture_note("First line is the title\n\nbody here")
    files = list((data_dir / "drops" / "documents").glob("*.md"))
    assert any("first-line-is-the-title" in f.name for f in files)


def test_capture_rejects_empty(tmp_path: Path) -> None:
    tools, data_dir = _make_tools(tmp_path)
    assert "Nothing to capture" in tools.capture_note("   ")
    assert list((data_dir / "drops" / "documents").glob("*.md")) == []
