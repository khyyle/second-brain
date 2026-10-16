"""Tests for dismissed duplicate pairs."""

from __future__ import annotations

from second_brain.config import Config
from second_brain.wiki.store import DuplicateStore


def test_dismissing_a_pair_is_order_independent_and_idempotent(config: Config) -> None:
    duplicate_store = DuplicateStore(config.manifest_db_path)

    duplicate_store.dismiss("water-electrolysis", "electrolysis")
    duplicate_store.dismiss("electrolysis", "water-electrolysis")

    assert duplicate_store.get_dismissed() == {("electrolysis", "water-electrolysis")}
