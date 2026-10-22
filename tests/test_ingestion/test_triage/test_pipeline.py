"""Tests for triage source-lane scoping in the pipeline."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from second_brain.api.status import STATUS_FILENAME
from second_brain.config import Config
from second_brain.ingestion.triage import pipeline as pipeline_mod
from second_brain.ingestion.triage.gemma import TriageDecision, TriageResult
from second_brain.ingestion.triage.store import TriageStore
from second_brain.ollama.client import OllamaUnavailableError


def _write(raw_dir: Path, rel: str, text: str) -> None:
    path = raw_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_triage_only_touches_scoped_lanes(
    config: Config, triage_store: TriageStore, monkeypatch: pytest.MonkeyPatch
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


def test_the_heartbeat_names_the_chat_being_triaged_and_those_waiting(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(config.raw_dir, "chatgpt/a.md", "chat body " * 100)
    _write(config.raw_dir, "chatgpt/b.md", "chat body " * 100)
    status_file = config.data_dir / STATUS_FILENAME
    heartbeats: list[tuple[list[str], list[str]]] = []

    def fake_triage(path: Path, _config: object) -> TriageResult:
        heartbeat = json.loads(status_file.read_text())
        heartbeats.append((heartbeat["current_sources"], heartbeat["queued_sources"]))
        return TriageResult(decision=TriageDecision.WORTHWHILE, confidence=0.9)

    monkeypatch.setattr(pipeline_mod, "triage_file", fake_triage)

    pipeline_mod.triage_pending(config)

    assert heartbeats == [(["chatgpt/a.md"], ["chatgpt/b.md"]), (["chatgpt/b.md"], [])]


def test_a_blocking_triage_keeps_its_heartbeat_fresh(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(config.raw_dir, "chatgpt/slow.md", "chat body " * 100)
    status_file = config.data_dir / STATUS_FILENAME
    started = threading.Event()
    refreshed = threading.Event()
    touch_status = pipeline_mod.touch_status

    def record_refresh(data_dir: Path) -> None:
        touch_status(data_dir)
        if started.is_set():
            refreshed.set()

    def blocking_triage(_path: Path, _config: object) -> TriageResult:
        before = json.loads(status_file.read_text())
        started.set()
        assert refreshed.wait(timeout=1)
        after = json.loads(status_file.read_text())
        assert after["updated_at"] > before["updated_at"]
        assert after["current_started_at"] == before["current_started_at"]
        assert after["current_sources"] == ["chatgpt/slow.md"]
        return TriageResult(decision=TriageDecision.WORTHWHILE, confidence=0.9)

    monkeypatch.setattr(pipeline_mod, "_HEARTBEAT_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(pipeline_mod, "touch_status", record_refresh)
    monkeypatch.setattr(pipeline_mod, "triage_file", blocking_triage)

    pipeline_mod.triage_pending(config)

    assert json.loads(status_file.read_text())["running"] is False


def test_each_decision_is_reported_and_a_skip_is_held_at_once(
    config: Config, monkeypatch: pytest.MonkeyPatch
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
    config: Config, triage_store: TriageStore, monkeypatch: pytest.MonkeyPatch
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


def test_triage_stops_when_ollama_stops(
    config: Config, triage_store: TriageStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("a.md", "b.md", "c.md"):
        _write(config.raw_dir, f"chatgpt/{name}", "body " * 100)

    def fake_triage(path: Path, cfg: object) -> TriageResult:
        if path.name == "b.md":
            raise OllamaUnavailableError("down")
        return TriageResult(decision=TriageDecision.WORTHWHILE, confidence=0.9)

    monkeypatch.setattr(pipeline_mod, "triage_file", fake_triage)

    with pytest.raises(pipeline_mod.TriageInterruptedError) as raised:
        pipeline_mod.triage_pending(config)

    assert raised.value.untriaged_count == 2
    assert isinstance(raised.value.__cause__, OllamaUnavailableError)

    decisions = triage_store.get_decisions()
    assert "chatgpt/a.md" in decisions
    assert "chatgpt/b.md" not in decisions
    assert "chatgpt/c.md" not in decisions
    status = json.loads((config.data_dir / STATUS_FILENAME).read_text(encoding="utf-8"))
    assert status["running"] is False


def test_worthwhile_sources_holds_undecided_chats(config: Config) -> None:
    sources = ["chatgpt/chat.md", "documents/doc.md"]

    assert pipeline_mod.worthwhile_sources(config, sources) == ["documents/doc.md"]

    disabled = config.model_copy(
        update={"triage": config.triage.model_copy(update={"enabled": False})}
    )
    assert pipeline_mod.worthwhile_sources(disabled, sources) == sources
