"""Tests for the compilation agent's sandboxed tool executor and history compaction."""

from __future__ import annotations

from pathlib import Path

from second_brain.compilation.agent import (
    _MAX_READ_CHARS,
    _PROTECTED_SOURCE_CHARS,
    WikiToolExecutor,
    build_source_block,
    compact_history,
)
from second_brain.wiki.structure import _parse_frontmatter


def _executor(tmp_path: Path) -> tuple[WikiToolExecutor, Path, Path]:
    wiki = tmp_path / "wiki"
    raw = tmp_path / "raw"
    (wiki / "concepts").mkdir(parents=True)
    (raw / "chatgpt").mkdir(parents=True)
    return WikiToolExecutor(wiki, raw), wiki, raw


def test_write_file_tool_is_removed(tmp_path: Path) -> None:
    executor, wiki, _raw = _executor(tmp_path)

    out = executor.execute("write_file", {"path": "concepts/foo.md", "content": "x"})

    assert out.startswith("Unknown tool")
    assert not (wiki / "concepts" / "foo.md").exists()


def test_write_page_path_stays_flat(tmp_path: Path) -> None:
    # A slash in the title is dropped by slugification, so a page can never land
    # in a nested folder.
    wiki = tmp_path / "wiki"
    raw = tmp_path / "raw"
    wiki.mkdir()
    raw.mkdir()
    executor = WikiToolExecutor(wiki, raw, sources=["s.md"])

    executor.execute("write_page", {"type": "concept", "title": "Sub/Folder Attempt", "body": "b"})

    assert (wiki / "concepts" / "subfolder-attempt.md").exists()
    assert not (wiki / "concepts" / "sub").exists()


