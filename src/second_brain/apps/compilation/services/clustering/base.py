"""Swappable clustering interface for grouping related sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from numpy.typing import ArrayLike


class Clusterer(ABC):
    """Partition embedding vectors into clusters.

    The interface lets the compiler select its grouping strategy at
    runtime without changing callers.
    """

    @abstractmethod
    def cluster(self, vectors: Sequence[ArrayLike]) -> list[list[int]]:
        """
        Partition ``vectors`` into clusters of their row indices.

        Parameters
        ----------
        vectors: Sequence[ArrayLike]
            One embedding per item, all of equal dimensionality.

        Returns
        -------
        list[list[int]]
            Groups of indices into ``vectors``. The groups form a
            partition: every index appears in exactly one group, so no
            item is ever dropped (unclustered items are singletons).
        """
        ...
