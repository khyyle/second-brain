"""Tests for operations on one source across every stage's records."""

from __future__ import annotations

from second_brain.compilation.store import BuildStore
from second_brain.config import Config
from second_brain.ingestion.manifest import Manifest
from second_brain.sources import forget_source
from second_brain.triage.store import TriageStore


def test_forgetting_a_source_clears_every_stage(
    config: Config, manifest: Manifest, triage_store: TriageStore, build_store: BuildStore
) -> None:
    drop = config.drops_dir / "documents" / "notes.md"
    drop.write_text("notes", encoding="utf-8")
    manifest.mark_processing(drop, "documents")
    manifest.mark_complete(drop, parse_lane="passthrough", raw_output="documents/notes.md")
    triage_store.record("documents/notes.md", "worthwhile", confidence=1.0)
    build_store.mark_compiled({"documents/notes.md": "hash"})
    build_store.defer_sources({"documents/notes.md": "hash"}, "did not finish", retryable=True)

    forget_source(config, "documents/notes.md")

    assert manifest.get_entry(drop) is None
    assert triage_store.get_decisions() == {}
    assert build_store.get_compiled() == {}
    assert build_store.get_deferred() == {}
