"""Tests for source embedding/grouping and the clusterer factory."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from second_brain.apps.compilation.services.clustering import (
    ThresholdClusterer,
    cluster_scoped_sources,
    cluster_sources,
    get_clusterer,
)
from second_brain.apps.compilation.services.clustering import sources as sources_mod
from second_brain.apps.inference.errors import InferenceUnavailableError
from second_brain.apps.inference.services.worker_client import EmbeddingRole
from second_brain.pipeline_config import ClusteringConfig, SearchConfig


def _write(raw_dir: Path, rel: str, text: str) -> None:
    path = raw_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_factory_returns_threshold_clusterer() -> None:
    clusterer = get_clusterer(ClusteringConfig(algorithm="threshold", threshold=0.7))
    assert isinstance(clusterer, ThresholdClusterer)


def test_factory_returns_hdbscan_clusterer() -> None:
    from second_brain.apps.compilation.services.clustering.hdbscan import HdbscanClusterer

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
    roles: list[EmbeddingRole] = []

    def fake_embed(model: str, text: str, role: EmbeddingRole) -> NDArray[np.float32]:
        assert model == "nomic-embed-text"
        roles.append(role)
        return np.asarray(vectors[text], dtype=np.float32)

    monkeypatch.setattr(sources_mod, "embed_text", fake_embed)

    clusters = cluster_sources(
        paths, raw, SearchConfig(), ThresholdClusterer(threshold=0.9), signature_chars=100
    )

    # a and b cluster together; c is alone; nothing dropped.
    assert any(sorted(c) == ["chatgpt/a.md", "chatgpt/b.md"] for c in clusters)
    assert ["chatgpt/c.md"] in clusters
    assert sorted(p for c in clusters for p in c) == sorted(paths)
    assert roles == ["clustering"] * len(paths)


def test_embed_failure_becomes_singleton_not_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "raw"
    paths = ["chatgpt/a.md", "chatgpt/b.md"]
    _write(raw, "chatgpt/a.md", "alpha")
    _write(raw, "chatgpt/b.md", "beta")

    # b returns no embedding and must still appear, as a singleton.
    monkeypatch.setattr(
        sources_mod,
        "embed_text",
        lambda model, text, role: (
            np.array([1.0, 0.0], dtype=np.float32) if "alpha" in text else None
        ),
    )

    clusters = cluster_sources(paths, raw, SearchConfig(), ThresholdClusterer(threshold=0.5))

    assert ["chatgpt/b.md"] in clusters
    assert sorted(p for c in clusters for p in c) == sorted(paths)


def test_embed_sources_propagates_inference_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "raw"
    _write(raw, "chatgpt/a.md", "alpha")

    def _raise(model: str, text: str, role: EmbeddingRole) -> NDArray[np.float32]:
        raise InferenceUnavailableError("down")

    monkeypatch.setattr(sources_mod, "embed_text", _raise)
    with pytest.raises(InferenceUnavailableError):
        sources_mod.embed_sources(["chatgpt/a.md"], raw, SearchConfig(), signature_chars=100)


def test_scoped_clustering_clusters_only_in_scope_lanes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "raw"
    paths = ["chatgpt/a.md", "chatgpt/b.md", "documents/c.md", "documents/d.md"]
    for rel in paths:
        _write(raw, rel, rel)

    # Identical vectors so any in-scope pair would cluster if eligible.
    monkeypatch.setattr(
        sources_mod, "embed_text", lambda model, text, role: np.array([1.0, 0.0], dtype=np.float32)
    )

    clusters = cluster_scoped_sources(
        paths, raw, SearchConfig(), ThresholdClusterer(threshold=0.5), ("chatgpt",)
    )

    # Chats cluster together; documents stay one-per-source.
    assert ["chatgpt/a.md", "chatgpt/b.md"] in [sorted(c) for c in clusters]
    assert ["documents/c.md"] in clusters
    assert ["documents/d.md"] in clusters
    assert sorted(p for c in clusters for p in c) == sorted(paths)
