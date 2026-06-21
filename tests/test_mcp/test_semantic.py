"""Tests for the optional semantic (embedding) search layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from second_brain.config import SearchConfig
from second_brain.mcp_server import embeddings as embeddings_mod
from second_brain.mcp_server.search import SearchIndex

# Tiny deterministic "embeddings": map keywords to fixed 3-d vectors so
# nearest-neighbor ordering is predictable without a real model.
_VECTORS = {
    "gradient": [1.0, 0.0, 0.0],
    "optimization": [0.9, 0.1, 0.0],
    "penguin": [0.0, 1.0, 0.0],
    "biology": [0.0, 0.9, 0.1],
}


def _fake_embed(text: str, config: SearchConfig) -> list[float] | None:
    lowered = text.lower()
    for key, vec in _VECTORS.items():
        if key in lowered:
            return vec
    return [0.0, 0.0, 1.0]


@pytest.fixture
def semantic_config() -> SearchConfig:
    return SearchConfig(embedding_dimensions=3, semantic_enabled=True)


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


def test_semantic_enabled_when_configured(tmp_path: Path, semantic_config: SearchConfig) -> None:
    index = SearchIndex(tmp_path / "s.db", semantic_config)
    assert index.semantic_enabled is True


def test_semantic_search_returns_nearest(tmp_path: Path, semantic_config: SearchConfig) -> None:
    index = SearchIndex(tmp_path / "s.db", semantic_config)
    _index_sample(index)

    hits = index.semantic_search("gradient optimization", limit=1)
    assert len(hits) == 1
    assert hits[0].stem == "gradient-descent"

    hits2 = index.semantic_search("penguin", limit=1)
    assert hits2[0].stem == "penguins"


def test_semantic_disabled_without_config(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "s.db")  # no SearchConfig -> keyword only
    assert index.semantic_enabled is False
    assert index.semantic_search("anything") == []


def test_semantic_returns_empty_when_embeddings_unavailable(
    tmp_path: Path, semantic_config: SearchConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = SearchIndex(tmp_path / "s.db", semantic_config)
    _index_sample(index)
    # Simulate Ollama going away: embed_text now returns None.
    monkeypatch.setattr(embeddings_mod, "embed_text", lambda text, config: None)
    assert index.semantic_search("gradient") == []


def test_keyword_search_still_works_with_semantic_on(
    tmp_path: Path, semantic_config: SearchConfig
) -> None:
    index = SearchIndex(tmp_path / "s.db", semantic_config)
    _index_sample(index)
    hits = index.search("penguin")
    assert any(h.stem == "penguins" for h in hits)
