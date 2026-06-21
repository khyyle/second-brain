"""Tests for deterministic wiki structure analysis."""

from __future__ import annotations

from pathlib import Path

from second_brain.compilation.structure import (
    _extract_wikilinks,
    _normalize_link_target,
    build_link_graph,
    detect_gaps,
    discover_all_pages,
)


def test_normalize_strips_folder_prefix_suffix_and_anchor() -> None:
    assert _normalize_link_target("concepts/exchange-traded-funds") == "exchange-traded-funds"
    assert _normalize_link_target("point-estimation") == "point-estimation"
    assert _normalize_link_target("concepts/foo.md") == "foo"
    assert _normalize_link_target("concepts/foo#section") == "foo"
    assert _normalize_link_target("  spaced  ") == "spaced"


def test_extract_wikilinks_normalizes_both_styles() -> None:
    content = "See [[point-estimation]] and [[concepts/exchange-traded-funds|ETFs]]."
    assert _extract_wikilinks(content) == ["point-estimation", "exchange-traded-funds"]


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
