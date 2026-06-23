"""Tests for the compilation agent's sandboxed tool executor and history compaction."""

from __future__ import annotations

from pathlib import Path

from second_brain.compilation.agent import (
    _MAX_READ_CHARS,
    WikiToolExecutor,
    compact_history,
)


def _executor(tmp_path: Path) -> tuple[WikiToolExecutor, Path, Path]:
    wiki = tmp_path / "wiki"
    raw = tmp_path / "raw"
    (wiki / "concepts").mkdir(parents=True)
    (raw / "chatgpt").mkdir(parents=True)
    return WikiToolExecutor(wiki, raw), wiki, raw


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
