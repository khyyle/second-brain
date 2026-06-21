"""Tests for triage source-lane scoping in the pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from second_brain.config import Config
from second_brain.ingestion.manifest import Manifest
from second_brain.triage import pipeline as pipeline_mod
from second_brain.triage.gemma import TriageDecision, TriageResult


def _write(raw_dir: Path, rel: str, text: str) -> None:
    path = raw_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_triage_only_touches_scoped_lanes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config(data_dir=tmp_path)
    config.ensure_directories()
    _write(config.raw_dir, "chatgpt/chat.md", "chat body " * 100)
    _write(config.raw_dir, "documents/doc.md", "doc body " * 100)

    monkeypatch.setattr(
        pipeline_mod,
        "triage_file",
        lambda path, cfg: TriageResult(decision=TriageDecision.WORTHWHILE, confidence=0.9),
    )

    counts = pipeline_mod.triage_pending(config, Manifest(config.manifest_db_path))

    decisions = Manifest(config.manifest_db_path).get_triage_decisions()
    assert "chatgpt/chat.md" in decisions  # in-scope lane is triaged
    assert "documents/doc.md" not in decisions  # out-of-scope lane gets no row
    assert counts["worthwhile"] == 1  # only the chat is counted


def test_triage_skips_a_vanished_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A source deleted mid-run must be skipped, not crash the whole pass.
    config = Config(data_dir=tmp_path)
    config.ensure_directories()
    _write(config.raw_dir, "chatgpt/present.md", "body " * 100)
    _write(config.raw_dir, "chatgpt/vanished.md", "body " * 100)

    def fake_triage(path: Path, cfg: object) -> TriageResult:
        if path.name == "vanished.md":
            raise FileNotFoundError(path)
        return TriageResult(decision=TriageDecision.WORTHWHILE, confidence=0.9)

    monkeypatch.setattr(pipeline_mod, "triage_file", fake_triage)

    counts = pipeline_mod.triage_pending(config, Manifest(config.manifest_db_path))

    decisions = Manifest(config.manifest_db_path).get_triage_decisions()
    assert "chatgpt/present.md" in decisions  # the good one still recorded
    assert "chatgpt/vanished.md" not in decisions  # skipped, not crashed
    assert counts["worthwhile"] == 1


def test_untriaged_documents_still_compile_via_fail_open(
    tmp_path: Path,
) -> None:
    # A document with no triage decision must remain compilable.
    config = Config(data_dir=tmp_path)
    config.ensure_directories()
    _write(config.raw_dir, "documents/doc.md", "doc body")

    kept = pipeline_mod.worthwhile_sources(
        Manifest(config.manifest_db_path), ["documents/doc.md"]
    )
    assert kept == ["documents/doc.md"]
