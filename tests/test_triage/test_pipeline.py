"""Tests for triage source-lane scoping in the pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from second_brain.config import Config
from second_brain.ingestion.manifest import Manifest
from second_brain.ollama import OllamaUnavailableError
from second_brain.status import STATUS_FILENAME
from second_brain.triage import pipeline as pipeline_mod
from second_brain.triage.gemma import TriageDecision, TriageResult


def _write(raw_dir: Path, rel: str, text: str) -> None:
    path = raw_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_triage_only_touches_scoped_lanes(
    config: Config, manifest: Manifest, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(config.raw_dir, "chatgpt/chat.md", "chat body " * 100)
    _write(config.raw_dir, "documents/doc.md", "doc body " * 100)

    monkeypatch.setattr(
        pipeline_mod,
        "triage_file",
        lambda path, cfg: TriageResult(decision=TriageDecision.WORTHWHILE, confidence=0.9),
    )

    counts = pipeline_mod.triage_pending(config, manifest)

    decisions = manifest.get_triage_decisions()
    assert "chatgpt/chat.md" in decisions  # in-scope lane is triaged
    assert "documents/doc.md" not in decisions  # out-of-scope lane gets no row
    assert counts["worthwhile"] == 1  # only the chat is counted


def test_triage_skips_a_vanished_source(
    config: Config, manifest: Manifest, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A source deleted mid-run must be skipped, not crash the whole pass.
    _write(config.raw_dir, "chatgpt/present.md", "body " * 100)
    _write(config.raw_dir, "chatgpt/vanished.md", "body " * 100)

    def fake_triage(path: Path, cfg: object) -> TriageResult:
        if path.name == "vanished.md":
            raise FileNotFoundError(path)
        return TriageResult(decision=TriageDecision.WORTHWHILE, confidence=0.9)

    monkeypatch.setattr(pipeline_mod, "triage_file", fake_triage)

    counts = pipeline_mod.triage_pending(config, manifest)

    decisions = manifest.get_triage_decisions()
    assert "chatgpt/present.md" in decisions  # the good one still recorded
    assert "chatgpt/vanished.md" not in decisions  # skipped, not crashed
    assert counts["worthwhile"] == 1


def test_triage_stops_when_ollama_stops(
    config: Config, manifest: Manifest, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("a.md", "b.md", "c.md"):
        _write(config.raw_dir, f"chatgpt/{name}", "body " * 100)

    def fake_triage(path: Path, cfg: object) -> TriageResult:
        if path.name == "b.md":
            raise OllamaUnavailableError("down")
        return TriageResult(decision=TriageDecision.WORTHWHILE, confidence=0.9)

    monkeypatch.setattr(pipeline_mod, "triage_file", fake_triage)

    with pytest.raises(OllamaUnavailableError, match="2 chats are waiting to be sorted"):
        pipeline_mod.triage_pending(config, manifest)

    decisions = manifest.get_triage_decisions()
    assert "chatgpt/a.md" in decisions
    assert "chatgpt/b.md" not in decisions
    assert "chatgpt/c.md" not in decisions
    status = json.loads((config.data_dir / STATUS_FILENAME).read_text(encoding="utf-8"))
    assert status["running"] is False


def test_worthwhile_sources_holds_undecided_chats(config: Config, manifest: Manifest) -> None:
    sources = ["chatgpt/chat.md", "documents/doc.md"]

    assert pipeline_mod.worthwhile_sources(config, manifest, sources) == ["documents/doc.md"]

    disabled = config.model_copy(
        update={"triage": config.triage.model_copy(update={"enabled": False})}
    )
    assert pipeline_mod.worthwhile_sources(disabled, manifest, sources) == sources
