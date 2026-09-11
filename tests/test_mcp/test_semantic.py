"""Tests for the optional semantic (embedding) search layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from second_brain.config import SearchConfig
from second_brain.mcp_server import embeddings as embeddings_mod
from second_brain.mcp_server.embeddings import EmbeddingRole
from second_brain.mcp_server.search import SearchIndex

# Tiny deterministic "embeddings": map keywords to fixed 3-d vectors so
# nearest-neighbor ordering is predictable without a real model.
_VECTORS = {
    "gradient": [1.0, 0.0, 0.0],
    "optimization": [0.9, 0.1, 0.0],
    "penguin": [0.0, 1.0, 0.0],
    "biology": [0.0, 0.9, 0.1],
}


def _fake_embed(text: str, config: SearchConfig, role: EmbeddingRole) -> list[float] | None:
    lowered = text.lower()
    for key, vec in _VECTORS.items():
        if key in lowered:
            return vec
    return [0.0, 0.0, 1.0]


@pytest.fixture(autouse=True)
def _mock_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embeddings_mod, "embed_text", _fake_embed)


def _index_sample(index: SearchIndex) -> None:
    index.index_page(
        stem="gradient-descent",
        title="Gradient Descent optimization",
        content="gradient based optimization method",
        content_type="concept",
        domains=["math"],
        tags=["optimization"],
        word_count=4,
        path="concepts/gradient-descent.md",
    )
    index.index_page(
        stem="penguins",
        title="Penguin biology",
        content="penguin species and biology",
        content_type="concept",
        domains=["biology"],
        tags=["penguin"],
        word_count=4,
        path="concepts/penguins.md",
    )


def test_semantic_enabled_when_configured(search_index: SearchIndex) -> None:
    assert search_index.semantic_enabled is True


def test_semantic_search_returns_nearest(search_index: SearchIndex) -> None:
    _index_sample(search_index)
    search_index.embed_pending()

    hits = search_index.semantic_search("gradient optimization", limit=1)
    assert len(hits) == 1
    assert hits[0].stem == "gradient-descent"

    hits2 = search_index.semantic_search("penguin", limit=1)
    assert hits2[0].stem == "penguins"


def test_semantic_search_uses_retrieval_roles(
    tmp_path: Path,
    search_config: SearchConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roles: list[EmbeddingRole] = []

    def record_role(text: str, config: SearchConfig, role: EmbeddingRole) -> list[float] | None:
        roles.append(role)
        return _fake_embed(text, config, role)

    monkeypatch.setattr(embeddings_mod, "embed_text", record_role)
    index = SearchIndex(tmp_path / "s.db", search_config)
    _index_sample(index)

    index.embed_pending()
    index.semantic_search("gradient")

    assert roles == [
        "search_document",
        "search_document",
        "search_query",
    ]


def test_semantic_disabled_without_config(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "s.db")  # no SearchConfig -> keyword only
    assert index.semantic_enabled is False
    assert index.semantic_search("anything") == []


def test_semantic_returns_empty_when_embeddings_unavailable(
    search_index: SearchIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    _index_sample(search_index)
    # Simulate Ollama going away: embed_text now returns None.
    monkeypatch.setattr(embeddings_mod, "embed_text", lambda text, config, role: None)
    assert search_index.semantic_search("gradient") == []


def test_keyword_search_still_works_with_semantic_on(search_index: SearchIndex) -> None:
    _index_sample(search_index)
    hits = search_index.search("penguin")
    assert any(h.stem == "penguins" for h in hits)


def test_near_duplicate_pairs_finds_only_close_pages(search_index: SearchIndex) -> None:
    _index_sample(search_index)
    # A near-copy of the gradient page: its fake embedding matches "gradient"
    # exactly, while the penguin page points in an orthogonal direction.
    search_index.index_page(
        stem="gradient-descent-2",
        title="Gradient methods",
        content="gradient stepping toward minima",
        content_type="concept",
        domains=["math"],
        tags=[],
        word_count=4,
        path="concepts/gradient-descent-2.md",
    )
    search_index.embed_pending()

    pairs = search_index.near_duplicate_pairs(min_similarity=0.95)

    assert [(a, b) for a, b, _ in pairs] == [("gradient-descent", "gradient-descent-2")]
    assert pairs[0][2] >= 0.95


def test_near_duplicate_pairs_ignores_cross_type_matches(search_index: SearchIndex) -> None:
    # A concept and a problem that embed identically: high similarity, but a
    # problem drilling a concept is intentional, not a duplicate.
    search_index.index_page(
        stem="gradient-descent",
        title="Gradient Descent",
        content="gradient based optimization method",
        content_type="concept",
        domains=["math"],
        tags=[],
        word_count=4,
        path="concepts/gradient-descent.md",
    )
    search_index.index_page(
        stem="gradient-descent-drill",
        title="Gradient Descent drill",
        content="gradient based optimization method",
        content_type="problem",
        domains=["math"],
        tags=[],
        word_count=4,
        path="problems/gradient-descent-drill.md",
    )
    search_index.embed_pending()

    assert search_index.near_duplicate_pairs(min_similarity=0.95) == []


def test_near_duplicate_pairs_empty_without_semantic(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "s.db")
    assert index.near_duplicate_pairs() == []


def test_near_duplicate_pairs_excludes_dismissed(search_index: SearchIndex) -> None:
    _index_sample(search_index)
    search_index.index_page(
        stem="gradient-descent-2",
        title="Gradient methods",
        content="gradient stepping toward minima",
        content_type="concept",
        domains=["math"],
        tags=[],
        word_count=4,
        path="concepts/gradient-descent-2.md",
    )
    search_index.embed_pending()

    pairs = search_index.near_duplicate_pairs(
        min_similarity=0.95,
        exclude={("gradient-descent", "gradient-descent-2")},
    )

    assert ("gradient-descent", "gradient-descent-2") not in [(a, b) for a, b, _ in pairs]
