"""Tests for incremental search-index syncing from the wiki on disk."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from second_brain.config import SearchConfig
from second_brain.mcp_server import embeddings as embeddings_mod
from second_brain.mcp_server.search import SearchIndex
from second_brain.mcp_server.tools import WikiTools

# Each fresh sync sets file mtimes explicitly so change detection is
# deterministic regardless of filesystem timestamp resolution.
_BASE_MTIME = 1_700_000_000.0


@pytest.fixture
def semantic_config() -> SearchConfig:
    return SearchConfig(embedding_dimensions=3, semantic_enabled=True)


@pytest.fixture
def embed_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Patch the embedder to record each call, proving when work happens."""
    calls: list[str] = []

    def _record(text: str, config: SearchConfig) -> list[float]:
        calls.append(text)
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(embeddings_mod, "embed_text", _record)
    return calls


def _write(wiki_dir: Path, stem: str, body: str, mtime: float) -> Path:
    path = wiki_dir / "concepts" / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntitle: {stem}\ntype: concept\n---\n{body}\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def test_sync_indexes_all_on_fresh_index(
    tmp_path: Path, semantic_config: SearchConfig, embed_calls: list[str]
) -> None:
    wiki = tmp_path / "wiki"
    _write(wiki, "alpha", "alpha about cats", _BASE_MTIME)
    _write(wiki, "beta", "beta about dogs", _BASE_MTIME)
    index = SearchIndex(tmp_path / "s.db", semantic_config)

    assert index.sync_from_wiki(wiki) == 2
    assert len(embed_calls) == 2
    assert {p["stem"] for p in index.list_pages()} == {"alpha", "beta"}


def test_sync_skips_unchanged_pages(
    tmp_path: Path, semantic_config: SearchConfig, embed_calls: list[str]
) -> None:
    wiki = tmp_path / "wiki"
    _write(wiki, "alpha", "alpha about cats", _BASE_MTIME)
    index = SearchIndex(tmp_path / "s.db", semantic_config)
    index.sync_from_wiki(wiki)

    embed_calls.clear()
    assert index.sync_from_wiki(wiki) == 0
    assert embed_calls == []


def test_sync_reindexes_changed_content(
    tmp_path: Path, semantic_config: SearchConfig, embed_calls: list[str]
) -> None:
    wiki = tmp_path / "wiki"
    _write(wiki, "alpha", "alpha about cats", _BASE_MTIME)
    _write(wiki, "beta", "beta about dogs", _BASE_MTIME)
    index = SearchIndex(tmp_path / "s.db", semantic_config)
    index.sync_from_wiki(wiki)
    embed_calls.clear()

    _write(wiki, "alpha", "alpha about lions now", _BASE_MTIME + 10)
    assert index.sync_from_wiki(wiki) == 1
    assert len(embed_calls) == 1
    assert any(h.stem == "alpha" for h in index.search("lions"))


def test_sync_skips_identical_content_with_new_mtime(
    tmp_path: Path, semantic_config: SearchConfig, embed_calls: list[str]
) -> None:
    wiki = tmp_path / "wiki"
    _write(wiki, "alpha", "alpha about cats", _BASE_MTIME)
    index = SearchIndex(tmp_path / "s.db", semantic_config)
    index.sync_from_wiki(wiki)
    embed_calls.clear()

    # Same bytes, newer mtime (e.g. a recompile rewriting an identical page).
    _write(wiki, "alpha", "alpha about cats", _BASE_MTIME + 10)
    assert index.sync_from_wiki(wiki) == 0
    assert embed_calls == []
    # The refreshed mtime is persisted, so a follow-up sync stays cheap too.
    assert index.sync_from_wiki(wiki) == 0


def test_sync_picks_up_new_file(
    tmp_path: Path, semantic_config: SearchConfig, embed_calls: list[str]
) -> None:
    wiki = tmp_path / "wiki"
    _write(wiki, "alpha", "alpha about cats", _BASE_MTIME)
    index = SearchIndex(tmp_path / "s.db", semantic_config)
    index.sync_from_wiki(wiki)
    embed_calls.clear()

    _write(wiki, "gamma", "gamma about birds", _BASE_MTIME + 10)
    assert index.sync_from_wiki(wiki) == 1
    assert len(embed_calls) == 1
    assert {p["stem"] for p in index.list_pages()} == {"alpha", "gamma"}


def test_sync_drops_deleted_file(
    tmp_path: Path, semantic_config: SearchConfig, embed_calls: list[str]
) -> None:
    wiki = tmp_path / "wiki"
    _write(wiki, "alpha", "alpha about cats", _BASE_MTIME)
    beta = _write(wiki, "beta", "beta about dogs", _BASE_MTIME)
    index = SearchIndex(tmp_path / "s.db", semantic_config)
    index.sync_from_wiki(wiki)

    beta.unlink()
    index.sync_from_wiki(wiki)

    assert {p["stem"] for p in index.list_pages()} == {"alpha"}
    assert index.search("dogs") == []
    assert all(h.stem != "beta" for h in index.semantic_search("dogs"))


def test_ensure_synced_picks_up_new_file_without_restart(
    tmp_path: Path, semantic_config: SearchConfig, embed_calls: list[str]
) -> None:
    wiki = tmp_path / "wiki"
    raw = tmp_path / "raw"
    raw.mkdir()
    _write(wiki, "alpha", "alpha about cats", _BASE_MTIME)
    index = SearchIndex(tmp_path / "s.db", semantic_config)
    tools = WikiTools(wiki, raw, index)

    tools.ensure_synced()
    assert tools.search_wiki("dogs") == "No results found."

    _write(wiki, "beta", "beta about dogs", _BASE_MTIME + 10)
    # Bypass the debounce window rather than sleeping in a test.
    tools._last_sync_check = 0.0
    tools.ensure_synced()
    assert "beta" in tools.search_wiki("dogs")
