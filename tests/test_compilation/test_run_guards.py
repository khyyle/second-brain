"""Tests for compile-run outcomes: completion, loop bound, cost cap, deferral."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from second_brain.compilation import compiler
from second_brain.compilation.compiler import RunOutcome, RunResult
from second_brain.config import CompilationConfig, Config, TriageConfig
from second_brain.ingestion.manifest import Manifest
from second_brain.llm import ModelError, ModelErrorKind


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


def _make_config(tmp_path: Path, *, max_iter: int = 20, cost_cap: float = 0.0) -> Config:
    cfg = Config(
        data_dir=tmp_path / "sb",
        compilation=CompilationConfig(
            max_iterations=max_iter,
            max_cost_per_build_usd=cost_cap,
            explore_tools=False,
        ),
    )
    cfg.ensure_directories()
    return cfg


def _install_fake(monkeypatch: pytest.MonkeyPatch, response: FakeResponse) -> FakeClient:
    client = FakeClient(response)
    monkeypatch.setattr(compiler, "create_client", lambda *_args, **_kwargs: client)
    return client


def _run(config: Config) -> RunResult:
    return compiler._run_agent(
        config,
        config.wiki_dir,
        config.raw_dir,
        ["a.md"],
        started_at="2026-01-01T00:00:00+00:00",
    )


def test_end_turn_completes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path)
    client = _install_fake(monkeypatch, FakeResponse("end_turn", FakeUsage(10, 10), []))

    result = _run(config)

    assert client.messages.calls == 1
    assert result.outcome is RunOutcome.COMPLETED


def test_iteration_cap_reports_runaway_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _make_config(tmp_path, max_iter=3)
    # Every turn keeps requesting tools and never ends, so the loop bound trips.
    client = _install_fake(monkeypatch, FakeResponse("tool_use", FakeUsage(1, 1), [FakeBlock()]))

    result = _run(config)

    assert client.messages.calls == 3
    assert result.outcome is RunOutcome.EXHAUSTED
    assert "3 agent turns" in result.reason


def test_cost_cap_stops_midrun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path, max_iter=50, cost_cap=2.0)
    # One turn of a million uncached input tokens (~$3 on the default model)
    # crosses the $2 build cap immediately.
    client = _install_fake(
        monkeypatch, FakeResponse("tool_use", FakeUsage(1_000_000, 0), [FakeBlock()])
    )

    result = _run(config)

    assert client.messages.calls == 1
    assert result.outcome is RunOutcome.COST_CAPPED
    assert result.cost > 2.0


def _build_config(tmp_path: Path) -> Config:
    cfg = Config(
        data_dir=tmp_path / "sb",
        compilation=CompilationConfig(max_cost_per_build_usd=2.0),
        triage=TriageConfig(enabled=False),
    )
    cfg.ensure_directories()
    return cfg


def test_build_stops_when_cost_cap_reached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_compilation stops before the source that would cross the cost cap."""
    config = _build_config(tmp_path)
    manifest = Manifest(config.manifest_db_path)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(compiler, "find_new_sources", lambda *_: ["a.md", "b.md", "c.md"])
    monkeypatch.setattr(compiler, "rebuild_structure", lambda *_: {})
    monkeypatch.setattr(compiler, "_git_commit", lambda *_: None)

    runs: list[str] = []

    def fake_run_agent(_config, _wiki, _raw, unit, **_kwargs) -> RunResult:
        runs.append(unit[0])
        return RunResult(1.0, RunOutcome.COMPLETED)

    monkeypatch.setattr(compiler, "_run_agent", fake_run_agent)

    stats = compiler.run_compilation(config, manifest)

    # a -> $1, b -> $2, then cumulative ($2) hits the cap before c.
    assert runs == ["a.md", "b.md"]
    assert stats["sources_compiled"] == 2


