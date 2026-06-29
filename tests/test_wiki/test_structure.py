"""Tests for deterministic wiki structure analysis."""

from __future__ import annotations

from pathlib import Path

from second_brain.wiki.structure import (
    _parse_frontmatter,
    build_link_graph,
    detect_gaps,
    discover_all_pages,
    serialize_page,
    strip_frontmatter,
    update_frontmatter,
)


def _write_page(wiki: Path, content_dir: str, stem: str, body: str) -> None:
    path = wiki / content_dir / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntitle: {stem}\ntype: concept\n---\n{body}\n", encoding="utf-8")


def test_path_prefixed_links_resolve_in_graph(tmp_path: Path) -> None:
    """A folder-prefixed link must not be reported as a missing page."""
    wiki = tmp_path / "wiki"
    _write_page(wiki, "concepts", "equity-swaps", "Hedge via [[concepts/exchange-traded-funds]].")
    _write_page(wiki, "concepts", "exchange-traded-funds", "An ETF.")

    pages = discover_all_pages(wiki)
    graph = build_link_graph(pages)

    assert "exchange-traded-funds" in graph.forward["equity-swaps"]
    assert "equity-swaps" in graph.backward["exchange-traded-funds"]
    assert detect_gaps(pages, graph) == []


def test_serialize_page_round_trips_through_parse() -> None:
    frontmatter = {
        "title": "Point Estimation",
        "type": "concept",
        "domains": ["mathematics"],
        "prerequisites": ["[[statistical-models]]"],
        "sources": ["raw/documents/inference-modeling.md"],
    }
    page = serialize_page(frontmatter, "# Point Estimation\n\nBody text.")
    assert _parse_frontmatter(page) == frontmatter
    assert page.startswith("---\n")
    # exactly one blank line between the closing fence and the body
    assert "---\n\n# Point Estimation" in page


def test_serialize_page_quotes_values_that_would_break_handwritten_yaml() -> None:
    # A bare colon in a title is invalid YAML when typed by hand; serializing
    # through yaml.safe_dump quotes it so the block always parses.
    page = serialize_page({"title": "Estimation: A Primer", "type": "concept"}, "body")
    assert _parse_frontmatter(page)["title"] == "Estimation: A Primer"


def test_update_frontmatter_merges_fields_and_keeps_body_verbatim() -> None:
    original = "---\ntitle: T\ntype: concept\ndomains:\n- a\n---\n\n# Heading\n\nBody.\n"
    updated = update_frontmatter(original, {"domains": ["b", "c"], "tags": ["x"]})
    assert updated is not None
    parsed = _parse_frontmatter(updated)
    assert parsed["domains"] == ["b", "c"]  # list replaced wholesale
    assert parsed["tags"] == ["x"]  # new field added
    assert parsed["title"] == "T"  # untouched field kept
    assert strip_frontmatter(updated) == strip_frontmatter(original)  # body byte-identical


def test_update_frontmatter_returns_none_without_a_block() -> None:
    assert update_frontmatter("just a body, no frontmatter", {"title": "x"}) is None
