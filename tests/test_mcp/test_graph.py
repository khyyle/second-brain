"""Tests for DB-backed graph traversal over the wiki_links graph."""

from __future__ import annotations

from pathlib import Path

import pytest

from second_brain.config import SearchConfig
from second_brain.mcp_server.search import SearchIndex
from second_brain.mcp_server.tools import WikiTools, _topological_order


def _make_tools(tmp_path: Path) -> WikiTools:
    data_dir = tmp_path / "data"
    raw = data_dir / "raw"
    wiki = data_dir / "wiki"
    raw.mkdir(parents=True)
    wiki.mkdir(parents=True)
    index = SearchIndex(data_dir / "search.db", SearchConfig(semantic_enabled=False))
    return WikiTools(wiki, raw, index)


def _write_page(wiki: Path, stem: str, links: list[str], content_dir: str = "concepts") -> Path:
    path = wiki / content_dir / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"[[{target}]]" for target in links)
    path.write_text(f"---\ntitle: {stem}\ntype: concept\n---\n{body}\n", encoding="utf-8")
    return path


def _write_concept(wiki: Path, stem: str, prerequisites: list[str]) -> Path:
    """Write a concept page whose frontmatter declares ``prerequisites`` as wikilinks."""
    path = wiki / "concepts" / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"title: {stem}", "type: concept"]
    if prerequisites:
        lines.append("prerequisites:")
        lines += [f'  - "[[{prerequisite}]]"' for prerequisite in prerequisites]
    lines += ["---", "", f"# {stem}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _sync(tools: WikiTools) -> None:
    """Index the on-disk pages so the link graph reflects them."""
    tools._search.sync_from_wiki(tools._wiki)


def test_find_related_traverses_both_directions(tmp_path: Path) -> None:
    tools = _make_tools(tmp_path)
    _write_page(tools._wiki, "a", ["b"])
    _write_page(tools._wiki, "b", [])
    _write_page(tools._wiki, "c", ["a"])
    _sync(tools)

    out = tools.find_related("a", depth=1)

    # b is reached via forward, c via backward.
    assert "[[b" in out
    assert "[[c" in out


def test_find_related_caps_fan_out(tmp_path: Path) -> None:
    tools = _make_tools(tmp_path)
    # One hub linking to many neighbors; the cap must bound the listing.
    targets = [f"n{i}" for i in range(60)]
    _write_page(tools._wiki, "hub", targets)
    for stem in targets:
        _write_page(tools._wiki, stem, [])
    _sync(tools)

    out = tools.find_related("hub", depth=1, limit=10)

    assert out.count("- [[") == 10
    assert "of 60" in out
    assert "raise limit" in out  # capped, not paged


def test_find_related_drops_deleted_page(tmp_path: Path) -> None:
    tools = _make_tools(tmp_path)
    _write_page(tools._wiki, "a", ["b"])
    page_b = _write_page(tools._wiki, "b", [])
    _sync(tools)

    assert "[[b|" in tools.find_related("a", depth=1)  # resolved page link

    page_b.unlink()
    _sync(tools)

    # b is no longer a page, so the surviving a->b edge shows it only as a gap.
    out = tools.find_related("a", depth=1)
    assert "b|" not in out  # no resolved page link to b


def test_neighbors_follows_targets_and_sources(tmp_path: Path) -> None:
    tools = _make_tools(tmp_path)
    _write_concept(tools._wiki, "advanced", ["basic"])  # advanced requires basic
    _write_concept(tools._wiki, "basic", [])
    _sync(tools)
    index = tools._search

    # advanced -> basic: basic is a target of advanced, advanced a source of basic.
    assert index.neighbors({"advanced"}, following="targets") == {"basic"}
    assert index.neighbors({"advanced"}, following="sources") == set()
    assert index.neighbors({"basic"}, following="sources") == {"advanced"}
    assert index.neighbors({"basic"}, following="targets") == set()


def test_neighbors_rejects_unknown_following(tmp_path: Path) -> None:
    tools = _make_tools(tmp_path)

    with pytest.raises(ValueError, match="following"):
        tools._search.neighbors({"advanced"}, following="up")


def test_topological_order_sorts_fundamentals_first() -> None:
    # c depends on b depends on a, so a must come first and c last.
    ordered, cyclic = _topological_order({"a", "b", "c"}, {"c": {"b"}, "b": {"a"}})

    assert ordered == ["a", "b", "c"]
    assert cyclic == []


def test_topological_order_breaks_ties_alphabetically() -> None:
    # With no dependency between them, independent nodes order alphabetically.
    ordered, cyclic = _topological_order({"x", "a", "m"}, {})

    assert ordered == ["a", "m", "x"]
    assert cyclic == []


def test_topological_order_flags_cycles() -> None:
    ordered, cyclic = _topological_order({"a", "b"}, {"a": {"b"}, "b": {"a"}})

    assert ordered == []
    assert cyclic == ["a", "b"]


def test_prerequisite_closure_orders_fundamentals_first(tmp_path: Path) -> None:
    tools = _make_tools(tmp_path)
    _write_concept(
        tools._wiki, "bias-variance-tradeoff", ["point-estimation", "expectation-and-variance"]
    )
    _write_concept(
        tools._wiki,
        "point-estimation",
        ["statistical-models", "probability-distributions", "expectation-and-variance"],
    )
    _write_concept(
        tools._wiki,
        "statistical-models",
        ["probability-distributions", "cumulative-distribution-functions"],
    )
    _sync(tools)

    out = tools.prerequisite_closure("bias-variance-tradeoff")

    # Real pages sort fundamentals-first, the queried target lands last.
    assert out.index("statistical-models") < out.index("point-estimation") < out.index("(target)")
    # The unwritten fundamentals surface as gaps, and a shared one is named.
    assert "probability-distributions]] (gap)" in out
    assert "probability-distributions" in out.split("Shared foundations")[1]


def test_prerequisite_closure_page_not_found(tmp_path: Path) -> None:
    tools = _make_tools(tmp_path)

    assert "not found" in tools.prerequisite_closure("nonexistent").lower()


def test_prerequisite_closure_without_prerequisites(tmp_path: Path) -> None:
    tools = _make_tools(tmp_path)
    _write_concept(tools._wiki, "axiom", [])
    _sync(tools)

    assert "no prerequisites" in tools.prerequisite_closure("axiom").lower()


def test_dependents_lists_pages_that_require_it(tmp_path: Path) -> None:
    tools = _make_tools(tmp_path)
    _write_concept(tools._wiki, "statistical-models", ["probability-distributions"])
    _write_concept(tools._wiki, "point-estimation", ["statistical-models"])
    _write_concept(tools._wiki, "confidence-intervals", ["statistical-models"])
    _sync(tools)

    out = tools.dependents("statistical-models")

    assert "point-estimation" in out
    assert "confidence-intervals" in out
    # A prerequisite of statistical-models is not a dependent of it.
    assert "probability-distributions" not in out


def test_dependents_reports_none(tmp_path: Path) -> None:
    tools = _make_tools(tmp_path)
    _write_concept(tools._wiki, "statistical-models", ["probability-distributions"])
    _sync(tools)

    assert "Nothing depends on" in tools.dependents("statistical-models")
