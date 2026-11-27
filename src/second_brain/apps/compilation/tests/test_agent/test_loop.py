"""The agent runs tool conversations and reports their completion, limits, or failures."""

from __future__ import annotations

from pathlib import Path

import pytest

from second_brain.apps.compilation.services.agent import loop
from second_brain.apps.compilation.services.agent.loop import RunOutcome, RunResult
from second_brain.apps.compilation.tests.fakes import (
    Turn,
    finished,
    install_fake,
    no_reply,
    tool_call,
    tool_use,
)
from second_brain.apps.inference.errors import InferenceUnavailableError
from second_brain.apps.jobs.services import job_progress
from second_brain.apps.wiki.services.assistant_tools import WikiTools
from second_brain.apps.wiki.services.topic_schema import register_domains, write_default_schema
from second_brain.apps.wiki.services.wiki_structure import serialize_page
from second_brain.cloud_llm.pi import pi_helper
from second_brain.pipeline_config import CompilationConfig, PipelineConfig

# A safe read-only tool call, for turns that only need to keep the conversation going.
GLOB = tool_call("glob_files", {"pattern": "*.md"})
PAGE = {"type": "concept", "title": "Energy", "body": "Body."}


def _make_config(
    tmp_path: Path,
    *,
    max_iterations: int = 20,
    cost_cap: float = 0.0,
    explore_tools: bool = False,
) -> PipelineConfig:
    config = PipelineConfig(
        data_dir=tmp_path / "sb",
        compilation=CompilationConfig(
            max_iterations=max_iterations,
            max_cost_per_build_usd=cost_cap,
            explore_tools=explore_tools,
        ),
    )
    config.ensure_directories()
    return config


def _run(config: PipelineConfig) -> RunResult:
    return loop.run_agent(
        config,
        ["a.md"],
        started_at="2026-01-01T00:00:00+00:00",
    )


def test_a_finished_reply_completes_the_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _make_config(tmp_path, explore_tools=False)
    turns = install_fake(monkeypatch, finished())

    result = _run(config)

    assert result.outcome is RunOutcome.COMPLETED
    [context] = turns.contexts
    assert context["systemPrompt"]
    assert {"name", "description", "parameters"} == set(context["tools"][0])
    assert [message["role"] for message in context["messages"]] == ["user"]


def test_a_rejected_tool_call_is_returned_to_the_model_for_correction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _make_config(tmp_path)
    page = config.wiki_dir / "concepts/energy.md"
    # Pi's reply carries fields only the provider understands. They must come back as sent.
    thinking = {"type": "thinking", "thinking": "Plan.", "thinkingSignature": "opaque"}
    invalid = tool_use(thinking, tool_call("write_page", PAGE, "invalid"))

    def corrected(context: dict) -> Turn:
        *_, asked, result = context["messages"]
        assert asked == invalid["message"]
        assert (result["role"], result["toolCallId"], result["toolName"]) == (
            "toolResult",
            "invalid",
            "write_page",
        )
        assert result["content"][0]["text"].startswith("Error: domains")
        assert not page.exists()
        return tool_use(tool_call("write_page", {**PAGE, "domains": ["physics"]}, "corrected"))

    def done(context: dict) -> Turn:
        result = context["messages"][-1]
        assert result["toolCallId"] == "corrected"
        assert result["content"][0]["text"].startswith("Wrote")
        assert page.exists()
        return finished()

    turns = install_fake(monkeypatch, invalid, corrected, done)

    result = _run(config)

    assert len(turns.contexts) == 3
    assert result.outcome is RunOutcome.COMPLETED
    assert result.changes == ({"action": "created", "path": "concepts/energy.md"},)


def test_arguments_pi_could_not_read_go_back_to_the_model_without_running_the_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _make_config(tmp_path)
    garbled = tool_call("write_page", {**PAGE, "domains": ["physics"]})
    garbled["argumentsError"] = "Unterminated string in JSON"
    turns = install_fake(monkeypatch, tool_use(garbled), finished())

    result = _run(config)

    reply = turns.contexts[1]["messages"][-1]
    assert (reply["isError"], reply["content"][0]["text"]) == (True, "Unterminated string in JSON")
    assert not (config.wiki_dir / "concepts/energy.md").exists()
    assert result.outcome is RunOutcome.COMPLETED


