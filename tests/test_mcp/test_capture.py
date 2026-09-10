"""Tests for capture_note: chat content enters the pipeline as a source."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.test_mcp.conftest import WikiToolsHarness


def test_capture_writes_markdown_to_drops_documents(wiki_harness: WikiToolsHarness) -> None:
    msg = wiki_harness.tools.capture_note(
        "Bonds with embedded options need OAS, not YTM.", title="OAS vs YTM"
    )

    drops = wiki_harness.data_dir / "drops" / "documents"
    files = list(drops.glob("*.md"))
    assert len(files) == 1
    assert "drops/documents" in msg

    text = files[0].read_text(encoding="utf-8")
    assert "origin: chat-capture" in text
    assert 'title: "OAS vs YTM"' in text
    assert "Bonds with embedded options need OAS" in text


def test_capture_triggers_ingest_of_the_written_file(wiki_harness: WikiToolsHarness) -> None:
    wiki_harness.tools.capture_note("A note worth keeping.", title="keepable")
    written = next((wiki_harness.data_dir / "drops" / "documents").glob("*.md"))
    assert wiki_harness.ingested == [written]


def test_capture_strips_a_leading_frontmatter_block(wiki_harness: WikiToolsHarness) -> None:
    content = (
        "---\ntitle: Agent Wrote A Page\ntype: concept\n---\n\nThe actual insight is in the body."
    )
    wiki_harness.tools.capture_note(content, title="real title")
    text = next((wiki_harness.data_dir / "drops" / "documents").glob("*.md")).read_text(
        encoding="utf-8"
    )

    # only the capture header survives, not the agent's frontmatter (no doubling)
    assert text.count("---") == 2
    assert "type: concept" not in text
    assert 'title: "real title"' in text
    assert "The actual insight is in the body." in text


def test_capture_records_topic_hint(wiki_harness: WikiToolsHarness) -> None:
    wiki_harness.tools.capture_note("Some content.", title="t", topic="fixed-income")
    text = next((wiki_harness.data_dir / "drops" / "documents").glob("*.md")).read_text(
        encoding="utf-8"
    )
    assert 'suggested_topic: "fixed-income"' in text


def test_capture_defaults_title_to_first_line(wiki_harness: WikiToolsHarness) -> None:
    wiki_harness.tools.capture_note("First line is the title\n\nbody here")
    files = list((wiki_harness.data_dir / "drops" / "documents").glob("*.md"))
    assert any("first-line-is-the-title" in f.name for f in files)


def test_capture_rejects_empty(wiki_harness: WikiToolsHarness) -> None:
    assert "Nothing to capture" in wiki_harness.tools.capture_note("   ")
    assert list((wiki_harness.data_dir / "drops" / "documents").glob("*.md")) == []
    assert wiki_harness.ingested == []


def test_capture_rejects_frontmatter_only_content(wiki_harness: WikiToolsHarness) -> None:
    msg = wiki_harness.tools.capture_note("---\ntitle: just a header\n---\n")
    assert "Nothing to capture" in msg
    assert list((wiki_harness.data_dir / "drops" / "documents").glob("*.md")) == []
    assert wiki_harness.ingested == []
