"""Embed raw sources and group them into clusters for compilation."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from second_brain.clustering.base import Clusterer
from second_brain.config import SearchConfig
from second_brain.mcp_server.embeddings import embed_text

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]


def embed_sources(
    raw_paths: list[str],
    raw_dir: Path,
    search_config: SearchConfig,
    signature_chars: int,
    progress: ProgressCallback | None = None,
) -> tuple[list[str], list[list[float]]]:
    """
    Embed each source's opening as a topical fingerprint.

    Parameters
    ----------
    raw_paths: list[str]
        Source paths relative to ``raw_dir``.
    raw_dir: Path
        Root of the raw parsed sources.
    search_config: SearchConfig
        Embedding model and Ollama settings.
    signature_chars: int
        How much of each source's opening to embed.
    progress: ProgressCallback | None
        Called with ``(index, total)`` — the 0-based index of the source
        being embedded — as each is processed, matching the pipeline's
        status-heartbeat convention for a live progress readout.

    Returns
    -------
    embedded: list[str]
        Paths that embedded successfully, aligned with ``vectors``.
    vectors: list[list[float]]
        Their embedding vectors.
    """
    embedded: list[str] = []
    vectors: list[list[float]] = []
    total = len(raw_paths)
    for index, rel in enumerate(raw_paths):
        if progress is not None:
            progress(index, total)
        try:
            text = (raw_dir / rel).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Could not read %s for clustering: %s", rel, exc)
            continue
        vector = embed_text(text[:signature_chars], search_config)
        if vector is None:
            continue
        embedded.append(rel)
        vectors.append(vector)
    return embedded, vectors


def _lane(rel_path: str) -> str:
    """Return a raw path's source lane (its first path component)."""
    parts = Path(rel_path).parts
    return parts[0] if parts else ""


def cluster_scoped_sources(
    raw_paths: list[str],
    raw_dir: Path,
    search_config: SearchConfig,
    clusterer: Clusterer,
    in_scope_lanes: tuple[str, ...],
    signature_chars: int = 8000,
    progress: ProgressCallback | None = None,
) -> list[list[str]]:
    """Cluster only sources in the configured lanes; pass others through.

    Sources whose lane is outside ``in_scope_lanes`` become singletons, so
    deliberately dropped material compiles one source per run while bulk
    imports are grouped.

    Parameters
    ----------
    raw_paths: list[str]
        Source paths relative to ``raw_dir``.
    raw_dir: Path
        Root of the raw parsed sources.
    search_config: SearchConfig
        Embedding settings.
    clusterer: Clusterer
        Strategy that partitions in-scope embeddings into groups.
    in_scope_lanes: tuple[str, ...]
        Lanes eligible for clustering.
    signature_chars: int
        How much of each source to embed as its fingerprint.

    Returns
    -------
    list[list[str]]
        Clusters covering every input path exactly once.
    """
    in_scope = [rel for rel in raw_paths if _lane(rel) in in_scope_lanes]
    out_of_scope = [rel for rel in raw_paths if _lane(rel) not in in_scope_lanes]

    clusters: list[list[str]] = []
    if in_scope:
        clusters.extend(
            cluster_sources(
                in_scope, raw_dir, search_config, clusterer, signature_chars, progress
            )
        )
    clusters.extend([rel] for rel in out_of_scope)
    return clusters


def cluster_sources(
    raw_paths: list[str],
    raw_dir: Path,
    search_config: SearchConfig,
    clusterer: Clusterer,
    signature_chars: int = 8000,
    progress: ProgressCallback | None = None,
) -> list[list[str]]:
    """
    Group related sources into clusters, preserving every source.

    Sources that fail to embed (e.g. Ollama unavailable) are returned as
    their own singleton clusters, so nothing is dropped from the build.

    Parameters
    ----------
    raw_paths: list[str]
        Source paths relative to ``raw_dir``.
    raw_dir: Path
        Root of the raw parsed sources.
    search_config: SearchConfig
        Embedding settings.
    clusterer: Clusterer
        Strategy that partitions embedding vectors into groups.
    signature_chars: int
        How much of each source to embed as its fingerprint.

    Returns
    -------
    list[list[str]]
        Clusters of source paths covering every input path exactly once.
    """
    embedded, vectors = embed_sources(
        raw_paths, raw_dir, search_config, signature_chars, progress
    )
    embedded_set = set(embedded)
    failed = [path for path in raw_paths if path not in embedded_set]

    clusters: list[list[str]] = []
    if vectors:
        for group in clusterer.cluster(vectors):
            clusters.append([embedded[i] for i in group])
    clusters.extend([path] for path in failed)
    return clusters
