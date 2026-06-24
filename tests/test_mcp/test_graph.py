"""Tests for the MCP link-graph cache and find_related traversal."""

from __future__ import annotations

import os
import time
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


def _force_cache_refresh(tools: WikiTools) -> None:
    """Defeat the once-per-second stat rate-limit so the next get() re-checks."""
    tools._cache._last_check = 0.0


def test_find_related_traverses_both_directions(tmp_path: Path) -> None:
    tools = _make_tools(tmp_path)
    _write_page(tools._wiki, "a", ["b"])
    _write_page(tools._wiki, "b", [])
    _write_page(tools._wiki, "c", ["a"])

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

    out = tools.find_related("hub", depth=1, limit=10)

    assert out.count("- [[") == 10
    assert "of 60" in out
    assert "raise limit" in out  # capped, not paged


def test_graph_cache_detects_deletion(tmp_path: Path) -> None:
    tools = _make_tools(tmp_path)
    _write_page(tools._wiki, "a", ["b"])
    page_b = _write_page(tools._wiki, "b", [])
    # An older page so the deleted file is not the newest (the case the bare
    # max-file-mtime check would miss).
    old = time.time() - 100
    os.utime(page_b, (old, old))

    assert "[[b" in tools.find_related("a", depth=1)

    page_b.unlink()
    _force_cache_refresh(tools)

    # The directory mtime moved when the file was removed, so the cache rebuilds
    # and b is gone from the graph (now only an unresolved gap target, if shown).
    out = tools.find_related("a", depth=1)
    assert "b|" not in out  # no resolved page link to b
