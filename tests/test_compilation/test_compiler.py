"""The compiler checkpoints completed conversations and handles unfinished builds."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from second_brain.api.build_log import BUILD_LOG_FILENAME
from second_brain.api.run_record import CRASH_REASON, StageOutcome
from second_brain.api.status import STATUS_FILENAME
from second_brain.compilation import compiler
from second_brain.compilation.agent.loop import RunOutcome, RunResult
from second_brain.compilation.store import BuildStore
from second_brain.config import CompilationConfig, Config, TriageConfig
from second_brain.wiki.repo import commit_all
from second_brain.wiki.structure import serialize_page
from tests.test_compilation.fakes import FakeResponse, FakeUsage, install_fake, status_error


def _build_config(tmp_path: Path) -> Config:
    config = Config(
        data_dir=tmp_path / "sb",
        compilation=CompilationConfig(max_cost_per_build_usd=2.0),
        triage=TriageConfig(enabled=False),
    )
    config.ensure_directories()
    return config


def _stage(monkeypatch: pytest.MonkeyPatch, config: Config, raw_paths: list[str]) -> None:
    """Stage these sources for the build, writing a raw file for any the test didn't."""
    for rel in raw_paths:
        raw_file = config.raw_dir / rel
        if not raw_file.exists():
            raw_file.write_text(f"# {rel}\n", encoding="utf-8")
    monkeypatch.setattr(compiler, "find_new_sources", lambda *_: list(raw_paths))


