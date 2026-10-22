"""Density-based clustering of embeddings via HDBSCAN."""

from __future__ import annotations

import numpy as np
from sklearn.cluster import HDBSCAN

from second_brain.compilation.clustering.base import Clusterer

DEFAULT_MIN_CLUSTER_SIZE = 2
NOISE_LABEL = -1
# HDBSCAN never reports a whole batch as one cluster, so a small batch with a
# single topic in it comes back as all singletons; below this size the
# threshold rule groups instead.
MIN_SOURCES_FOR_DENSITY = 10


class HdbscanClusterer(Clusterer):
    """Cluster embeddings by density, isolating sparse points as singletons.

    HDBSCAN finds clusters of varying density and labels low-density points
    as noise, so it avoids the chaining a single fixed similarity threshold
    suffers on large groups. Vectors are L2-normalized so Euclidean distance
    ranks pairs by cosine similarity.

    Parameters
    ----------
    min_cluster_size: int
        Smallest grouping HDBSCAN will treat as a cluster. Sparser or
        smaller groupings are returned as their own singletons.
    small_batch_clusterer: Clusterer | None
        Used instead for batches under ``MIN_SOURCES_FOR_DENSITY`` items.
    """

    def __init__(
        self,
        min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
        small_batch_clusterer: Clusterer | None = None,
    ) -> None:
        self._min_cluster_size = min_cluster_size
        self._small_batch_clusterer = small_batch_clusterer

    def cluster(self, vectors: list[list[float]]) -> list[list[int]]:
        """Partition vectors into density-based clusters.

        Parameters
        ----------
        vectors: list[list[float]]
            One embedding per item.

        Returns
        -------
        list[list[int]]
            A partition of ``range(len(vectors))``. Points labelled noise
            each become their own singleton, so no item is dropped.
        """
        if len(vectors) < 2:
            return [[i] for i in range(len(vectors))]
        if self._small_batch_clusterer is not None and len(vectors) < MIN_SOURCES_FOR_DENSITY:
            return self._small_batch_clusterer.cluster(vectors)

        matrix = np.asarray(vectors, dtype=float)
        magnitudes = np.linalg.norm(matrix, axis=1, keepdims=True)
        magnitudes[magnitudes == 0] = 1.0
        units = matrix / magnitudes

        labels = HDBSCAN(min_cluster_size=self._min_cluster_size, copy=True).fit_predict(units)

        clusters: dict[int, list[int]] = {}
        singletons: list[list[int]] = []
        for index, label in enumerate(labels):
            if label == NOISE_LABEL:
                singletons.append([index])
            else:
                clusters.setdefault(int(label), []).append(index)
        return list(clusters.values()) + singletons