@pytest.mark.parametrize(
    "turn_outcome,outcome,reason",
    [
        ("incomplete", RunOutcome.EXHAUSTED, "The model stopped before finishing this source."),
        ("error", RunOutcome.PROVIDER_FAILED, "The model provider did not answer. Build again."),
        ("not_connected", RunOutcome.FAILED, "Connect a provider in Settings."),
        ("model_unavailable", RunOutcome.FAILED, "Choose a model on the Build tab."),
    ],
)
def test_a_turn_without_a_usable_reply_ends_the_conversation_with_what_to_do(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    turn_outcome: str,
    outcome: RunOutcome,
    reason: str,
) -> None:
    config = _make_config(tmp_path)
    turns = install_fake(monkeypatch, {"outcome": turn_outcome})

    result = _run(config)

    assert len(turns.contexts) == 1
    assert (result.outcome, result.reason, result.cost) == (outcome, reason, 0.0)


def test_existing_domains_are_supplied_before_any_tools_and_refreshed_between_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _make_config(tmp_path, explore_tools=False)
    write_default_schema(config.wiki_dir)
    register_domains(config.wiki_dir, {"mathematics"})
    concepts = config.wiki_dir / "concepts"
    concepts.mkdir(exist_ok=True)
    (concepts / "energy.md").write_text(
        serialize_page({"title": "Energy", "domains": ["physics"]}, "Body."), encoding="utf-8"
    )
    turns = install_fake(monkeypatch, finished())

    _run(config)
    (concepts / "cells.md").write_text(
        serialize_page({"title": "Cells", "domains": ["biology"]}, "Body."), encoding="utf-8"
    )
    _run(config)

    first, second = (context["messages"][0]["content"][0]["text"] for context in turns.contexts)
    assert "list_domains" not in [tool["name"] for tool in turns.contexts[0]["tools"]]
    assert "Existing domains:\n- mathematics\n- physics" in first
    assert "biology" not in first
    assert "Existing domains:\n- biology\n- mathematics\n- physics" in second


def test_iteration_cap_reports_runaway_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _make_config(tmp_path, max_iterations=3)
    # Every turn keeps requesting tools and never ends, so the loop bound trips.
    turns = install_fake(monkeypatch, tool_use(GLOB))

    result = _run(config)

    assert len(turns.contexts) == 3
    assert result.outcome is RunOutcome.EXHAUSTED
    assert result.reason == "The model didn't finish within the 3-step limit."


def test_inference_outage_during_search_fails_the_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _make_config(tmp_path, explore_tools=True)
    search = tool_call("semantic_search", {"query": "gradient descent"})
    turns = install_fake(monkeypatch, tool_use(search, cost=0.25))

    def _down(*_args: object, **_kwargs: object) -> str:
        raise InferenceUnavailableError("Local inference timed out. Try again.")

    monkeypatch.setattr(WikiTools, "semantic_search", _down)

    result = _run(config)

    assert len(turns.contexts) == 1
    assert result.outcome is RunOutcome.FAILED
    assert result.reason == "Local inference timed out. Try again."
    assert result.cost == 0.25


def test_provider_failure_keeps_partial_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure after a billed turn keeps that turn's cost."""
    config = _make_config(tmp_path)
    turns = install_fake(monkeypatch, tool_use(GLOB, cost=0.25), no_reply("error"))

    result = _run(config)

    assert len(turns.contexts) == 2
    assert result.outcome is RunOutcome.PROVIDER_FAILED
    assert result.cost == 0.25


def test_a_helper_failure_stops_the_conversation_with_its_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _make_config(tmp_path)
    install_fake(monkeypatch, pi_helper.PiHelperError("Could not start the provider helper."))

    result = _run(config)

    assert result.outcome is RunOutcome.PROVIDER_FAILED
    assert result.reason == "Could not start the provider helper."


