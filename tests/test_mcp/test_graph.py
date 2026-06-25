"""Tests for DB-backed find_related traversal over the wiki_links graph."""

from __future__ import annotations

from pathlib import Path

from second_brain.config import SearchConfig
from second_brain.mcp_server.search import SearchIndex
from second_brain.mcp_server.tools import WikiTools


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
