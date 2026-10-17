"""Tests for the derived state artifact the menu bar app reads."""

from __future__ import annotations

import json
from pathlib import Path

from second_brain.api.state import compute_state
from second_brain.compilation.clustering.preview import CLUSTERS_FILENAME
from second_brain.compilation.store import BuildStore
from second_brain.config import Config
from second_brain.hashing import hash_file
from second_brain.ingestion.manifest import Manifest
from second_brain.ingestion.triage.store import TriageStore


def _seed(config: Config, *names: str) -> None:
    raw = config.raw_dir / "documents"
    raw.mkdir(parents=True, exist_ok=True)
    for name in names:
        (raw / name).write_text("body " * 50, encoding="utf-8")


def test_compute_state_lists_staged_sources(config: Config) -> None:
    _seed(config, "a.md", "b.md")

    state = compute_state(config)

    assert sorted(s["rel"] for s in state["staged"]) == ["documents/a.md", "documents/b.md"]
    assert all(s["bytes"] > 0 for s in state["staged"])
    assert state["built_count"] == 0
    assert state["stale"] is False
    assert state["costs"] and all(cost >= 0 for cost in state["costs"].values())


def test_compute_state_excludes_undecided_chat(config: Config) -> None:
    chat = config.raw_dir / "chatgpt"
    chat.mkdir(parents=True, exist_ok=True)
    (chat / "chat.md").write_text("body " * 50, encoding="utf-8")
    _seed(config, "doc.md")

    state = compute_state(config)

    rels = [entry["rel"] for entry in state["staged"]]
    assert "chatgpt/chat.md" not in rels
    assert "documents/doc.md" in rels


def test_compute_state_excludes_skipped(config: Config, triage_store: TriageStore) -> None:
    _seed(config, "a.md", "b.md")
    triage_store.record("documents/b.md", "skip", confidence=1.0, reason="manual")

    state = compute_state(config)

    assert [s["rel"] for s in state["staged"]] == ["documents/a.md"]


def test_compute_state_flags_drifted_preview(config: Config) -> None:
    _seed(config, "a.md", "b.md")
    # A preview that covers only one of the two staged sources is stale.
    artifact = {
        "groups": [{"id": "g", "title": "a", "members": [{"rel": "documents/a.md", "bytes": 1}]}]
    }
    (config.data_dir / CLUSTERS_FILENAME).write_text(json.dumps(artifact), encoding="utf-8")

    state = compute_state(config)

    assert state["stale"] is True


def test_compute_state_counts_needs_review(config: Config, triage_store: TriageStore) -> None:
    _seed(config, "present.md")
    triage_store.record("documents/present.md", "review", confidence=1.0, reason="unsure")
    triage_store.record("documents/missing.md", "review", confidence=1.0, reason="unsure")

    state = compute_state(config)

    assert state["needs_review"] == 1


def test_compute_state_counts_skipped(config: Config, triage_store: TriageStore) -> None:
    _seed(config, "in_raw.md")
    skipped = config.raw_dir / ".skipped" / "documents"
    skipped.mkdir(parents=True, exist_ok=True)
    (skipped / "in_skipped.md").write_text("body " * 50, encoding="utf-8")
    triage_store.record("documents/in_raw.md", "skip", confidence=1.0, reason="manual")
    triage_store.record("documents/in_skipped.md", "skip", confidence=1.0, reason="manual")
    triage_store.record("documents/gone.md", "skip", confidence=1.0, reason="manual")

    state = compute_state(config)

    assert state["skipped"] == 1


def test_set_aside_sources_say_whether_a_retry_can_help(
    config: Config, build_store: BuildStore
) -> None:
    _seed(config, "long.md", "huge.md")
    raw_dir = config.raw_dir
    build_store.defer_sources(
        {"documents/long.md": hash_file(raw_dir / "documents/long.md")},
        "The model didn't finish.",
        retryable=True,
    )
    build_store.defer_sources(
        {"documents/huge.md": hash_file(raw_dir / "documents/huge.md")},
        "Too large to build.",
        retryable=False,
    )

    state = compute_state(config)

    retryable = {entry["rel"]: entry["retryable"] for entry in state["staged"]}
    assert retryable == {"documents/huge.md": False, "documents/long.md": True}


def test_failed_ingests_carry_their_reason(
    config: Config, manifest: Manifest, tmp_path: Path
) -> None:
    drop = tmp_path / "scan.pdf"
    drop.write_bytes(b"%PDF-1.4\n")
    manifest.mark_processing(drop, "documents")
    manifest.mark_failed(drop, "Chandra failed on page 3")

    state = compute_state(config)

    assert state["ingest_failures"] == {str(drop): "Chandra failed on page 3"}
