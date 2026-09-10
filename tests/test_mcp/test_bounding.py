"""Tests that the read tools bound their output and report coverage."""

from __future__ import annotations

from tests.test_mcp.conftest import WikiToolsHarness


def test_list_pages_pages_and_reports_total(wiki_harness: WikiToolsHarness) -> None:
    concepts = wiki_harness.wiki_dir / "concepts"
    concepts.mkdir(parents=True)
    for i in range(25):
        (concepts / f"p{i:02d}.md").write_text(
            f"---\ntitle: Page {i:02d}\ntype: concept\n---\nBody.\n", encoding="utf-8"
        )
    wiki_harness.tools.ensure_synced()

    first = wiki_harness.tools.list_pages(limit=10, offset=0)
    assert first.count("\n- ") + first.startswith("- ") == 10  # 10 list rows
    assert "of 25" in first
    assert "offset=10" in first

    last = wiki_harness.tools.list_pages(limit=10, offset=20)
    assert "of 25" in last
    assert "offset=" not in last.split("showing")[-1]  # final page: no next offset


def test_get_sources_caps_number_of_sources(wiki_harness: WikiToolsHarness) -> None:
    refs = []
    for i in range(15):
        src = wiki_harness.raw_dir / "documents" / f"s{i:02d}.md"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(f"SOURCE {i:02d}", encoding="utf-8")
        refs.append(f'  - "raw/documents/s{i:02d}.md"')
    page = wiki_harness.wiki_dir / "concepts" / "topic.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntitle: topic\ntype: concept\nsources:\n" + "\n".join(refs) + "\n---\nBody.\n",
        encoding="utf-8",
    )

    out = wiki_harness.tools.get_sources("topic", limit=5)
    assert out.count("### ") == 5
    assert "of 15" in out