def test_exhausted_unit_is_deferred_not_compiled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that never finishes parks its unit and the build moves on."""
    config = _build_config(tmp_path)
    manifest = Manifest(config.manifest_db_path)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(compiler, "find_new_sources", lambda *_: ["a.md", "b.md"])
    monkeypatch.setattr(compiler, "rebuild_structure", lambda *_: {})
    monkeypatch.setattr(compiler, "_git_commit", lambda *_: None)

    def fake_run_agent(_config, _wiki, _raw, unit, **_kwargs) -> RunResult:
        if unit == ["a.md"]:
            return RunResult(0.1, RunOutcome.EXHAUSTED, "failed to compile within 20 agent turns")
        return RunResult(0.1, RunOutcome.COMPLETED)

    monkeypatch.setattr(compiler, "_run_agent", fake_run_agent)

    stats = compiler.run_compilation(config, manifest)

    assert manifest.get_deferred_sources() == {"a.md": "failed to compile within 20 agent turns"}
    assert manifest.get_compiled_raw_paths() == {"b.md"}
    assert stats["sources_compiled"] == 1


def test_cost_capped_unit_stays_staged_not_deferred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cap-stopped unit is retried next build (fresh budget), never parked."""
    config = _build_config(tmp_path)
    manifest = Manifest(config.manifest_db_path)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(compiler, "find_new_sources", lambda *_: ["a.md", "b.md"])
    monkeypatch.setattr(compiler, "rebuild_structure", lambda *_: {})
    monkeypatch.setattr(compiler, "_git_commit", lambda *_: None)

    runs: list[str] = []

    def fake_run_agent(_config, _wiki, _raw, unit, **_kwargs) -> RunResult:
        runs.append(unit[0])
        return RunResult(2.5, RunOutcome.COST_CAPPED, "build cost cap reached")

    monkeypatch.setattr(compiler, "_run_agent", fake_run_agent)

    stats = compiler.run_compilation(config, manifest)

    # The capped unit ends the build; nothing after it runs.
    assert runs == ["a.md"]
    assert stats["sources_compiled"] == 0
    assert manifest.get_compiled_raw_paths() == set()
    assert manifest.get_deferred_sources() == {}


def test_find_new_sources_excludes_deferred(tmp_path: Path) -> None:
    config = _build_config(tmp_path)
    manifest = Manifest(config.manifest_db_path)
    (config.raw_dir / "a.md").write_text("alpha", encoding="utf-8")
    (config.raw_dir / "b.md").write_text("beta", encoding="utf-8")

    manifest.defer_sources(["b.md"], "failed to compile within 20 agent turns")

    assert compiler.find_new_sources(config, manifest) == ["a.md"]


def test_over_window_unit_defers_preflight_without_api_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source too large for the model's window parks before any spend."""
    config = _build_config(tmp_path)
    manifest = Manifest(config.manifest_db_path)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(compiler, "rebuild_structure", lambda *_: {})
    monkeypatch.setattr(compiler, "_git_commit", lambda *_: None)
    # Shrink the window so an ordinary temp file crosses the 60% line.
    profile = compiler.resolve_profile(config.compilation.provider, config.compilation.model)
    monkeypatch.setattr(
        compiler,
        "resolve_profile",
        lambda *a, **k: dataclasses.replace(profile, context_window_tokens=1000),
    )
    (config.raw_dir / "huge.md").write_text("x" * 10_000, encoding="utf-8")
    monkeypatch.setattr(compiler, "find_new_sources", lambda *_: ["huge.md"])

    def fail_run_agent(*_a, **_k):
        raise AssertionError("agent must not run for an over-window unit")

    monkeypatch.setattr(compiler, "_run_agent", fail_run_agent)

    stats = compiler.run_compilation(config, manifest)

    assert stats["sources_compiled"] == 0
    deferred = manifest.get_deferred_sources()
    assert set(deferred) == {"huge.md"}
    assert "split it into parts" in deferred["huge.md"]


