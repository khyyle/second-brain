"""Tests for DB-backed graph traversal over the wiki_links graph."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from second_brain.mcp_server.tools import _topological_order

if TYPE_CHECKING:
    from tests.test_mcp.conftest import WikiToolsHarness


def _write_page(wiki: Path, stem: str, links: list[str], content_dir: str = "concepts") -> Path:
    path = wiki / content_dir / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"[[{target}]]" for target in links)
    path.write_text(f"---\ntitle: {stem}\ntype: concept\n---\n{body}\n", encoding="utf-8")
    return path


def _write_concept(
    wiki: Path,
    stem: str,
    prerequisites: list[str] | None = None,
    domains: list[str] | None = None,
) -> Path:
    """Write a concept page declaring ``prerequisites`` as wikilinks and ``domains``."""
    path = wiki / "concepts" / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"title: {stem}", "type: concept"]
    if domains:
        lines.append("domains:")
        lines += [f"  - {domain}" for domain in domains]
    if prerequisites:
        lines.append("prerequisites:")
        lines += [f'  - "[[{prerequisite}]]"' for prerequisite in prerequisites]
    lines += ["---", "", f"# {stem}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _sync(harness: WikiToolsHarness) -> None:
    """Index the on-disk pages so the link graph reflects them."""
    harness.index.sync_from_wiki(harness.wiki_dir)


def test_find_related_traverses_both_directions(wiki_harness: WikiToolsHarness) -> None:
    _write_page(wiki_harness.wiki_dir, "a", ["b"])
    _write_page(wiki_harness.wiki_dir, "b", [])
    _write_page(wiki_harness.wiki_dir, "c", ["a"])
    _sync(wiki_harness)

    out = wiki_harness.tools.find_related("a", depth=1)

    # b is reached via forward, c via backward.
    assert "[[b" in out
    assert "[[c" in out


def test_find_related_caps_fan_out(wiki_harness: WikiToolsHarness) -> None:
    # One hub linking to many neighbors; the cap must bound the listing.
    targets = [f"n{i}" for i in range(60)]
    _write_page(wiki_harness.wiki_dir, "hub", targets)
    for stem in targets:
        _write_page(wiki_harness.wiki_dir, stem, [])
    _sync(wiki_harness)

    out = wiki_harness.tools.find_related("hub", depth=1, limit=10)

    assert out.count("- [[") == 10
    assert "of 60" in out
    assert "raise limit" in out  # capped, not paged


def test_find_related_drops_deleted_page(wiki_harness: WikiToolsHarness) -> None:
    _write_page(wiki_harness.wiki_dir, "a", ["b"])
    page_b = _write_page(wiki_harness.wiki_dir, "b", [])
    _sync(wiki_harness)

    assert "[[b|" in wiki_harness.tools.find_related("a", depth=1)  # resolved page link

    page_b.unlink()
    _sync(wiki_harness)

    # b is no longer a page, so the surviving a->b edge shows it only as a gap.
    out = wiki_harness.tools.find_related("a", depth=1)
    assert "b|" not in out  # no resolved page link to b


def test_neighbors_follows_targets_and_sources(wiki_harness: WikiToolsHarness) -> None:
    _write_concept(wiki_harness.wiki_dir, "advanced", ["basic"])  # advanced requires basic
    _write_concept(wiki_harness.wiki_dir, "basic", [])
    _sync(wiki_harness)
    index = wiki_harness.index

    # advanced -> basic: basic is a target of advanced, advanced a source of basic.
    assert index.neighbors({"advanced"}, following="targets") == {"basic"}
    assert index.neighbors({"advanced"}, following="sources") == set()
    assert index.neighbors({"basic"}, following="sources") == {"advanced"}
    assert index.neighbors({"basic"}, following="targets") == set()


def test_neighbors_rejects_unknown_following(wiki_harness: WikiToolsHarness) -> None:
    with pytest.raises(ValueError, match="following"):
        wiki_harness.index.neighbors({"advanced"}, following="up")


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


def test_prerequisite_closure_orders_fundamentals_first(
    wiki_harness: WikiToolsHarness,
) -> None:
    _write_concept(
        wiki_harness.wiki_dir,
        "bias-variance-tradeoff",
        ["point-estimation", "expectation-and-variance"],
    )
    _write_concept(
        wiki_harness.wiki_dir,
        "point-estimation",
        ["statistical-models", "probability-distributions", "expectation-and-variance"],
    )
    _write_concept(
        wiki_harness.wiki_dir,
        "statistical-models",
        ["probability-distributions", "cumulative-distribution-functions"],
    )
    _sync(wiki_harness)

    out = wiki_harness.tools.prerequisite_closure("bias-variance-tradeoff")

    # Real pages sort fundamentals-first, the queried target lands last.
    assert out.index("statistical-models") < out.index("point-estimation") < out.index("(target)")
    # The unwritten fundamentals surface as gaps, and a shared one is named.
    assert "probability-distributions]] (gap)" in out
    assert "probability-distributions" in out.split("Shared foundations")[1]


def test_prerequisite_closure_page_not_found(wiki_harness: WikiToolsHarness) -> None:
    assert "not found" in wiki_harness.tools.prerequisite_closure("nonexistent").lower()


def test_prerequisite_closure_without_prerequisites(wiki_harness: WikiToolsHarness) -> None:
    _write_concept(wiki_harness.wiki_dir, "axiom", [])
    _sync(wiki_harness)

    assert "no prerequisites" in wiki_harness.tools.prerequisite_closure("axiom").lower()


def test_dependents_lists_pages_that_require_it(wiki_harness: WikiToolsHarness) -> None:
    _write_concept(wiki_harness.wiki_dir, "statistical-models", ["probability-distributions"])
    _write_concept(wiki_harness.wiki_dir, "point-estimation", ["statistical-models"])
    _write_concept(wiki_harness.wiki_dir, "confidence-intervals", ["statistical-models"])
    _sync(wiki_harness)

    out = wiki_harness.tools.dependents("statistical-models")

    assert "point-estimation" in out
    assert "confidence-intervals" in out
    # A prerequisite of statistical-models is not a dependent of it.
    assert "probability-distributions" not in out


def test_dependents_reports_none(wiki_harness: WikiToolsHarness) -> None:
    _write_concept(wiki_harness.wiki_dir, "statistical-models", ["probability-distributions"])
    _sync(wiki_harness)

    assert "Nothing depends on" in wiki_harness.tools.dependents("statistical-models")


def test_find_related_falls_back_to_alphabetical_without_semantic(
    wiki_harness: WikiToolsHarness,
) -> None:
    # wiki_harness disables semantic by default, so ranking is alphabetical.
    _write_page(wiki_harness.wiki_dir, "source", ["zeta", "alpha", "mid"])
    for stem in ("zeta", "alpha", "mid"):
        _write_page(wiki_harness.wiki_dir, stem, [])
    _sync(wiki_harness)

    out = wiki_harness.tools.find_related("source", depth=1)

    assert out.index("alpha") < out.index("mid") < out.index("zeta")


def test_list_gaps_ranks_by_reference_count(wiki_harness: WikiToolsHarness) -> None:
    _write_concept(wiki_harness.wiki_dir, "a", ["popular-gap", "lonely-gap"])
    _write_concept(wiki_harness.wiki_dir, "b", ["popular-gap"])
    _sync(wiki_harness)

    out = wiki_harness.tools.list_gaps()

    # popular-gap is referenced by two pages, lonely-gap by one, so it ranks first.
    assert out.index("popular-gap") < out.index("lonely-gap")
    assert "2 references" in out


def test_list_gaps_reports_none(wiki_harness: WikiToolsHarness) -> None:
    _write_concept(wiki_harness.wiki_dir, "self-contained", [])
    _sync(wiki_harness)

    assert "No gaps" in wiki_harness.tools.list_gaps()


def test_list_domains_counts_pages(wiki_harness: WikiToolsHarness) -> None:
    _write_concept(wiki_harness.wiki_dir, "a", domains=["mathematics", "economics"])
    _write_concept(wiki_harness.wiki_dir, "b", domains=["mathematics"])
    _sync(wiki_harness)

    out = wiki_harness.tools.list_domains()

    # mathematics has two pages, economics one, so it ranks first.
    assert out.index("mathematics") < out.index("economics")
    assert "mathematics (2 pages)" in out


def test_read_index_groups_by_domain_with_cap(wiki_harness: WikiToolsHarness) -> None:
    _write_concept(wiki_harness.wiki_dir, "alpha", domains=["mathematics"])
    _write_concept(wiki_harness.wiki_dir, "beta", domains=["mathematics"])
    _write_concept(wiki_harness.wiki_dir, "gamma", domains=["economics"])
    _write_concept(wiki_harness.wiki_dir, "loose")  # declares no domains
    _sync(wiki_harness)

    out = wiki_harness.tools.read_index(pages_per_domain=1)

    assert "4 pages across 3 domains" in out
    assert "## mathematics (2)" in out
    # the per-domain cap leaves a drill-down pointer for the bigger domain
    assert '+1 more — list_pages(domain="mathematics")' in out
    # a page with no domains is grouped under uncategorized
    assert "## uncategorized (1)" in out


def test_read_index_empty_wiki(wiki_harness: WikiToolsHarness) -> None:
    assert "No pages yet" in wiki_harness.tools.read_index()
