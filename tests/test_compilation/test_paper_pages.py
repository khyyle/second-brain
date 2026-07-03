"""Tests for the paper content type and the flat-namespace write guard."""

from __future__ import annotations

from pathlib import Path

from second_brain.compilation.agent import (
    COMPILATION_SYSTEM_PROMPT,
    WikiToolExecutor,
)
from second_brain.wiki.structure import CONTENT_DIRS, _parse_frontmatter


def _executor(tmp_path: Path) -> tuple[WikiToolExecutor, Path]:
    wiki = tmp_path / "wiki"
    raw = tmp_path / "raw"
    wiki.mkdir()
    raw.mkdir()
    return WikiToolExecutor(wiki, raw, sources=["research-papers/nice.md"]), wiki


def test_prompt_carries_paper_taxonomy_and_rule() -> None:
    assert "papers/ — one page per academic paper" in COMPILATION_SYSTEM_PROMPT
    assert "never skipped as already covered" in COMPILATION_SYSTEM_PROMPT


def test_papers_is_a_content_dir() -> None:
    assert "papers" in CONTENT_DIRS


def test_write_page_places_papers_with_authors_and_year(tmp_path: Path) -> None:
    executor, wiki = _executor(tmp_path)

    out = executor.execute(
        "write_page",
        {
            "type": "paper",
            "title": "NICE (Dinh 2015)",
            "authors": ["Laurent Dinh", "David Krueger", "Yoshua Bengio"],
            "year": 2015,
            "related": ["[[additive-coupling-layer]]"],
            "body": "# NICE\n\nThe argument.",
        },
    )

    assert "papers/nice-dinh-2015.md" in out
    fm = _parse_frontmatter((wiki / "papers" / "nice-dinh-2015.md").read_text(encoding="utf-8"))
    assert fm["type"] == "paper"
    assert fm["authors"] == ["Laurent Dinh", "David Krueger", "Yoshua Bengio"]
    assert fm["year"] == 2015
    assert fm["related"] == ["[[additive-coupling-layer]]"]


def test_write_page_refuses_stem_taken_by_another_folder(tmp_path: Path) -> None:
    # Stems are the whole link namespace, so a paper may not shadow a concept.
    executor, wiki = _executor(tmp_path)
    (wiki / "concepts").mkdir()
    (wiki / "concepts" / "nice.md").write_text("---\ntitle: NICE\n---\nbody", encoding="utf-8")

    out = executor.execute("write_page", {"type": "paper", "title": "NICE", "body": "b"})

    assert out.startswith("Error")
    assert "concepts/nice.md" in out
    assert not (wiki / "papers" / "nice.md").exists()
