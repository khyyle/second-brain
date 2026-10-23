"""Compilation writes preserve metadata and receive referenced topic names."""

from pathlib import Path

import pytest

from second_brain.compilation.agent import WikiToolExecutor, build_compilation_prompt
from second_brain.wiki.structure import (
    _parse_frontmatter,
    build_link_graph,
    discover_all_pages,
    rank_gaps,
    serialize_page,
)


@pytest.fixture
def executor(tmp_path: Path) -> WikiToolExecutor:
    wiki = tmp_path / "wiki"
    raw = tmp_path / "raw"
    (wiki / "concepts").mkdir(parents=True)
    raw.mkdir()
    return WikiToolExecutor(wiki, raw)


@pytest.mark.parametrize("domains", [None, [], [""], [" "], "physics", [1]])
def test_write_page_rejects_invalid_domains(
    executor: WikiToolExecutor, tmp_path: Path, domains: object
) -> None:
    response = executor.execute(
        "write_page",
        {"type": "concept", "title": "Energy", "body": "Body.", "domains": domains},
    )

    assert response.startswith("Error: domains")
    assert not (tmp_path / "wiki/concepts/energy.md").exists()
    assert not executor.changes


def test_write_page_requires_domains(executor: WikiToolExecutor) -> None:
    response = executor.execute(
        "write_page", {"type": "concept", "title": "Energy", "body": "Body."}
    )

    assert response.startswith("Error: domains")
    assert not executor.changes


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("old", ["title: Energy", "raw/notes.md", "---\n\nBody"])
def test_edit_file_rejects_frontmatter_changes(tmp_path: Path, dry_run: bool, old: str) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "concepts/energy.md"
    page.parent.mkdir(parents=True)
    original = serialize_page(
        {
            "title": "Energy",
            "type": "concept",
            "domains": ["physics"],
            "sources": ["raw/notes.md"],
        },
        "Body.",
    )
    page.write_text(original, encoding="utf-8")
    executor = WikiToolExecutor(wiki, tmp_path / "raw", dry_run=dry_run)

    response = executor.execute(
        "edit_file", {"path": "./concepts/energy.md", "old_string": old, "new_string": "Changed"}
    )

    assert "set_page_meta" in response
    assert page.read_text(encoding="utf-8") == original
    assert not executor.changes


def test_edit_file_can_still_edit_non_page_files(
    executor: WikiToolExecutor, tmp_path: Path
) -> None:
    schema = tmp_path / "wiki/_meta/topic_schema.yaml"
    schema.parent.mkdir()
    schema.write_text("domains: {}\n", encoding="utf-8")

    response = executor.execute(
        "edit_file",
        {"path": "_meta/topic_schema.yaml", "old_string": "{}", "new_string": "{physics: {}}"},
    )

    assert response.startswith("Edited")
    assert schema.read_text(encoding="utf-8") == "domains: {physics: {}}\n"


def test_set_page_meta_can_leave_a_page_uncategorized(
    executor: WikiToolExecutor, tmp_path: Path
) -> None:
    page = tmp_path / "wiki/concepts/energy.md"
    original = serialize_page({"title": "Energy", "domains": ["physics"]}, "Body.")
    page.write_text(original, encoding="utf-8")

    response = executor.execute("set_page_meta", {"slug": "energy", "domains": []})

    assert response.startswith("Updated frontmatter")
    assert _parse_frontmatter(page.read_text(encoding="utf-8"))["domains"] == []


@pytest.mark.parametrize("title", ["Energy", "Dark Energy"])
def test_write_page_does_not_infer_topic_identity_from_shared_words(
    executor: WikiToolExecutor, tmp_path: Path, title: str
) -> None:
    concepts = tmp_path / "wiki/concepts"
    for stem in ["one", "two"]:
        (concepts / f"{stem}.md").write_text("See [[energy]].", encoding="utf-8")
    arguments = {"type": "concept", "title": title, "domains": ["physics"], "body": "Body."}

    response = executor.execute("write_page", arguments)

    assert response.startswith("Wrote")
    assert len(executor.changes) == 1


def test_prompt_prioritizes_existing_gap_names_and_limits_the_list(tmp_path: Path) -> None:
    concepts = tmp_path / "wiki/concepts"
    concepts.mkdir(parents=True)
    links = " ".join(f"[[gap-{index:02}]]" for index in range(31))
    (concepts / "one.md").write_text(f"{links} [[popular]]", encoding="utf-8")
    (concepts / "two.md").write_text("See [[popular]] and [[gap-00]].", encoding="utf-8")

    pages = discover_all_pages(tmp_path / "wiki")
    prompt = build_compilation_prompt(
        ["notes.md"], gaps=rank_gaps(pages, build_link_graph(pages)), domains=[]
    )

    assert "raw/notes.md" in prompt
    assert "reuse its exact name" in prompt
    assert prompt.index("[[gap-00]]") < prompt.index("[[gap-01]]")
    assert "[[popular]]" in prompt
    assert "[[gap-28]]" in prompt
    assert "[[gap-29]]" not in prompt