def test_write_page_creates_valid_page_and_stamps_sources(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    raw = tmp_path / "raw"
    wiki.mkdir()
    raw.mkdir()
    executor = WikiToolExecutor(wiki, raw, sources=["documents/inference-modeling.md"])

    out = executor.execute(
        "write_page",
        {
            "type": "concept",
            "title": "Point Estimation",
            "domains": ["mathematics"],
            "prerequisites": ["[[statistical-models]]"],
            "body": "# Point Estimation\n\nBody.",
        },
    )

    assert "concepts/point-estimation.md" in out
    fm = _parse_frontmatter(
        (wiki / "concepts" / "point-estimation.md").read_text(encoding="utf-8")
    )
    assert fm["title"] == "Point Estimation"
    assert fm["type"] == "concept"
    assert fm["domains"] == ["mathematics"]
    assert fm["prerequisites"] == ["[[statistical-models]]"]
    # provenance is stamped by the executor, not the agent
    assert fm["sources"] == ["raw/documents/inference-modeling.md"]


def test_write_page_rejects_unknown_type(tmp_path: Path) -> None:
    executor, _wiki, _raw = _executor(tmp_path)
    out = executor.execute("write_page", {"type": "essay", "title": "X", "body": "y"})
    assert out.startswith("Error")


def test_write_page_refuses_to_overwrite_existing(tmp_path: Path) -> None:
    executor, wiki, _raw = _executor(tmp_path)
    (wiki / "concepts" / "foo.md").write_text(
        "---\ntitle: Foo\ntype: concept\n---\n", encoding="utf-8"
    )

    out = executor.execute("write_page", {"type": "concept", "title": "Foo", "body": "y"})

    assert out.startswith("Error")
    assert "exists" in out


def test_set_page_meta_merges_frontmatter_and_keeps_body(tmp_path: Path) -> None:
    executor, wiki, _raw = _executor(tmp_path)
    page = wiki / "concepts" / "foo.md"
    page.write_text(
        "---\ntitle: Foo\ntype: concept\ndomains:\n- a\n---\n\n# Foo\n\nBody.\n", encoding="utf-8"
    )

    out = executor.execute(
        "set_page_meta", {"slug": "foo", "domains": ["b"], "related": ["[[bar]]"]}
    )

    assert "foo" in out
    text = page.read_text(encoding="utf-8")
    fm = _parse_frontmatter(text)
    assert fm["domains"] == ["b"]  # replaced wholesale
    assert fm["related"] == ["[[bar]]"]  # added
    assert fm["title"] == "Foo"  # untouched
    assert "# Foo\n\nBody." in text  # body untouched


def test_set_page_meta_ignores_system_managed_fields(tmp_path: Path) -> None:
    executor, wiki, _raw = _executor(tmp_path)
    page = wiki / "concepts" / "foo.md"
    page.write_text(
        "---\ntitle: Foo\ntype: concept\nsources:\n- raw/documents/old.md\n---\n\nBody.\n",
        encoding="utf-8",
    )

    executor.execute(
        "set_page_meta",
        {"slug": "foo", "sources": ["raw/evil.md"], "title": "Hacked", "tags": ["x"]},
    )

    fm = _parse_frontmatter(page.read_text(encoding="utf-8"))
    assert fm["sources"] == ["raw/documents/old.md"]  # provenance is not agent-settable
    assert fm["title"] == "Foo"  # nor the title
    assert fm["tags"] == ["x"]  # ordinary fields still apply


def test_finalize_unions_sources_into_updated_pages(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    raw = tmp_path / "raw"
    (wiki / "concepts").mkdir(parents=True)
    raw.mkdir()
    executor = WikiToolExecutor(wiki, raw, sources=["chatgpt/new.md"])
    page = wiki / "concepts" / "foo.md"
    page.write_text(
        "---\ntitle: Foo\ntype: concept\nsources:\n- raw/documents/old.md\n---\n\nBody.\n",
        encoding="utf-8",
    )

    executor.execute(
        "edit_file",
        {"path": "concepts/foo.md", "old_string": "Body.", "new_string": "Body. More."},
    )
    executor.finalize_provenance()

    fm = _parse_frontmatter(page.read_text(encoding="utf-8"))
    assert fm["sources"] == [
        "raw/documents/old.md",
        "raw/chatgpt/new.md",
    ]  # additive, no regression


def test_finalize_is_idempotent_for_created_pages(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    raw = tmp_path / "raw"
    wiki.mkdir()
    raw.mkdir()
    executor = WikiToolExecutor(wiki, raw, sources=["chatgpt/new.md"])

    executor.execute("write_page", {"type": "concept", "title": "Bar", "body": "b"})
    executor.finalize_provenance()

    fm = _parse_frontmatter((wiki / "concepts" / "bar.md").read_text(encoding="utf-8"))
    assert fm["sources"] == ["raw/chatgpt/new.md"]  # unchanged: write_page already stamped it


def test_read_file_pages_long_source(tmp_path: Path) -> None:
    executor, _wiki, raw = _executor(tmp_path)
    (raw / "chatgpt" / "big.md").write_text("A" * (_MAX_READ_CHARS + 500), encoding="utf-8")

    first = executor.execute("read_file", {"path": "raw/chatgpt/big.md"})
    assert f"offset={_MAX_READ_CHARS}" in first

    rest = executor.execute("read_file", {"path": "raw/chatgpt/big.md", "offset": _MAX_READ_CHARS})
    assert "end of file" in rest


def test_grep_searches_raw_sources(tmp_path: Path) -> None:
    executor, wiki, raw = _executor(tmp_path)
    (raw / "chatgpt" / "a.md").write_text("hello NTKGP world", encoding="utf-8")
    (wiki / "concepts" / "p.md").write_text("unrelated", encoding="utf-8")

    out = executor.execute("grep_files", {"pattern": "NTKGP", "glob": "raw/chatgpt/a.md"})
    assert "raw/chatgpt/a.md:1: hello NTKGP world" == out


def test_grep_defaults_to_wiki(tmp_path: Path) -> None:
    executor, wiki, _raw = _executor(tmp_path)
    (wiki / "concepts" / "p.md").write_text("foo bar", encoding="utf-8")

    out = executor.execute("grep_files", {"pattern": "foo"})
    assert "concepts/p.md:1: foo bar" == out


def test_glob_lists_raw_sources(tmp_path: Path) -> None:
    executor, _wiki, raw = _executor(tmp_path)
    (raw / "chatgpt" / "a.md").write_text("x", encoding="utf-8")

    out = executor.execute("glob_files", {"pattern": "raw/chatgpt/*.md"})
    assert out == "raw/chatgpt/a.md"


class _FakeToolUse:
    type = "tool_use"

    def __init__(self, block_id: str, name: str, tool_input: dict) -> None:
        self.id = block_id
        self.name = name
        self.input = tool_input


def _assistant(block_id: str, name: str, tool_input: dict) -> dict:
    return {"role": "assistant", "content": [_FakeToolUse(block_id, name, tool_input)]}


def _tool_result(tool_use_id: str, content: str) -> dict:
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": content}],
    }


def test_compact_keeps_only_latest_source_read(tmp_path: Path) -> None:
    big = "X" * 1000
    messages = [
        {"role": "user", "content": "prompt"},
        _assistant("t1", "read_file", {"path": "raw/chatgpt/a.md"}),
        _tool_result("t1", big),
        _assistant("t3", "read_file", {"path": "raw/chatgpt/a.md"}),
        _tool_result("t3", big),
        _assistant("t4", "glob_files", {"pattern": "*.md"}),
        _tool_result("t4", big),
    ]

    compact_history(messages, keep_last=1)

    # Older duplicate source read is collapsed; the latest is preserved.
    assert messages[2]["content"][0]["content"] != big
    assert messages[4]["content"][0]["content"] == big
    # Most recent user turn stays intact regardless of content.
    assert messages[6]["content"][0]["content"] == big


def test_compact_keeps_every_page_of_a_paged_source() -> None:
    big = "X" * 1000
    messages = [
        {"role": "user", "content": "prompt"},
        _assistant("p1", "read_file", {"path": "raw/chatgpt/a.md", "offset": 0}),
        _tool_result("p1", big),
        _assistant("p2", "read_file", {"path": "raw/chatgpt/a.md", "offset": 24000}),
        _tool_result("p2", big),
        _assistant("g1", "glob_files", {"pattern": "*.md"}),
        _tool_result("g1", big),
    ]

    compact_history(messages, keep_last=1)

    # Both pages of the same source survive; only the unrelated old result is cut.
    assert messages[2]["content"][0]["content"] == big
    assert messages[4]["content"][0]["content"] == big


def test_compact_bounds_protected_source_reads_by_char_budget() -> None:
    # Three distinct source pages at ~40% of the protection budget each, so only
    # the two newest fit; the oldest must be compacted despite being a
    # latest-per-page read.
    page = "Y" * (_PROTECTED_SOURCE_CHARS * 2 // 5)
    messages = [
        {"role": "user", "content": "prompt"},
        _assistant("r1", "read_file", {"path": "raw/chatgpt/a.md", "offset": 0}),
        _tool_result("r1", page),
        _assistant("r2", "read_file", {"path": "raw/chatgpt/b.md", "offset": 0}),
        _tool_result("r2", page),
        _assistant("r3", "read_file", {"path": "raw/chatgpt/c.md", "offset": 0}),
        _tool_result("r3", page),
        _assistant("done", "glob_files", {"pattern": "*.md"}),
        _tool_result("done", "x"),
    ]

    compact_history(messages, keep_last=1)

    # Oldest read is evicted; the two newest stay within budget.
    assert messages[2]["content"][0]["content"] != page
    assert messages[4]["content"][0]["content"] == page
    assert messages[6]["content"][0]["content"] == page


def test_build_source_block_labels_each_source(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "chatgpt"
    raw.mkdir(parents=True)
    (raw / "a.md").write_text("alpha body", encoding="utf-8")
    (raw / "b.md").write_text("beta body", encoding="utf-8")

    block = build_source_block(["chatgpt/a.md", "chatgpt/b.md"], tmp_path / "raw")

    assert "=== raw/chatgpt/a.md ===" in block
    assert "alpha body" in block
    assert "=== raw/chatgpt/b.md ===" in block
    assert "beta body" in block
