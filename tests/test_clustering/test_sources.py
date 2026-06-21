"""Tests for source embedding/grouping and the clusterer factory."""

from __future__ import annotations

from pathlib import Path

import pytest

from second_brain.clustering import (
    ThresholdClusterer,
    cluster_scoped_sources,
    cluster_sources,
    get_clusterer,
)
from second_brain.clustering import sources as sources_mod
from second_brain.config import ClusteringConfig, SearchConfig


def _write(raw_dir: Path, rel: str, text: str) -> None:
    path = raw_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_factory_returns_threshold_clusterer() -> None:
    clusterer = get_clusterer(ClusteringConfig(algorithm="threshold", threshold=0.7))
    assert isinstance(clusterer, ThresholdClusterer)


def test_factory_returns_hdbscan_clusterer() -> None:
    from second_brain.clustering.hdbscan import HdbscanClusterer

    clusterer = get_clusterer(ClusteringConfig(algorithm="hdbscan"))
    assert isinstance(clusterer, HdbscanClusterer)


def test_cluster_sources_groups_by_embedding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "raw"
    paths = ["chatgpt/a.md", "chatgpt/b.md", "chatgpt/c.md"]
    _write(raw, "chatgpt/a.md", "alpha alpha")
    _write(raw, "chatgpt/b.md", "alpha beta")
    _write(raw, "chatgpt/c.md", "zeta zeta")

    vectors = {
        "alpha alpha": [1.0, 0.0],
        "alpha beta": [0.98, 0.02],
        "zeta zeta": [0.0, 1.0],
    }
    monkeypatch.setattr(sources_mod, "embed_text", lambda text, config: vectors[text])

    clusters = cluster_sources(
        paths, raw, SearchConfig(), ThresholdClusterer(threshold=0.9), signature_chars=100
    )

    # a and b cluster together; c is alone; nothing dropped.
    assert any(sorted(c) == ["chatgpt/a.md", "chatgpt/b.md"] for c in clusters)
    assert ["chatgpt/c.md"] in clusters
    assert sorted(p for c in clusters for p in c) == sorted(paths)


def test_embed_failure_becomes_singleton_not_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "raw"
    paths = ["chatgpt/a.md", "chatgpt/b.md"]
    _write(raw, "chatgpt/a.md", "alpha")
    _write(raw, "chatgpt/b.md", "beta")

    # b fails to embed (e.g. Ollama hiccup) -> must still appear, as a singleton.
    monkeypatch.setattr(
        sources_mod,
        "embed_text",
        lambda text, config: [1.0, 0.0] if "alpha" in text else None,
    )

    clusters = cluster_sources(paths, raw, SearchConfig(), ThresholdClusterer(threshold=0.5))

    assert ["chatgpt/b.md"] in clusters
    assert sorted(p for c in clusters for p in c) == sorted(paths)


def test_scoped_clustering_clusters_only_in_scope_lanes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "raw"
    paths = ["chatgpt/a.md", "chatgpt/b.md", "documents/c.md", "documents/d.md"]
    for rel in paths:
        _write(raw, rel, rel)

    # Identical vectors so any in-scope pair would cluster if eligible.
    monkeypatch.setattr(sources_mod, "embed_text", lambda text, config: [1.0, 0.0])

    clusters = cluster_scoped_sources(
        paths, raw, SearchConfig(), ThresholdClusterer(threshold=0.5), ("chatgpt",)
    )

    # Chats cluster together; documents stay one-per-source.
    assert ["chatgpt/a.md", "chatgpt/b.md"] in [sorted(c) for c in clusters]
    assert ["documents/c.md"] in clusters
    assert ["documents/d.md"] in clusters
    assert sorted(p for c in clusters for p in c) == sorted(paths)
