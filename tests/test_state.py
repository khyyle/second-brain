"""Tests for the derived state artifact the menu bar app reads."""

from __future__ import annotations

import json
from pathlib import Path

from second_brain.clustering.preview import CLUSTERS_FILENAME
from second_brain.config import Config
from second_brain.ingestion.manifest import Manifest
from second_brain.state import compute_state


def _seed(config: Config, *names: str) -> None:
    raw = config.raw_dir / "documents"
    raw.mkdir(parents=True, exist_ok=True)
    for name in names:
        (raw / name).write_text("body " * 50, encoding="utf-8")


def test_compute_state_lists_staged_sources(tmp_path: Path) -> None:
    config = Config(data_dir=tmp_path / "sb")
    _seed(config, "a.md", "b.md")
    manifest = Manifest(config.manifest_db_path)

    state = compute_state(config, manifest)

    assert sorted(s["rel"] for s in state["staged"]) == ["documents/a.md", "documents/b.md"]
    assert all(s["bytes"] > 0 for s in state["staged"])
    assert state["built_count"] == 0
    assert state["stale"] is False
    assert state["costs"] and all(cost >= 0 for cost in state["costs"].values())


def test_compute_state_excludes_skipped(tmp_path: Path) -> None:
    config = Config(data_dir=tmp_path / "sb")
    _seed(config, "a.md", "b.md")
    manifest = Manifest(config.manifest_db_path)
    manifest.record_triage("documents/b.md", "skip", confidence=1.0, reason="manual")

    state = compute_state(config, manifest)

    assert [s["rel"] for s in state["staged"]] == ["documents/a.md"]


def test_compute_state_flags_drifted_preview(tmp_path: Path) -> None:
    config = Config(data_dir=tmp_path / "sb")
    _seed(config, "a.md", "b.md")
    manifest = Manifest(config.manifest_db_path)
    # A preview that covers only one of the two staged sources is stale.
    artifact = {
        "groups": [{"id": "g", "title": "a", "members": [{"rel": "documents/a.md", "bytes": 1}]}]
    }
    (config.data_dir / CLUSTERS_FILENAME).write_text(json.dumps(artifact), encoding="utf-8")

    state = compute_state(config, manifest)

    assert state["stale"] is True
