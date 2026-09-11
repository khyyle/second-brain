"""Tests for incremental keyword syncing and off-path embedding."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from second_brain.config import SearchConfig
from second_brain.mcp_server import embeddings as embeddings_mod
from second_brain.mcp_server.embeddings import EmbeddingRole
from second_brain.mcp_server.search import SearchIndex
from second_brain.mcp_server.tools import WikiTools

# Each fresh sync sets file mtimes explicitly so change detection is
# deterministic regardless of filesystem timestamp resolution.
_BASE_MTIME = 1_700_000_000.0


@pytest.fixture
def embed_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Patch the embedder to record each call, proving when work happens."""
    calls: list[str] = []

    def _record(text: str, config: SearchConfig, role: EmbeddingRole) -> list[float]:
        calls.append(text)
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(embeddings_mod, "embed_text", _record)
    return calls


@pytest.fixture(autouse=True)
def _mock_default_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure all tests in this module have a 3-d mock embedder by default."""

    def _default_embed(text: str, config: SearchConfig, role: EmbeddingRole) -> list[float]:
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(embeddings_mod, "embed_text", _default_embed)


def _write(wiki_dir: Path, stem: str, body: str, mtime: float) -> Path:
    path = wiki_dir / "concepts" / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntitle: {stem}\ntype: concept\n---\n{body}\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


# --- keyword/metadata sync: fast, inline, never embeds -----------------------


def test_sync_indexes_all_on_fresh_index(
    tmp_path: Path, search_index: SearchIndex, embed_calls: list[str]
) -> None:
    wiki = tmp_path / "wiki"
    _write(wiki, "alpha", "alpha about cats", _BASE_MTIME)
    _write(wiki, "beta", "beta about dogs", _BASE_MTIME)

    assert search_index.sync_from_wiki(wiki) == 2
    # Sync is keyword-only: embedding happens off the request path.
    assert embed_calls == []
    assert {p["stem"] for p in search_index.list_pages()} == {"alpha", "beta"}


def test_sync_skips_unchanged_pages(tmp_path: Path, search_index: SearchIndex) -> None:
    wiki = tmp_path / "wiki"
    _write(wiki, "alpha", "alpha about cats", _BASE_MTIME)
    search_index.sync_from_wiki(wiki)

    assert search_index.sync_from_wiki(wiki) == 0


def test_sync_reindexes_changed_content(tmp_path: Path, search_index: SearchIndex) -> None:
    wiki = tmp_path / "wiki"
    _write(wiki, "alpha", "alpha about cats", _BASE_MTIME)
    _write(wiki, "beta", "beta about dogs", _BASE_MTIME)
    search_index.sync_from_wiki(wiki)

    _write(wiki, "alpha", "alpha about lions now", _BASE_MTIME + 10)
    assert search_index.sync_from_wiki(wiki) == 1
    assert any(h.stem == "alpha" for h in search_index.search("lions"))


def test_sync_skips_identical_content_with_new_mtime(
    tmp_path: Path, search_index: SearchIndex
) -> None:
    wiki = tmp_path / "wiki"
    _write(wiki, "alpha", "alpha about cats", _BASE_MTIME)
    search_index.sync_from_wiki(wiki)

    # Same bytes, newer mtime (e.g. a recompile rewriting an identical page).
    _write(wiki, "alpha", "alpha about cats", _BASE_MTIME + 10)
    assert search_index.sync_from_wiki(wiki) == 0
    # The refreshed mtime is persisted, so a follow-up sync stays cheap too.
    assert search_index.sync_from_wiki(wiki) == 0


def test_sync_picks_up_new_file(tmp_path: Path, search_index: SearchIndex) -> None:
    wiki = tmp_path / "wiki"
    _write(wiki, "alpha", "alpha about cats", _BASE_MTIME)
    search_index.sync_from_wiki(wiki)

    _write(wiki, "gamma", "gamma about birds", _BASE_MTIME + 10)
    assert search_index.sync_from_wiki(wiki) == 1
    assert {p["stem"] for p in search_index.list_pages()} == {"alpha", "gamma"}


def test_sync_drops_deleted_file(tmp_path: Path, search_index: SearchIndex) -> None:
    wiki = tmp_path / "wiki"
    _write(wiki, "alpha", "alpha about cats", _BASE_MTIME)
    beta = _write(wiki, "beta", "beta about dogs", _BASE_MTIME)
    search_index.sync_from_wiki(wiki)
    search_index.embed_pending()

    beta.unlink()
    search_index.sync_from_wiki(wiki)

    assert {p["stem"] for p in search_index.list_pages()} == {"alpha"}
    assert search_index.search("dogs") == []
    assert all(h.stem != "beta" for h in search_index.semantic_search("dogs"))


# --- embedding: only pending pages, off the request path ---------------------


def test_embed_pending_embeds_only_pending(
    tmp_path: Path, search_index: SearchIndex, embed_calls: list[str]
) -> None:
    wiki = tmp_path / "wiki"
    _write(wiki, "alpha", "alpha about cats", _BASE_MTIME)
    _write(wiki, "beta", "beta about dogs", _BASE_MTIME)
    search_index.sync_from_wiki(wiki)

    assert search_index.embed_pending() == 2
    assert len(embed_calls) == 2
    # Idempotent: nothing pending, no further embedding work.
    embed_calls.clear()
    assert search_index.embed_pending() == 0
    assert embed_calls == []
    assert any(h.stem == "alpha" for h in search_index.semantic_search("cats"))


def test_embed_pending_reembeds_changed_page_only(
    tmp_path: Path, search_index: SearchIndex, embed_calls: list[str]
) -> None:
    wiki = tmp_path / "wiki"
    _write(wiki, "alpha", "alpha about cats", _BASE_MTIME)
    _write(wiki, "beta", "beta about dogs", _BASE_MTIME)
    search_index.sync_from_wiki(wiki)
    search_index.embed_pending()
    embed_calls.clear()

    _write(wiki, "alpha", "alpha about lions", _BASE_MTIME + 10)
    search_index.sync_from_wiki(wiki)
    assert search_index.embed_pending() == 1
    assert len(embed_calls) == 1


def test_embed_pending_stops_when_embedder_unavailable(
    tmp_path: Path, search_index: SearchIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = tmp_path / "wiki"
    _write(wiki, "alpha", "alpha about cats", _BASE_MTIME)
    search_index.sync_from_wiki(wiki)

    monkeypatch.setattr(embeddings_mod, "embed_text", lambda text, config, role: None)
    assert search_index.embed_pending() == 0
    # Page stays pending; a later pass with a working embedder will retry.
    assert search_index.semantic_search("cats") == []


# --- live pickup via WikiTools -----------------------------------------------


def test_ensure_synced_picks_up_new_file_without_restart(
    tmp_path: Path, search_index: SearchIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("second_brain.mcp_server.tools.SYNC_MIN_INTERVAL_SECONDS", 0.0)

    class SyncThread:
        def __init__(self, target: Callable[[], None], daemon: bool = True) -> None:
            self._target = target

        def start(self) -> None:
            self._target()  # run synchronously on main thread

        def is_alive(self) -> bool:
            return False

    monkeypatch.setattr("second_brain.mcp_server.tools.threading.Thread", SyncThread)

    wiki = tmp_path / "wiki"
    raw = tmp_path / "raw"
    raw.mkdir()
    _write(wiki, "alpha", "alpha about cats", _BASE_MTIME)
    tools = WikiTools(wiki, raw, search_index)

    tools.ensure_synced()
    assert tools.search_wiki("dogs") == "No results found."

    _write(wiki, "beta", "beta about dogs", _BASE_MTIME + 10)
    tools.ensure_synced()

    # Keyword search is updated inline.
    assert "beta" in tools.search_wiki("dogs")
    assert any(h.stem == "beta" for h in search_index.semantic_search("dogs"))
