"""Tests for triage source-lane scoping in the pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from second_brain.apps.inference.errors import InferenceUnavailableError
from second_brain.apps.ingestion.services.triage import pipeline as pipeline_mod
from second_brain.apps.ingestion.services.triage.classification import TriageDecision, TriageResult
from second_brain.apps.ingestion.services.triage.triage_decisions import TriageStore
from second_brain.pipeline_config import PipelineConfig

pytestmark = pytest.mark.django_db(transaction=True)


def _write(raw_dir: Path, rel: str, text: str) -> None:
    path = raw_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_triage_only_touches_scoped_lanes(
    config: PipelineConfig,
    triage_store: TriageStore,
    monkeypatch: pytest.MonkeyPatch,
    read_job_progress,
) -> None:
    _write(config.raw_dir, "chatgpt/chat.md", "chat body " * 100)
    _write(config.raw_dir, "documents/doc.md", "doc body " * 100)

    monkeypatch.setattr(
        pipeline_mod,
        "triage_file",
        lambda path, cfg: TriageResult(decision=TriageDecision.WORTHWHILE, confidence=0.9),
    )

    counts = pipeline_mod.triage_pending(config)

    decisions = triage_store.get_decisions()
    assert "chatgpt/chat.md" in decisions  # in-scope lane is triaged
    assert "documents/doc.md" not in decisions  # out-of-scope lane gets no row
    assert counts["worthwhile"] == 1  # only the chat is counted


def test_progress_names_the_chat_being_triaged_and_those_waiting(
    config: PipelineConfig, monkeypatch: pytest.MonkeyPatch, read_job_progress
) -> None:
    _write(config.raw_dir, "chatgpt/a.md", "chat body " * 100)
    _write(config.raw_dir, "chatgpt/b.md", "chat body " * 100)
    heartbeats: list[tuple[list[str], list[str]]] = []

    def fake_triage(path: Path, _config: object) -> TriageResult:
        heartbeat = read_job_progress()
        heartbeats.append((heartbeat["current_sources"], heartbeat["queued_sources"]))
        return TriageResult(decision=TriageDecision.WORTHWHILE, confidence=0.9)

    monkeypatch.setattr(pipeline_mod, "triage_file", fake_triage)

    pipeline_mod.triage_pending(config)

    assert heartbeats == [(["chatgpt/a.md"], ["chatgpt/b.md"]), (["chatgpt/b.md"], [])]


def test_each_decision_is_reported_and_a_skip_is_held_at_once(
    config: PipelineConfig, monkeypatch: pytest.MonkeyPatch, read_job_progress
) -> None:
    _write(config.raw_dir, "chatgpt/keep.md", "chat body " * 100)
    _write(config.raw_dir, "chatgpt/skip.md", "chat body " * 100)
    decisions = {"keep.md": TriageDecision.WORTHWHILE, "skip.md": TriageDecision.SKIP}
    monkeypatch.setattr(
        pipeline_mod,
        "triage_file",
        lambda path, _config: TriageResult(decision=decisions[path.name], confidence=0.9),
    )
    held_after_each: list[bool] = []

    pipeline_mod.triage_pending(
        config,
        on_decided=lambda: held_after_each.append(
            (config.raw_dir / ".skipped" / "chatgpt" / "skip.md").exists()
        ),
    )

    assert held_after_each == [False, True]


def test_triage_skips_a_vanished_source(
    config: PipelineConfig,
    triage_store: TriageStore,
    monkeypatch: pytest.MonkeyPatch,
    read_job_progress,
) -> None:
    # A source deleted mid-run must be skipped, not crash the whole pass.
    _write(config.raw_dir, "chatgpt/present.md", "body " * 100)
    _write(config.raw_dir, "chatgpt/vanished.md", "body " * 100)

    def fake_triage(path: Path, cfg: object) -> TriageResult:
        if path.name == "vanished.md":
            raise FileNotFoundError(path)
        return TriageResult(decision=TriageDecision.WORTHWHILE, confidence=0.9)

    monkeypatch.setattr(pipeline_mod, "triage_file", fake_triage)

    counts = pipeline_mod.triage_pending(config)

    decisions = triage_store.get_decisions()
    assert "chatgpt/present.md" in decisions  # the good one still recorded
    assert "chatgpt/vanished.md" not in decisions  # skipped, not crashed
    assert counts["worthwhile"] == 1


@pytest.mark.parametrize("completed_decision", list(TriageDecision))
def test_inference_failure_preserves_completed_work_and_leaves_rest_pending(
    config: PipelineConfig,
    triage_store: TriageStore,
    monkeypatch: pytest.MonkeyPatch,
    read_job_progress,
    completed_decision: TriageDecision,
) -> None:
    for name in ("a.md", "b.md", "c.md"):
        _write(config.raw_dir, f"chatgpt/{name}", "body " * 100)
    failure = InferenceUnavailableError("Local inference is still loading.")
    attempted: list[str] = []

    def fake_triage(path: Path, cfg: object) -> TriageResult:
        attempted.append(path.name)
        if path.name == "b.md":
            raise failure
        return TriageResult(decision=completed_decision, confidence=0.9)

    monkeypatch.setattr(pipeline_mod, "triage_file", fake_triage)

    with pytest.raises(InferenceUnavailableError) as raised:
        pipeline_mod.triage_pending(config)

    assert raised.value is failure
    assert attempted == ["a.md", "b.md"]
    assert triage_store.get_decisions() == {"chatgpt/a.md": completed_decision.value}
    assert pipeline_mod.find_untriaged_sources(config, triage_store) == [
        "chatgpt/b.md",
        "chatgpt/c.md",
    ]
    assert pipeline_mod.worthwhile_sources(config, ["chatgpt/b.md", "chatgpt/c.md"]) == []
    assert read_job_progress()["running"] is False
    if completed_decision == TriageDecision.REVIEW:
        assert (config.inbox_dir / "a.md").read_text() == "body " * 100
    elif completed_decision == TriageDecision.SKIP:
        assert (config.raw_dir / ".skipped/chatgpt/a.md").exists()
        assert not (config.raw_dir / "chatgpt/a.md").exists()


def test_worthwhile_sources_holds_undecided_chats(config: PipelineConfig) -> None:
    sources = ["chatgpt/chat.md", "documents/doc.md"]

    assert pipeline_mod.worthwhile_sources(config, sources) == ["documents/doc.md"]

    disabled = config.model_copy(
        update={"triage": config.triage.model_copy(update={"enabled": False})}
    )
    assert pipeline_mod.worthwhile_sources(disabled, sources) == sources
