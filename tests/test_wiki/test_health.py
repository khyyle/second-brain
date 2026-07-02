"""Tests for wiki health: defect vs growth framing and gap provenance."""

from __future__ import annotations

from pathlib import Path

from second_brain.wiki.health import run_health_check


def _write(wiki: Path, content_dir: str, stem: str, body: str, frontmatter: str = "") -> None:
    path = wiki / content_dir / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    head = f"title: {stem}\ntype: concept\ndomains:\n- mathematics\n"
    path.write_text(f"---\n{head}{frontmatter}---\n\n{body}\n", encoding="utf-8")


def test_gaps_and_orphans_do_not_make_a_wiki_unhealthy(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    # `a` links to `b` (which exists) and to `missing` (a gap). `b` is only
    # linked-to, `a` is only linking-out, so both are structurally normal.
    _write(wiki, "concepts", "a", "See [[b]] and [[missing]].")
    _write(wiki, "concepts", "b", "A page.")

    report = run_health_check(wiki)

    assert any(stem == "missing" for stem, _ in report.gap_links)
    assert report.orphan_pages  # `a` is not referenced by anything
    assert report.is_healthy  # ...yet the wiki is healthy: no defects


def test_missing_frontmatter_makes_a_wiki_unhealthy(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    path = wiki / "concepts" / "bare.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Bare\n\nNo frontmatter here.\n", encoding="utf-8")

    report = run_health_check(wiki)

    assert report.missing_frontmatter
    assert not report.is_healthy


def test_gap_links_are_ranked_by_reference_count(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    # `popular` is referenced by two pages, `rare` by one, so `popular` ranks first.
    _write(wiki, "concepts", "one", "Needs [[popular]] and [[rare]].")
    _write(wiki, "concepts", "two", "Also needs [[popular]].")

    report = run_health_check(wiki)

    assert report.gap_links[0] == ("popular", 2)
    assert ("rare", 1) in report.gap_links
