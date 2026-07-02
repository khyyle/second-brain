"""Tests for wiki-repo git operations: log, rollback, and rename repair."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from second_brain.wiki.repo import (
    WikiRepoError,
    commit_all,
    count_commits,
    detect_renames,
    list_commits,
    merge_pages,
    repair_links,
    rollback,
)
from second_brain.wiki.structure import _parse_frontmatter, strip_frontmatter


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init_wiki(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki"
    (wiki / "concepts").mkdir(parents=True)
    _git(wiki, "init")
    _git(wiki, "config", "user.email", "test@test")
    _git(wiki, "config", "user.name", "test")
    return wiki


def _commit_page(wiki: Path, stem: str, sources: list[str], body: str = "Body.") -> Path:
    page = wiki / "concepts" / f"{stem}.md"
    source_lines = "\n".join(f"- raw/{source}" for source in sources)
    page.write_text(
        f"---\ntitle: {stem}\ntype: concept\nsources:\n{source_lines}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    _git(wiki, "add", "-A")
    _git(wiki, "commit", "-m", f"add {stem}")
    return page


def test_list_commits_returns_newest_first_with_file_counts(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    _commit_page(wiki, "alpha", ["chatgpt/a.md"])
    _commit_page(wiki, "beta", ["chatgpt/b.md"])

    commits = list_commits(wiki, limit=5)

    assert [c.subject for c in commits] == ["add beta", "add alpha"]
    assert all(c.files_changed == 1 for c in commits)
    assert all(c.short_hash and c.date for c in commits)
    assert count_commits(wiki) == 2


def test_commit_all_checkpoints_and_skips_clean_tree(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / "concepts" / "alpha.md").write_text("Body.\n", encoding="utf-8")

    assert commit_all(wiki, "auto: test checkpoint") is True
    assert [c.subject for c in list_commits(wiki, limit=5)] == ["auto: test checkpoint"]
    # a second call on the now-clean tree is a quiet no-op
    assert commit_all(wiki, "auto: test checkpoint") is False
    assert count_commits(wiki) == 1


def test_rollback_returns_sources_and_resets(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    _commit_page(wiki, "keep", ["chatgpt/keep.md"])
    _commit_page(wiki, "undo", ["chatgpt/undo-1.md", "chatgpt/undo-2.md"])

    rolled_back_sources = rollback(wiki, count=1)

    # sources come back exactly as frontmatter records them; the caller adapts
    assert rolled_back_sources == ["raw/chatgpt/undo-1.md", "raw/chatgpt/undo-2.md"]
    assert (wiki / "concepts" / "keep.md").exists()
    assert not (wiki / "concepts" / "undo.md").exists()


def test_rollback_aborts_on_dirty_tree(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    page = _commit_page(wiki, "alpha", ["chatgpt/a.md"])
    page.write_text(page.read_text(encoding="utf-8") + "\nEdit.\n", encoding="utf-8")

    with pytest.raises(WikiRepoError, match="uncommitted"):
        rollback(wiki, count=1)


def test_rollback_refuses_to_pass_the_root_commit(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    _commit_page(wiki, "alpha", ["chatgpt/a.md"])

    with pytest.raises(WikiRepoError, match="only has"):
        rollback(wiki, count=1)


def test_detect_renames_maps_old_stem_to_new(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    page = _commit_page(wiki, "old-name", ["chatgpt/a.md"])
    page.rename(page.with_name("new-name.md"))

    mapping = detect_renames(wiki)

    assert mapping == {"old-name": "new-name"}
    # only the index was touched; the rename itself is still on disk
    assert (wiki / "concepts" / "new-name.md").exists()


def test_detect_renames_empty_on_clean_tree(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    _commit_page(wiki, "alpha", ["chatgpt/a.md"])

    assert detect_renames(wiki) == {}


def test_repair_links_repoints_body_and_frontmatter(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    linker = wiki / "concepts" / "linker.md"
    linker.write_text(
        "---\n"
        "title: Linker\n"
        "type: concept\n"
        "related:\n"
        "- '[[old-name]]'\n"
        "---\n\n"
        "See [[old-name]] and [[old-name|the old thing]].\n"
        "```python\nx = arr[[0]]\n```\n",
        encoding="utf-8",
    )

    repairs = repair_links(wiki, {"old-name": "new-name"})

    text = linker.read_text(encoding="utf-8")
    assert _parse_frontmatter(text)["related"] == ["[[new-name]]"]
    body = strip_frontmatter(text)
    assert "[[new-name]]" in body
    assert "[[new-name|the old thing]]" in body  # display label kept
    assert "arr[[0]]" in body  # code block untouched
    assert repairs[0].links_repointed == 3


def test_repair_links_dry_run_writes_nothing(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    linker = wiki / "concepts" / "linker.md"
    original = "---\ntitle: Linker\ntype: concept\n---\n\nSee [[old-name]].\n"
    linker.write_text(original, encoding="utf-8")

    repairs = repair_links(wiki, {"old-name": "new-name"}, dry_run=True)

    assert linker.read_text(encoding="utf-8") == original
    assert repairs[0].links_repointed == 1


def test_repair_links_noop_without_mapping(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)

    assert repair_links(wiki, {}) == []


def _write_page(wiki: Path, stem: str, frontmatter: str, body: str) -> Path:
    page = wiki / "concepts" / f"{stem}.md"
    page.write_text(
        f"---\ntitle: {stem}\ntype: concept\n{frontmatter}---\n\n{body}\n", encoding="utf-8"
    )
    return page


def test_merge_pages_unions_repoints_and_retires(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    keeper = _write_page(
        wiki,
        "electrolysis",
        "related:\n- '[[water-electrolysis]]'\nsources:\n- raw/chatgpt/a.md\n",
        "The kept page.",
    )
    _write_page(
        wiki,
        "water-electrolysis",
        "related:\n- '[[nernst-equation]]'\nsources:\n- raw/chatgpt/b.md\n",
        "The duplicate.",
    )
    linker = _write_page(
        wiki,
        "linker",
        "related:\n- '[[water-electrolysis]]'\n",
        "See [[water-electrolysis|splitting water]].",
    )
    _git(wiki, "add", "-A")
    _git(wiki, "commit", "-m", "seed")

    repairs = merge_pages(wiki, "electrolysis", ["water-electrolysis"])

    assert not (wiki / "concepts" / "water-electrolysis.md").exists()
    kept = _parse_frontmatter(keeper.read_text(encoding="utf-8"))
    assert kept["sources"] == ["raw/chatgpt/a.md", "raw/chatgpt/b.md"]  # provenance unioned
    # edges unioned, minus anything pointing at the merged pages themselves
    assert kept["related"] == ["[[nernst-equation]]"]
    linker_text = linker.read_text(encoding="utf-8")
    assert "[[electrolysis|splitting water]]" in linker_text  # body repointed, label kept
    assert _parse_frontmatter(linker_text)["related"] == ["[[electrolysis]]"]
    assert any(repair.rel_path == "concepts/linker.md" for repair in repairs)
    # the merge is checkpointed into the wiki history
    assert list_commits(wiki, limit=1)[0].subject == (
        "auto: merge water-electrolysis into electrolysis"
    )


def test_merge_pages_mutual_references_leave_no_self_link(tmp_path: Path) -> None:
    # Duplicates often point only at each other; the kept page must not end
    # up related to itself after the retired stem is repointed onto it.
    wiki = _init_wiki(tmp_path)
    keeper = _write_page(wiki, "electrolysis", "related:\n- '[[water-electrolysis]]'\n", "Kept.")
    _write_page(wiki, "water-electrolysis", "related:\n- '[[electrolysis]]'\n", "Retired.")
    _git(wiki, "add", "-A")
    _git(wiki, "commit", "-m", "seed")

    merge_pages(wiki, "electrolysis", ["water-electrolysis"])

    kept = _parse_frontmatter(keeper.read_text(encoding="utf-8"))
    assert kept["related"] == []


def test_merge_pages_rejects_missing_or_self(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    _write_page(wiki, "a", "", "Body.")

    with pytest.raises(WikiRepoError, match="No page found"):
        merge_pages(wiki, "a", ["ghost"])
    with pytest.raises(WikiRepoError, match="into itself"):
        merge_pages(wiki, "a", ["a"])
