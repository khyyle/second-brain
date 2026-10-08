"""Tests for embed_text chunking of long input.

Long wiki pages and conversations exceed the local embedder's context
window; embed_text must chunk them and mean-pool rather than silently
returning None (which would drop the page from semantic search).
"""

from __future__ import annotations

import httpx
import pytest

from second_brain import ollama
from second_brain.config import SearchConfig
from second_brain.mcp_server import embeddings as embeddings_mod
from second_brain.mcp_server.embeddings import (
    EMBED_CHUNK_CHARS,
    EMBED_MAX_CHUNKS,
    EmbeddingRole,
    embed_text,
)
from second_brain.ollama import OllamaUnavailableError


def _patch_chunk(
    monkeypatch: pytest.MonkeyPatch, vector: list[float]
) -> list[tuple[str, EmbeddingRole]]:
    """Patch _embed_chunk to a fixed vector and record its inputs."""
    seen: list[tuple[str, EmbeddingRole]] = []

    def fake(text: str, config: SearchConfig, role: EmbeddingRole) -> list[float]:
        seen.append((text, role))
        return list(vector)

    monkeypatch.setattr(embeddings_mod, "_embed_chunk", fake)
    return seen


def test_embed_text_raises_when_ollama_unreachable(
    monkeypatch: pytest.MonkeyPatch, search_config: SearchConfig
) -> None:
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(ollama.httpx, "post", _raise)
    with pytest.raises(OllamaUnavailableError):
        embed_text("short", search_config, role="search_query")


def test_server_error_for_one_chunk_returns_none(
    monkeypatch: pytest.MonkeyPatch, search_config: SearchConfig
) -> None:
    def _raise(*_args: object, **_kwargs: object) -> None:
        request = httpx.Request("POST", "http://localhost:11434/api/embeddings")
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError("too large", request=request, response=response)

    monkeypatch.setattr(ollama.httpx, "post", _raise)
    assert embed_text("short", search_config, role="search_query") is None


def test_short_text_single_chunk_unchanged(
    monkeypatch: pytest.MonkeyPatch, search_config: SearchConfig
) -> None:
    seen = _patch_chunk(monkeypatch, [1.0, 2.0, 3.0])
    assert embed_text("short", search_config, role="search_query") == [
        1.0,
        2.0,
        3.0,
    ]
    assert len(seen) == 1
    assert seen[0] == ("short", "search_query")


def test_long_text_is_chunked_and_pooled(
    monkeypatch: pytest.MonkeyPatch, search_config: SearchConfig
) -> None:
    seen = _patch_chunk(monkeypatch, [2.0, 4.0, 6.0])
    out = embed_text(
        "x" * (EMBED_CHUNK_CHARS * 3),
        search_config,
        role="search_document",
    )
    assert len(seen) == 3
    assert all(len(chunk) <= EMBED_CHUNK_CHARS for chunk, _ in seen)
    assert all(role == "search_document" for _, role in seen)
    # Mean-pool of identical vectors is that vector.
    assert out == [2.0, 4.0, 6.0]


def test_chunk_count_is_capped(
    monkeypatch: pytest.MonkeyPatch, search_config: SearchConfig
) -> None:
    seen = _patch_chunk(monkeypatch, [1.0, 1.0, 1.0])
    embed_text(
        "y" * (EMBED_CHUNK_CHARS * (EMBED_MAX_CHUNKS + 5)),
        search_config,
        role="clustering",
    )
    assert len(seen) == EMBED_MAX_CHUNKS


def test_returns_none_when_all_chunks_fail(
    monkeypatch: pytest.MonkeyPatch, search_config: SearchConfig
) -> None:
    monkeypatch.setattr(embeddings_mod, "_embed_chunk", lambda text, config, role: None)
    assert (
        embed_text(
            "x" * (EMBED_CHUNK_CHARS * 2),
            search_config,
            role="search_document",
        )
        is None
    )


def test_pools_only_successful_chunks(
    monkeypatch: pytest.MonkeyPatch, search_config: SearchConfig
) -> None:
    calls = {"n": 0}

    def fake(text: str, config: SearchConfig, role: EmbeddingRole) -> list[float] | None:
        calls["n"] += 1
        return [3.0, 3.0, 3.0] if calls["n"] == 1 else None

    monkeypatch.setattr(embeddings_mod, "_embed_chunk", fake)
    out = embed_text(
        "z" * (EMBED_CHUNK_CHARS * 2),
        search_config,
        role="search_document",
    )
    assert out == [3.0, 3.0, 3.0]


@pytest.mark.parametrize("role", ["search_document", "search_query", "clustering"])
def test_embed_chunk_prefixes_text_for_role(
    monkeypatch: pytest.MonkeyPatch,
    search_config: SearchConfig,
    role: EmbeddingRole,
) -> None:
    request_json: dict[str, str] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, list[float]]:
            return {"embedding": [1.0, 2.0, 3.0]}

    def fake_post(url: str, *, json: dict[str, str], timeout: int) -> FakeResponse:
        request_json.update(json)
        return FakeResponse()

    monkeypatch.setattr(ollama.httpx, "post", fake_post)

    assert embeddings_mod._embed_chunk("content", search_config, role) is not None
    assert request_json["prompt"] == f"{role}: content"
