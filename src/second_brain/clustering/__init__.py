"""Source clustering for the compilation step.

Groups related raw sources so the compiler synthesizes one topic in a
single agent run rather than one run per conversation. The grouping
algorithm is selected by configuration.
"""

from __future__ import annotations

from second_brain.clustering.base import Clusterer
from second_brain.clustering.sources import (
    cluster_scoped_sources,
    cluster_sources,
    embed_sources,
)
from second_brain.clustering.threshold import ThresholdClusterer
from second_brain.config import ClusteringConfig

__all__ = [
    "Clusterer",
    "ThresholdClusterer",
    "cluster_scoped_sources",
    "cluster_sources",
    "embed_sources",
    "get_clusterer",
]


def get_clusterer(config: ClusteringConfig) -> Clusterer:
    """
    Build the clusterer selected by configuration.

    Parameters
    ----------
    config: ClusteringConfig
        Clustering settings (selects the algorithm and its parameters).

    Returns
    -------
    Clusterer
        The clusterer implementation for ``config.algorithm``.

    Raises
    ------
    ValueError
        If ``config.algorithm`` is unknown.
    """
    if config.algorithm == "threshold":
        return ThresholdClusterer(threshold=config.threshold)
    if config.algorithm == "hdbscan":
        from second_brain.clustering.hdbscan import HdbscanClusterer

        return HdbscanClusterer(min_cluster_size=config.hdbscan_min_cluster_size)
    raise ValueError(f"unknown clustering algorithm: {config.algorithm}")