def test_tool_turns_stay_uncommitted_until_the_conversation_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _build_config(tmp_path)
    config = config.model_copy(
        update={"compilation": config.compilation.model_copy(update={"explore_tools": False})}
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _stage(monkeypatch, config, ["a.md", "b.md"])
    build_store = BuildStore(config.manifest_db_path)
    checkpoints: list[set[str]] = []
    rebuilds: list[Path] = []
    monkeypatch.setattr(
        compiler, "_git_commit", lambda _wiki: checkpoints.append(set(build_store.get_compiled()))
    )

    def rebuild(wiki: Path) -> dict:
        rebuilds.append(wiki)
        return {}

    monkeypatch.setattr(compiler, "rebuild_structure", rebuild)
    finished = FakeResponse("end_turn", FakeUsage(10, 10), [])
    client = install_fake(monkeypatch, finished)

    def respond(**_kwargs: Any) -> FakeResponse:
        client.messages.calls += 1
        title = "Alpha" if client.messages.calls <= 2 else "Beta"
        if client.messages.calls in (1, 3):
            write_page = SimpleNamespace(
                type="tool_use",
                id=f"write-{title.lower()}",
                name="write_page",
                input={
                    "type": "concept",
                    "title": title,
                    "domains": ["testing"],
                    "body": f"# {title}\n\nSource content.",
                },
            )
            return FakeResponse("tool_use", FakeUsage(10, 10), [write_page])
        assert (config.wiki_dir / "concepts" / f"{title.lower()}.md").exists()
        assert set(build_store.get_compiled()) == (set() if title == "Alpha" else {"a.md"})
        assert checkpoints == ([] if title == "Alpha" else [{"a.md"}])
        assert rebuilds == []
        return finished

    monkeypatch.setattr(client.messages, "create", respond)

    stats = compiler.run_compilation(config)

    assert client.messages.calls == 4
    assert stats["sources_compiled"] == 2
    assert checkpoints == [{"a.md"}, {"a.md", "b.md"}, {"a.md", "b.md"}]
    assert rebuilds == [config.wiki_dir]


@pytest.mark.parametrize(
    "outcome",
    [
        RunOutcome.EXHAUSTED,
        RunOutcome.COST_CAPPED,
        RunOutcome.TOO_LARGE,
        RunOutcome.FAILED,
        RunOutcome.STOPPED,
        RunOutcome.PROVIDER_FAILED,
    ],
)
def test_an_unfinished_conversation_restores_saved_pages_and_removes_new_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: RunOutcome
) -> None:
    config = _build_config(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _stage(monkeypatch, config, ["a.md"])
    saved_page = config.wiki_dir / "concepts/saved.md"
    saved_page.parent.mkdir(exist_ok=True)
    original = serialize_page({"title": "Saved", "domains": ["testing"]}, "Saved content.")
    saved_page.write_text(original, encoding="utf-8")
    assert commit_all(config.wiki_dir, "test: save the existing wiki")
    new_page = config.wiki_dir / "concepts/unfinished.md"

    def leave_unfinished_edits(_config: Config, _sources: list[str], **_kwargs: Any) -> RunResult:
        saved_page.write_text("Unfinished rewrite.", encoding="utf-8")
        new_page.write_text("Unfinished page.", encoding="utf-8")
        return RunResult(0.1, outcome, "The conversation did not finish.")

    monkeypatch.setattr(compiler, "run_agent", leave_unfinished_edits)

    stats = compiler.run_compilation(config)

    assert stats["sources_compiled"] == 0
    assert BuildStore(config.manifest_db_path).get_compiled() == {}
    assert saved_page.read_text(encoding="utf-8") == original
    assert not new_page.exists()


def test_build_stops_when_cost_cap_reached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_compilation stops before the source that would cross the cost cap."""
    config = _build_config(tmp_path)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _stage(monkeypatch, config, ["a.md", "b.md", "c.md"])
    monkeypatch.setattr(compiler, "rebuild_structure", lambda *_: {})
    monkeypatch.setattr(compiler, "_git_commit", lambda *_: None)

    runs: list[str] = []

    def fake_run_agent(_config, unit, **_kwargs) -> RunResult:
        runs.append(unit[0])
        return RunResult(1.0, RunOutcome.COMPLETED)

    monkeypatch.setattr(compiler, "run_agent", fake_run_agent)

    stats = compiler.run_compilation(config)

    # a -> $1, b -> $2, then cumulative ($2) hits the cap before c.
    assert runs == ["a.md", "b.md"]
    assert stats["sources_compiled"] == 2
    assert stats["outcome"] is StageOutcome.CAPPED
    assert stats["reason"] == "Reached the spend cap per build with 1 source left."
    assert stats["counts"] == {"completed": 2, "deferred": 0, "left": 1}


def test_the_heartbeat_names_the_group_being_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _build_config(tmp_path)
    status_file = config.data_dir / STATUS_FILENAME

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _stage(monkeypatch, config, ["a.md", "b.md"])
    monkeypatch.setattr(compiler, "rebuild_structure", lambda *_: {})
    monkeypatch.setattr(compiler, "_git_commit", lambda *_: None)

    heartbeat_sources: list[list[str]] = []

    def fake_run_agent(_config, unit, **_kwargs) -> RunResult:
        heartbeat = json.loads(status_file.read_text(encoding="utf-8"))
        heartbeat_sources.append(heartbeat["current_sources"])
        return RunResult(0.0, RunOutcome.COMPLETED)

    monkeypatch.setattr(compiler, "run_agent", fake_run_agent)

    compiler.run_compilation(config)

    assert heartbeat_sources == [["a.md"], ["b.md"]]
    assert json.loads(status_file.read_text(encoding="utf-8"))["current_sources"] == []


def test_each_finished_group_is_reported_as_it_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _build_config(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _stage(monkeypatch, config, ["a.md", "b.md"])
    monkeypatch.setattr(compiler, "rebuild_structure", lambda *_: {})
    monkeypatch.setattr(compiler, "_git_commit", lambda *_: None)
    monkeypatch.setattr(compiler, "_git_restore", lambda *_: None)

    outcomes = {"a.md": RunOutcome.COMPLETED, "b.md": RunOutcome.EXHAUSTED}

    def fake_run_agent(_config, unit, **_kwargs) -> RunResult:
        return RunResult(0.0, outcomes[unit[0]], "didn't finish")

    monkeypatch.setattr(compiler, "run_agent", fake_run_agent)
    finished_after: list[set[str]] = []
    build_store = BuildStore(config.manifest_db_path)

    compiler.run_compilation(
        config,
        on_unit_finished=lambda: finished_after.append(
            set(build_store.get_compiled()) | set(build_store.get_deferred())
        ),
    )

    assert finished_after == [{"a.md"}, {"a.md", "b.md"}]


def test_only_committed_groups_reach_the_build_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _build_config(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _stage(monkeypatch, config, ["a.md", "b.md"])
    monkeypatch.setattr(compiler, "rebuild_structure", lambda *_: {})
    monkeypatch.setattr(compiler, "_git_commit", lambda *_: None)
    monkeypatch.setattr(compiler, "_git_restore", lambda *_: None)

    def fake_run_agent(_config, unit, **_kwargs) -> RunResult:
        if unit == ["a.md"]:
            changes = (
                {"action": "created", "path": "concepts/alpha.md"},
                {"action": "updated", "path": "concepts/alpha.md"},
            )
            return RunResult(0.0, RunOutcome.COMPLETED, "", changes)
        changes = ({"action": "created", "path": "concepts/beta.md"},)
        return RunResult(0.0, RunOutcome.FAILED, "Your API account is out of credits.", changes)

    monkeypatch.setattr(compiler, "run_agent", fake_run_agent)

    compiler.run_compilation(config)

    log_lines = (config.data_dir / BUILD_LOG_FILENAME).read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in log_lines]
    assert [(e["action"], e["path"], e["sources"]) for e in entries] == [
        ("created", "concepts/alpha.md", ["a.md"])
    ]


def test_exhausted_unit_is_deferred_not_compiled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that never finishes parks its unit and the build moves on."""
    config = _build_config(tmp_path)
    build_store = BuildStore(config.manifest_db_path)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _stage(monkeypatch, config, ["a.md", "b.md"])
    monkeypatch.setattr(compiler, "rebuild_structure", lambda *_: {})
    monkeypatch.setattr(compiler, "_git_commit", lambda *_: None)

    def fake_run_agent(_config, unit, **_kwargs) -> RunResult:
        if unit == ["a.md"]:
            return RunResult(0.1, RunOutcome.EXHAUSTED, "failed to compile within 20 agent turns")
        return RunResult(0.1, RunOutcome.COMPLETED)

    monkeypatch.setattr(compiler, "run_agent", fake_run_agent)

    stats = compiler.run_compilation(config)

    deferred_sources = build_store.get_deferred()
    assert set(deferred_sources) == {"a.md"}
    assert deferred_sources["a.md"].reason == "failed to compile within 20 agent turns"
    assert deferred_sources["a.md"].retryable
    assert set(build_store.get_compiled()) == {"b.md"}
    assert stats["sources_compiled"] == 1
    assert stats["outcome"] is StageOutcome.PARTIAL
    assert stats["reason"] == "1 source was set aside."
    assert stats["counts"] == {"completed": 1, "deferred": 1, "left": 0}


def test_cost_capped_unit_stays_staged_not_deferred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cap-stopped unit is retried next build (fresh budget), never parked."""
    config = _build_config(tmp_path)
    build_store = BuildStore(config.manifest_db_path)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _stage(monkeypatch, config, ["a.md", "b.md"])
    monkeypatch.setattr(compiler, "rebuild_structure", lambda *_: {})
    monkeypatch.setattr(compiler, "_git_commit", lambda *_: None)

    runs: list[str] = []

    def fake_run_agent(_config, unit, **_kwargs) -> RunResult:
        runs.append(unit[0])
        return RunResult(2.5, RunOutcome.COST_CAPPED, "build cost cap reached")

    monkeypatch.setattr(compiler, "run_agent", fake_run_agent)

    stats = compiler.run_compilation(config)

    # The capped unit ends the build; nothing after it runs.
    assert runs == ["a.md"]
    assert stats["sources_compiled"] == 0
    assert stats["outcome"] is StageOutcome.CAPPED
    assert stats["reason"] == "Reached the spend cap per build with 2 sources left."
    assert stats["counts"] == {"completed": 0, "deferred": 0, "left": 2}
    assert build_store.get_compiled() == {}
    assert build_store.get_deferred() == {}


def test_over_window_unit_defers_preflight_without_api_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source too large for the model's window parks before any spend."""
    config = _build_config(tmp_path)
    build_store = BuildStore(config.manifest_db_path)

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
    _stage(monkeypatch, config, ["huge.md"])

    def fail_run_agent(*_a, **_k):
        raise AssertionError("agent must not run for an over-window unit")

    monkeypatch.setattr(compiler, "run_agent", fail_run_agent)

    stats = compiler.run_compilation(config)

    assert stats["sources_compiled"] == 0
    deferred_sources = build_store.get_deferred()
    assert set(deferred_sources) == {"huge.md"}
    assert "Split it into smaller files" in deferred_sources["huge.md"].reason
    assert not deferred_sources["huge.md"].retryable


def test_unexpected_failure_stops_build_and_keeps_units_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected failure in our own code stops the build and leaves every unit staged."""
    config = _build_config(tmp_path)
    build_store = BuildStore(config.manifest_db_path)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _stage(monkeypatch, config, ["a.md", "b.md"])
    monkeypatch.setattr(compiler, "rebuild_structure", lambda *_: {})
    monkeypatch.setattr(compiler, "_git_commit", lambda *_: None)
    monkeypatch.setattr(compiler, "_git_restore", lambda *_: None)
    (config.raw_dir / "a.md").write_text("body", encoding="utf-8")
    (config.raw_dir / "b.md").write_text("body", encoding="utf-8")
    attempts: list[list[str]] = []

    def raise_unexpected(_config, unit, **_kwargs):
        attempts.append(unit)
        raise ConnectionError("network blip")

    monkeypatch.setattr(compiler, "run_agent", raise_unexpected)

    stats = compiler.run_compilation(config)

    assert len(attempts) == 1
    assert stats["sources_compiled"] == 0
    assert stats["outcome"] is StageOutcome.FAILED
    assert stats["reason"] == CRASH_REASON
    assert stats["counts"] == {"completed": 0, "deferred": 0, "left": 2}
    assert build_store.get_deferred() == {}
    assert build_store.get_compiled() == {}


def test_prompt_too_long_rejection_defers_not_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API's own too-long rejection parks the unit instead of retry-looping."""
    config = _build_config(tmp_path)
    build_store = BuildStore(config.manifest_db_path)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _stage(monkeypatch, config, ["a.md"])
    monkeypatch.setattr(compiler, "rebuild_structure", lambda *_: {})
    monkeypatch.setattr(compiler, "_git_commit", lambda *_: None)
    (config.raw_dir / "a.md").write_text("body", encoding="utf-8")

    error = status_error(400, "prompt is too long: 210000 tokens > 200000 maximum")
    client = install_fake(
        monkeypatch,
        FakeResponse("end_turn", FakeUsage(0, 0), []),
        error=error,
    )

    stats = compiler.run_compilation(config)

    assert client.messages.calls == 1
    assert stats["sources_compiled"] == 0
    assert set(build_store.get_deferred()) == {"a.md"}


def test_provider_failure_stops_build_and_keeps_curation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An account failure stops the build and leaves skipped files and the preview."""
    config = _build_config(tmp_path)
    build_store = BuildStore(config.manifest_db_path)
    skipped = config.raw_dir / ".skipped" / "chatgpt"
    skipped.mkdir(parents=True)
    (skipped / "junk.md").write_text("y", encoding="utf-8")
    (config.raw_dir / "a.md").write_text("alpha", encoding="utf-8")
    (config.raw_dir / "b.md").write_text("beta", encoding="utf-8")
    # Not valid preview JSON, so grouping stays one source per run.
    (config.data_dir / ".clusters.json").write_text("{not json", encoding="utf-8")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _stage(monkeypatch, config, ["a.md", "b.md"])
    monkeypatch.setattr(compiler, "rebuild_structure", lambda *_: {})
    monkeypatch.setattr(compiler, "_git_commit", lambda *_: None)
    monkeypatch.setattr(compiler, "_git_restore", lambda *_: None)

    client = install_fake(
        monkeypatch,
        FakeResponse("end_turn", FakeUsage(0, 0), []),
        error=status_error(402, "credit balance is too low"),
    )

    stats = compiler.run_compilation(config)

    assert client.messages.calls == 1
    assert build_store.get_compiled() == {}
    assert build_store.get_deferred() == {}
    assert (config.raw_dir / ".skipped" / "chatgpt" / "junk.md").is_file()
    assert (config.data_dir / ".clusters.json").is_file()
    assert stats["outcome"] is StageOutcome.FAILED
    assert stats["reason"] == "Your API account is out of credits."
    assert stats["counts"] == {"completed": 0, "deferred": 0, "left": 2}


def test_stop_before_first_unit_is_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stop requested before the first unit ends the build with nothing run."""
    config = _build_config(tmp_path)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _stage(monkeypatch, config, ["a.md", "b.md"])
    monkeypatch.setattr(compiler, "rebuild_structure", lambda *_: {})
    monkeypatch.setattr(compiler, "_git_commit", lambda *_: None)
    monkeypatch.setattr("second_brain.api.status.stop_requested", lambda _data_dir: True)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("agent must not run after a stop")

    monkeypatch.setattr(compiler, "run_agent", fail_if_called)

    stats = compiler.run_compilation(config)

    assert stats["outcome"] is StageOutcome.STOPPED
    assert stats["reason"] == ""
    assert stats["counts"] == {"completed": 0, "deferred": 0, "left": 2}
    assert stats["sources_compiled"] == 0


def test_full_build_purges_skipped_holding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A build that compiles every staged source clears the holding folder."""
    config = _build_config(tmp_path)
    skipped = config.raw_dir / ".skipped" / "chatgpt"
    skipped.mkdir(parents=True, exist_ok=True)
    (skipped / "junk.md").write_text("y", encoding="utf-8")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _stage(monkeypatch, config, ["a.md"])
    monkeypatch.setattr(compiler, "rebuild_structure", lambda *_: {})
    monkeypatch.setattr(compiler, "_git_commit", lambda *_: None)
    monkeypatch.setattr(
        compiler, "run_agent", lambda *_a, **_k: RunResult(0.1, RunOutcome.COMPLETED)
    )

    stats = compiler.run_compilation(config)

    assert stats["outcome"] is StageOutcome.OK
    assert stats["reason"] == ""
    assert stats["counts"] == {"completed": 1, "deferred": 0, "left": 0}
    assert not (config.raw_dir / ".skipped").exists()


def test_incomplete_build_keeps_skipped_holding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed unit leaves the holding folder for the next attempt."""
    config = _build_config(tmp_path)
    skipped = config.raw_dir / ".skipped" / "chatgpt"
    skipped.mkdir(parents=True, exist_ok=True)
    (skipped / "junk.md").write_text("y", encoding="utf-8")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _stage(monkeypatch, config, ["a.md"])
    monkeypatch.setattr(compiler, "rebuild_structure", lambda *_: {})
    monkeypatch.setattr(compiler, "_git_commit", lambda *_: None)
    monkeypatch.setattr(compiler, "_git_restore", lambda *_: None)
    monkeypatch.setattr(compiler, "run_agent", lambda *_a, **_k: RunResult(0.0, RunOutcome.FAILED))

    compiler.run_compilation(config)

    assert (config.raw_dir / ".skipped" / "chatgpt" / "junk.md").is_file()
