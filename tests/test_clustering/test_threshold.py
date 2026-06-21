"""Tests for the single-linkage threshold clusterer."""

from __future__ import annotations

from second_brain.clustering.threshold import ThresholdClusterer


def _flat(groups: list[list[int]]) -> list[int]:
    return sorted(index for group in groups for index in group)


def test_empty_input() -> None:
    assert ThresholdClusterer().cluster([]) == []


def test_partitions_into_two_clusters_and_a_singleton() -> None:
    vectors = [
        [1.0, 0.0, 0.0],
        [0.99, 0.01, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.98, 0.02],
        [0.0, 0.0, 1.0],
    ]
    groups = ThresholdClusterer(threshold=0.9).cluster(vectors)

    assert _flat(groups) == [0, 1, 2, 3, 4]  # partition: every index once
    assert sorted(len(g) for g in groups) == [1, 2, 2]


def test_high_threshold_keeps_everything_separate() -> None:
    vectors = [[1.0, 0.0], [0.0, 1.0], [0.7, 0.7]]
    groups = ThresholdClusterer(threshold=0.999).cluster(vectors)
    assert sorted(len(g) for g in groups) == [1, 1, 1]


def test_low_threshold_merges_everything() -> None:
    vectors = [[1.0, 0.0], [0.9, 0.1], [0.8, 0.2]]
    groups = ThresholdClusterer(threshold=0.1).cluster(vectors)
    assert len(groups) == 1
    assert sorted(groups[0]) == [0, 1, 2]


def test_single_linkage_chains_transitively() -> None:
    # a~b and b~c but a is not directly similar to c; single linkage still
    # merges all three (the chaining behavior a denser clusterer avoids).
    vectors = [[1.0, 0.0], [0.95, 0.31], [0.8, 0.6]]
    groups = ThresholdClusterer(threshold=0.93).cluster(vectors)
    assert len(groups) == 1
    assert sorted(groups[0]) == [0, 1, 2]
