"""Tests for the compilation agent's token-budget guard and iteration cap."""

from __future__ import annotations

from pathlib import Path

import pytest

from second_brain.compilation import compiler
from second_brain.config import CompilationConfig, Config, TriageConfig
from second_brain.ingestion.manifest import Manifest


class FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeBlock:
    """A tool_use block that triggers a safe read-only glob each turn."""

    type = "tool_use"
    name = "glob_files"
    id = "tool-1"
    input = {"pattern": "*.md"}  # noqa: A003


class FakeResponse:
    def __init__(self, stop_reason: str, usage: FakeUsage, content: list) -> None:
        self.stop_reason = stop_reason
        self.usage = usage
        self.content = content


class FakeMessages:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response
        self.calls = 0

    def create(self, **_kwargs) -> FakeResponse:
        self.calls += 1
        return self._response


class FakeClient:
    def __init__(self, response: FakeResponse) -> None:
        self.messages = FakeMessages(response)


def _make_config(tmp_path: Path, *, budget: int, max_iter: int) -> Config:
    cfg = Config(
        data_dir=tmp_path / "sb",
        compilation=CompilationConfig(
            token_budget_per_run=budget, max_iterations=max_iter, explore_tools=False
        ),
    )
    cfg.ensure_directories()
    return cfg


def _install_fake(monkeypatch: pytest.MonkeyPatch, response: FakeResponse) -> FakeClient:
    client = FakeClient(response)
    monkeypatch.setattr(compiler.anthropic, "Anthropic", lambda *a, **k: client)
    return client


def test_stops_when_token_budget_exceeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path, budget=100, max_iter=50)
    # Each turn keeps requesting tools (never end_turn) and burns 120 tokens,
    # so the budget (100) is blown after the first turn.
    response = FakeResponse("tool_use", FakeUsage(60, 60), [FakeBlock()])
    client = _install_fake(monkeypatch, response)

    compiler._run_agent(
        config,
        config.wiki_dir,
        config.raw_dir,
        ["a.md"],
        started_at="2026-01-01T00:00:00+00:00",
    )

    assert client.messages.calls == 1


def test_stops_on_end_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path, budget=1_000_000, max_iter=50)
    response = FakeResponse("end_turn", FakeUsage(10, 10), [])
    client = _install_fake(monkeypatch, response)

    compiler._run_agent(
        config,
        config.wiki_dir,
        config.raw_dir,
        ["a.md"],
        started_at="2026-01-01T00:00:00+00:00",
    )

    assert client.messages.calls == 1


def test_respects_max_iterations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path, budget=10_000_000, max_iter=3)
    # Tiny per-turn usage so the budget never trips; the iteration cap does.
    response = FakeResponse("tool_use", FakeUsage(1, 1), [FakeBlock()])
    client = _install_fake(monkeypatch, response)

    compiler._run_agent(
        config,
        config.wiki_dir,
        config.raw_dir,
        ["a.md"],
        started_at="2026-01-01T00:00:00+00:00",
    )

    assert client.messages.calls == 3


def test_build_stops_when_cost_cap_reached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_compilation stops before the source that would cross the cost cap."""
    config = Config(
        data_dir=tmp_path / "sb",
        compilation=CompilationConfig(max_cost_per_build_usd=2.0),
        triage=TriageConfig(enabled=False),
    )
    config.ensure_directories()
    manifest = Manifest(config.manifest_db_path)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    sources = ["a.md", "b.md", "c.md"]
    monkeypatch.setattr(compiler, "_find_new_sources", lambda *_: list(sources))
    monkeypatch.setattr(compiler, "rebuild_structure", lambda *_: {})
    monkeypatch.setattr(compiler, "_git_commit", lambda *_: None)

    runs: list[str] = []

    def fake_run_agent(_config, _wiki, _raw, batch, **_kwargs) -> float:
        runs.append(batch[0])
        return 1.0  # each source costs ~$1

    monkeypatch.setattr(compiler, "_run_agent", fake_run_agent)

    stats = compiler.run_compilation(config, manifest)

    # a -> $1, b -> $2, then cumulative ($2) hits the cap before c.
    assert runs == ["a.md", "b.md"]
    assert stats["sources_compiled"] == 2
