"""Single-linkage clustering over embedding cosine similarity."""

from __future__ import annotations

import numpy as np

from second_brain.clustering.base import Clusterer

# Balances under-merging (related topics left separate) against the
# single-linkage chaining that lower thresholds cause.
DEFAULT_SIMILARITY_THRESHOLD = 0.82


class ThresholdClusterer(Clusterer):
    """Cluster items by connected components of a cosine-similarity graph.

    Two items join the same cluster when their cosine similarity meets the
    threshold. Single linkage favors recall: a chain of pairwise-similar
    items collapses into one cluster even when its ends are dissimilar.

    Parameters
    ----------
    threshold: float
        Cosine similarity at/above which two items join the same cluster.
    """

    def __init__(self, threshold: float = DEFAULT_SIMILARITY_THRESHOLD) -> None:
        self._threshold = threshold

    def cluster(self, vectors: list[list[float]]) -> list[list[int]]:
        """Group vectors by connected components of the similarity graph.

        Parameters
        ----------
        vectors: list[list[float]]
            One embedding per item.

        Returns
        -------
        list[list[int]]
            A partition of ``range(len(vectors))`` into clusters.
        """
        count = len(vectors)
        if count == 0:
            return []

        matrix = np.asarray(vectors, dtype=float)
        magnitudes = np.linalg.norm(matrix, axis=1, keepdims=True)
        magnitudes[magnitudes == 0] = 1.0
        units = matrix / magnitudes
        similarity = units @ units.T

        parent = list(range(count))

        def find(node: int) -> int:
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def union(left: int, right: int) -> None:
            root_left, root_right = find(left), find(right)
            if root_left != root_right:
                parent[root_left] = root_right

        rows, cols = np.nonzero(np.triu(similarity >= self._threshold, k=1))
        for left, right in zip(rows.tolist(), cols.tolist()):
            union(left, right)

        clusters: dict[int, list[int]] = {}
        for index in range(count):
            clusters.setdefault(find(index), []).append(index)
        return list(clusters.values())
