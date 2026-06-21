"""Tests for the HDBSCAN density clusterer."""

from __future__ import annotations

import numpy as np

from second_brain.clustering.hdbscan import HdbscanClusterer


def _flat(groups: list[list[int]]) -> list[int]:
    return sorted(index for group in groups for index in group)


def test_empty_input() -> None:
    assert HdbscanClusterer().cluster([]) == []


def test_single_input_is_singleton() -> None:
    assert HdbscanClusterer().cluster([[1.0, 0.0]]) == [[0]]


def test_dense_blobs_cluster_and_outlier_is_isolated() -> None:
    rng = np.random.default_rng(0)
    blob_a = rng.normal([5.0, 0.0, 0.0], 0.05, (8, 3))
    blob_b = rng.normal([0.0, 5.0, 0.0], 0.05, (8, 3))
    outlier = np.array([[0.0, 0.0, 9.0]])
    vectors = np.vstack([blob_a, blob_b, outlier]).tolist()

    groups = HdbscanClusterer(min_cluster_size=3).cluster(vectors)

    assert _flat(groups) == list(range(17))  # partition: nothing dropped
    blob_a_group = next(group for group in groups if 0 in group)
    blob_b_group = next(group for group in groups if 8 in group)
    assert set(range(0, 8)) <= set(blob_a_group)
    assert set(range(8, 16)) <= set(blob_b_group)
    assert set(blob_a_group).isdisjoint(blob_b_group)  # two distinct clusters
    assert [16] in groups  # the outlier is its own singleton
