"""Tests that the read tools bound their output and report coverage."""

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


def test_list_pages_pages_and_reports_total(tmp_path: Path) -> None:
    tools, _ = _make_tools(tmp_path)
    concepts = tools._wiki / "concepts"
    concepts.mkdir(parents=True)
    for i in range(25):
        (concepts / f"p{i:02d}.md").write_text(
            f"---\ntitle: Page {i:02d}\ntype: concept\n---\nBody.\n", encoding="utf-8"
        )
    tools.ensure_synced()

    first = tools.list_pages(limit=10, offset=0)
    assert first.count("\n- ") + first.startswith("- ") == 10  # 10 list rows
    assert "of 25" in first
    assert "offset=10" in first

    last = tools.list_pages(limit=10, offset=20)
    assert "of 25" in last
    assert "offset=" not in last.split("showing")[-1]  # final page: no next offset


def test_get_sources_caps_number_of_sources(tmp_path: Path) -> None:
    tools, data_dir = _make_tools(tmp_path)
    refs = []
    for i in range(15):
        src = data_dir / "raw" / "documents" / f"s{i:02d}.md"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(f"SOURCE {i:02d}", encoding="utf-8")
        refs.append(f'  - "raw/documents/s{i:02d}.md"')
    page = tools._wiki / "concepts" / "topic.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntitle: topic\ntype: concept\nsources:\n" + "\n".join(refs) + "\n---\nBody.\n",
        encoding="utf-8",
    )

    out = tools.get_sources("topic", limit=5)
    assert out.count("### ") == 5
    assert "of 15" in out