def test_transient_failure_stays_staged_not_deferred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A network-style failure leaves the unit staged for the next build."""
    config = _build_config(tmp_path)
    manifest = Manifest(config.manifest_db_path)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(compiler, "find_new_sources", lambda *_: ["a.md"])
    monkeypatch.setattr(compiler, "rebuild_structure", lambda *_: {})
    monkeypatch.setattr(compiler, "_git_commit", lambda *_: None)
    (config.raw_dir / "a.md").write_text("body", encoding="utf-8")

    def raise_transient(*_a, **_k):
        raise ConnectionError("network blip")

    monkeypatch.setattr(compiler, "_run_agent", raise_transient)

    stats = compiler.run_compilation(config, manifest)

    assert stats["sources_compiled"] == 0
    assert manifest.get_deferred_sources() == {}
    assert manifest.get_compiled_raw_paths() == set()


def test_prompt_too_long_rejection_defers_not_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API's own too-long rejection parks the unit instead of retry-looping."""
    config = _build_config(tmp_path)
    manifest = Manifest(config.manifest_db_path)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(compiler, "find_new_sources", lambda *_: ["a.md"])
    monkeypatch.setattr(compiler, "rebuild_structure", lambda *_: {})
    monkeypatch.setattr(compiler, "_git_commit", lambda *_: None)
    (config.raw_dir / "a.md").write_text("body", encoding="utf-8")

    def raise_too_long(*_a, **_k):
        raise ModelError(ModelErrorKind.TOO_LARGE, "The source is too large for the model")

    monkeypatch.setattr(compiler, "_run_agent", raise_too_long)

    stats = compiler.run_compilation(config, manifest)

    assert stats["sources_compiled"] == 0
    assert set(manifest.get_deferred_sources()) == {"a.md"}


def test_full_build_purges_skipped_holding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A build that compiles every staged source clears the holding folder."""
    config = _build_config(tmp_path)
    manifest = Manifest(config.manifest_db_path)
    skipped = config.raw_dir / ".skipped" / "chatgpt"
    skipped.mkdir(parents=True, exist_ok=True)
    (skipped / "junk.md").write_text("y", encoding="utf-8")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(compiler, "find_new_sources", lambda *_: ["a.md"])
    monkeypatch.setattr(compiler, "rebuild_structure", lambda *_: {})
    monkeypatch.setattr(compiler, "_git_commit", lambda *_: None)
    monkeypatch.setattr(
        compiler, "_run_agent", lambda *_a, **_k: RunResult(0.1, RunOutcome.COMPLETED)
    )

    compiler.run_compilation(config, manifest)

    assert not (config.raw_dir / ".skipped").exists()


def test_incomplete_build_keeps_skipped_holding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed unit leaves the holding folder for the next attempt."""
    config = _build_config(tmp_path)
    manifest = Manifest(config.manifest_db_path)
    skipped = config.raw_dir / ".skipped" / "chatgpt"
    skipped.mkdir(parents=True, exist_ok=True)
    (skipped / "junk.md").write_text("y", encoding="utf-8")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(compiler, "find_new_sources", lambda *_: ["a.md"])
    monkeypatch.setattr(compiler, "rebuild_structure", lambda *_: {})
    monkeypatch.setattr(compiler, "_git_commit", lambda *_: None)
    monkeypatch.setattr(compiler, "_git_restore", lambda *_: None)
    monkeypatch.setattr(
        compiler, "_run_agent", lambda *_a, **_k: RunResult(0.0, RunOutcome.FAILED)
    )

    compiler.run_compilation(config, manifest)

    assert (config.raw_dir / ".skipped" / "chatgpt" / "junk.md").is_file()


def test_iteration_allowance_scales_with_source_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A large inline source raises the turn bound above the configured floor."""
    config = _make_config(tmp_path, max_iter=2)
    # ~40k chars ≈ 10k tokens → allowance max(2, 10000 // 4000) = 2 is too low;
    # use ~64k chars ≈ 16k tokens → max(2, 4) = 4 turns.
    (config.raw_dir / "big.md").write_text("y" * 64_000, encoding="utf-8")
    client = _install_fake(monkeypatch, FakeResponse("tool_use", FakeUsage(1, 1), [FakeBlock()]))

    result = compiler._run_agent(
        config,
        config.wiki_dir,
        config.raw_dir,
        ["big.md"],
        started_at="2026-01-01T00:00:00+00:00",
    )

    assert client.messages.calls == 4
    assert result.outcome is RunOutcome.EXHAUSTED
