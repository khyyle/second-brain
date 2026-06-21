"""Swappable clustering interface for grouping related sources."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Clusterer(ABC):
    """Partition embedding vectors into clusters.

    The interface lets the compiler select its grouping strategy at
    runtime without changing callers.
    """

    @abstractmethod
    def cluster(self, vectors: list[list[float]]) -> list[list[int]]:
        """
        Partition ``vectors`` into clusters of their row indices.

        Parameters
        ----------
        vectors: list[list[float]]
            One embedding per item, all of equal dimensionality.

        Returns
        -------
        list[list[int]]
            Groups of indices into ``vectors``. The groups form a
            partition: every index appears in exactly one group, so no
            item is ever dropped (unclustered items are singletons).
        """
        ...
