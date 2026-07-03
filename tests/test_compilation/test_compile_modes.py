"""Tests for per-source compile modes: config, resolution, and prompt assembly."""

from __future__ import annotations

from pathlib import Path

import pytest

from second_brain.compilation.agent import (
    COMPILATION_SYSTEM_PROMPT,
    REFERENCE_MODE_ADDENDUM,
    WikiToolExecutor,
    build_system_prompt,
)
from second_brain.compilation.compiler import _build_work_units, _resolve_compile_mode
from second_brain.config import Config, SourceConfig
from second_brain.wiki.structure import _parse_frontmatter


def test_source_config_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="compile_mode"):
        SourceConfig(path="/tmp/x", compile_mode="summarize")


def _config_with_sources(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path / "sb",
        sources={
            "chatgpt": SourceConfig(path=tmp_path / "chats"),
            "research-papers": SourceConfig(path=tmp_path / "papers", compile_mode="reference"),
        },
    )


def test_resolve_compile_mode_uses_source_folder(tmp_path: Path) -> None:
    config = _config_with_sources(tmp_path)

    assert _resolve_compile_mode(config, ["research-papers/glow.md"]) == "reference"
    assert _resolve_compile_mode(config, ["chatgpt/a.md", "chatgpt/b.md"]) == "synthesize"


def test_resolve_compile_mode_defaults_for_unknown_folder(tmp_path: Path) -> None:
    config = _config_with_sources(tmp_path)

    assert _resolve_compile_mode(config, ["legacy-folder/x.md"]) == "synthesize"


def test_work_units_never_mix_source_folders(tmp_path: Path) -> None:
    # Mode resolution reads only a unit's first path, which is sound as long
    # as grouping keeps every unit within one source folder.
    config = _config_with_sources(tmp_path)
    raw = config.raw_dir
    for rel in ("chatgpt/a.md", "chatgpt/b.md", "research-papers/glow.md"):
        (raw / rel).parent.mkdir(parents=True, exist_ok=True)
        (raw / rel).write_text("body", encoding="utf-8")

    staged = ["chatgpt/a.md", "chatgpt/b.md", "research-papers/glow.md"]
    units = _build_work_units(config, raw, staged)

    for unit in units:
        folders = {rel.split("/", 1)[0] for rel in unit}
        assert len(folders) == 1, f"unit mixes source folders: {unit}"


def test_build_system_prompt_appends_addendum_only_for_reference() -> None:
    assert build_system_prompt("synthesize") == COMPILATION_SYSTEM_PROMPT
    reference = build_system_prompt("reference")
    assert reference.startswith(COMPILATION_SYSTEM_PROMPT)
    assert REFERENCE_MODE_ADDENDUM in reference


def test_write_page_carries_authors_and_year(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    raw = tmp_path / "raw"
    wiki.mkdir()
    raw.mkdir()
    executor = WikiToolExecutor(wiki, raw, sources=["research-papers/glow.md"])

    executor.execute(
        "write_page",
        {
            "type": "concept",
            "title": "Glow",
            "authors": ["Kingma", "Dhariwal"],
            "year": 2018,
            "body": "# Glow\n\nBody.",
        },
    )

    fm = _parse_frontmatter((wiki / "concepts" / "glow.md").read_text(encoding="utf-8"))
    assert fm["authors"] == ["Kingma", "Dhariwal"]
    assert fm["year"] == 2018