def test_a_request_too_large_for_the_model_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _make_config(tmp_path)
    turns = install_fake(monkeypatch, no_reply("too_large"))

    result = _run(config)

    assert len(turns.contexts) == 1
    assert result.outcome is RunOutcome.TOO_LARGE
    assert result.reason.startswith("Too large to build in one run")


@pytest.mark.parametrize("turn", [tool_use(GLOB, cost=3.0), no_reply("incomplete", cost=3.0)])
def test_cost_cap_stops_midrun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, turn: Turn
) -> None:
    """Crossing the cap leaves the source staged, even if that turn was also cut off."""
    config = _make_config(tmp_path, max_iterations=50, cost_cap=2.0)
    turns = install_fake(monkeypatch, turn)

    result = _run(config)

    assert len(turns.contexts) == 1
    assert result.outcome is RunOutcome.COST_CAPPED
    assert result.cost == 3.0


def test_a_reply_the_loop_does_not_recognize_runs_no_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _make_config(tmp_path)
    write = tool_use(tool_call("write_page", {**PAGE, "domains": ["physics"]}))
    install_fake(monkeypatch, {**write, "outcome": "deferred"})

    result = _run(config)

    assert result.outcome is RunOutcome.PROVIDER_FAILED
    assert not (config.wiki_dir / "concepts/energy.md").exists()


def test_an_unknown_cost_stays_unknown_and_is_not_held_to_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read_job_progress
) -> None:
    config = _make_config(tmp_path, cost_cap=2.0)
    # Counting the unknown turn as free would cap the build at the second turn.
    install_fake(monkeypatch, tool_use(GLOB, cost=None), tool_use(GLOB, cost=5.0), finished())

    result = _run(config)

    assert result.outcome is RunOutcome.COMPLETED
    assert result.cost is None
    progress = read_job_progress()
    assert (progress["cost_usd"], progress["current_cost_usd"]) == (None, None)


def test_stop_ends_a_turn_in_flight_and_skips_the_tools_that_have_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _make_config(tmp_path)
    stopped = False
    monkeypatch.setattr(job_progress, "stop_requested", lambda: stopped)
    first = tool_call("write_page", {**PAGE, "domains": ["physics"]}, "first")
    second = tool_call("write_page", {**PAGE, "title": "Heat", "domains": ["physics"]}, "second")

    def stop_after_first_tool(*_args: object) -> str:
        nonlocal stopped
        stopped = True
        return "Reading"

    # The readout is published before each tool, so the first call flips Stop.
    monkeypatch.setattr(loop, "describe_tool_call", stop_after_first_tool)
    turns = install_fake(monkeypatch, tool_use(first, second))

    _run(config)

    assert len(turns.contexts) == 1
    assert (config.wiki_dir / "concepts/energy.md").exists()
    assert not (config.wiki_dir / "concepts/heat.md").exists()

    # A turn the helper abandoned on Stop returns nothing, and nothing more is asked.
    stopped = False
    abandoned = install_fake(monkeypatch, lambda _context: None)
    _run(config)
    assert len(abandoned.contexts) == 1


def test_iteration_allowance_scales_with_source_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A large inline source raises the turn bound above the configured floor."""
    config = _make_config(tmp_path, max_iterations=2)
    # ~40k chars ≈ 10k tokens → allowance max(2, 10000 // 4000) = 2 is too low;
    # use ~64k chars ≈ 16k tokens → max(2, 4) = 4 turns.
    (config.raw_dir / "big.md").write_text("y" * 64_000, encoding="utf-8")
    turns = install_fake(monkeypatch, tool_use(GLOB))

    result = loop.run_agent(
        config,
        ["big.md"],
        started_at="2026-01-01T00:00:00+00:00",
    )

    assert len(turns.contexts) == 4
    assert result.outcome is RunOutcome.EXHAUSTED
