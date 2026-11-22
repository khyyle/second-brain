"""Tests for incremental keyword syncing and off-path embedding."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

import numpy as np
import pytest
import sqlite_vec
from numpy.typing import NDArray

from second_brain.apps.inference.services.worker_client import EmbeddingRole
from second_brain.apps.wiki.services import search_index as search_index_mod
from second_brain.apps.wiki.services.search_index import SearchIndex
from second_brain.apps.wiki.tests.test_assistant_tools.conftest import padded_vector

# Each fresh sync sets file mtimes explicitly so change detection is
# deterministic regardless of filesystem timestamp resolution.
_BASE_MTIME = 1_700_000_000.0


@pytest.fixture(autouse=True)
def embed_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Patch the embedder to record each call, proving when work happens."""
    calls: list[str] = []

    def _record(text: str, role: EmbeddingRole) -> NDArray[np.float32]:
        calls.append(text)
        return padded_vector(0.1, 0.2, 0.3)

    monkeypatch.setattr(search_index_mod, "embed_text", _record)
    return calls


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


def test_sync_drops_deleted_file(
    tmp_path: Path, search_index: SearchIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = tmp_path / "wiki"
    _write(wiki, "alpha", "alpha about cats", _BASE_MTIME)
    beta = _write(wiki, "beta", "beta about dogs", _BASE_MTIME)
    search_index.sync_from_wiki(wiki)
    search_index.embed_pending()

    beta.unlink()
    with monkeypatch.context() as patch:
        patch.setattr(
            sqlite_vec, "load", lambda conn: pytest.fail("Keyword sync loaded sqlite-vec")
        )
        search_index.sync_from_wiki(wiki)
        assert search_index.search("dogs") == []

    assert {p["stem"] for p in search_index.list_pages()} == {"alpha"}
    assert all(h.stem != "beta" for h in search_index.semantic_search("dogs"))
    assert search_index.embed_pending() == 0
    # Searches already exclude deleted metadata. Check the stored vector is gone too.
    with closing(sqlite3.connect(tmp_path / "search.db")) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        assert conn.execute("SELECT stem FROM wiki_vec").fetchall() == [("alpha",)]


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


@pytest.mark.parametrize("deleted", [False, True], ids=["edit", "delete"])
def test_page_changes_during_embedding(
    tmp_path: Path, search_index: SearchIndex, monkeypatch: pytest.MonkeyPatch, deleted: bool
) -> None:
    wiki = tmp_path / "wiki"
    page = _write(wiki, "alpha", "first draft", _BASE_MTIME)
    search_index.sync_from_wiki(wiki)

    def edit_during_request(text: str, role: EmbeddingRole) -> NDArray[np.float32]:
        if deleted:
            page.unlink()
        else:
            _write(wiki, "alpha", "revised draft", _BASE_MTIME + 10)
        search_index.sync_from_wiki(wiki)
        return padded_vector(1, 0, 0)

    with monkeypatch.context() as patch:
        patch.setattr(search_index_mod, "embed_text", edit_during_request)
        assert search_index.embed_pending() == 0

    assert search_index.semantic_search("first draft") == []
    assert search_index.embed_pending() == (0 if deleted else 1)


def test_late_embedding_cannot_replace_a_newer_vector(
    tmp_path: Path, search_index: SearchIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = tmp_path / "wiki"
    _write(wiki, "note", "old draft", _BASE_MTIME)
    search_index.sync_from_wiki(wiki)

    def embed(text: str, role: EmbeddingRole) -> NDArray[np.float32]:
        if "old draft" in text:
            _write(wiki, "note", "new draft", _BASE_MTIME + 10)
            search_index.sync_from_wiki(wiki)
            assert search_index.embed_pending() == 1
            return padded_vector(1, 0, 0)
        return padded_vector(0, 1, 0)

    monkeypatch.setattr(search_index_mod, "embed_text", embed)
    assert search_index.embed_pending() == 0
    (hit,) = search_index.semantic_search("new draft")
    assert hit.stem == "note"
    assert hit.score == pytest.approx(0)


def test_embed_pending_retains_pages_without_usable_embeddings(
    tmp_path: Path, search_index: SearchIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = tmp_path / "wiki"
    _write(wiki, "alpha", "alpha about cats", _BASE_MTIME)
    search_index.sync_from_wiki(wiki)

    monkeypatch.setattr(search_index_mod, "embed_text", lambda text, role: None)
    assert search_index.embed_pending() == 0
    # Page stays pending
    assert search_index.semantic_search("cats") == []
