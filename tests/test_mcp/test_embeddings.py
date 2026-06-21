"""Tests for embed_text chunking of long input.

Long wiki pages and conversations exceed the local embedder's context
window; embed_text must chunk them and mean-pool rather than silently
returning None (which would drop the page from semantic search).
"""

from __future__ import annotations

import pytest

from second_brain.config import SearchConfig
from second_brain.mcp_server import embeddings as embeddings_mod
from second_brain.mcp_server.embeddings import (
    EMBED_CHUNK_CHARS,
    EMBED_MAX_CHUNKS,
    embed_text,
)


@pytest.fixture
def search_config() -> SearchConfig:
    return SearchConfig(embedding_dimensions=3)


def _patch_chunk(monkeypatch: pytest.MonkeyPatch, vector: list[float]) -> list[str]:
    """Patch _embed_chunk to a fixed vector and record the chunk texts seen."""
    seen: list[str] = []

    def fake(text: str, config: SearchConfig) -> list[float]:
        seen.append(text)
        return list(vector)

    monkeypatch.setattr(embeddings_mod, "_embed_chunk", fake)
    return seen


def test_short_text_single_chunk_unchanged(
    monkeypatch: pytest.MonkeyPatch, search_config: SearchConfig
) -> None:
    seen = _patch_chunk(monkeypatch, [1.0, 2.0, 3.0])
    assert embed_text("short", search_config) == [1.0, 2.0, 3.0]
    assert len(seen) == 1


def test_long_text_is_chunked_and_pooled(
    monkeypatch: pytest.MonkeyPatch, search_config: SearchConfig
) -> None:
    seen = _patch_chunk(monkeypatch, [2.0, 4.0, 6.0])
    out = embed_text("x" * (EMBED_CHUNK_CHARS * 3), search_config)
    assert len(seen) == 3
    assert all(len(chunk) <= EMBED_CHUNK_CHARS for chunk in seen)
    # Mean-pool of identical vectors is that vector.
    assert out == [2.0, 4.0, 6.0]


def test_chunk_count_is_capped(
    monkeypatch: pytest.MonkeyPatch, search_config: SearchConfig
) -> None:
    seen = _patch_chunk(monkeypatch, [1.0, 1.0, 1.0])
    embed_text("y" * (EMBED_CHUNK_CHARS * (EMBED_MAX_CHUNKS + 5)), search_config)
    assert len(seen) == EMBED_MAX_CHUNKS


def test_returns_none_when_all_chunks_fail(
    monkeypatch: pytest.MonkeyPatch, search_config: SearchConfig
) -> None:
    monkeypatch.setattr(embeddings_mod, "_embed_chunk", lambda text, config: None)
    assert embed_text("x" * (EMBED_CHUNK_CHARS * 2), search_config) is None


def test_pools_only_successful_chunks(
    monkeypatch: pytest.MonkeyPatch, search_config: SearchConfig
) -> None:
    calls = {"n": 0}

    def fake(text: str, config: SearchConfig) -> list[float] | None:
        calls["n"] += 1
        return [3.0, 3.0, 3.0] if calls["n"] == 1 else None

    monkeypatch.setattr(embeddings_mod, "_embed_chunk", fake)
    out = embed_text("z" * (EMBED_CHUNK_CHARS * 2), search_config)
    assert out == [3.0, 3.0, 3.0]
