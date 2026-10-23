"""The agent runs tool conversations and reports their completion, limits, or failures."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from second_brain.compilation.agent import loop
from second_brain.compilation.agent.loop import RunOutcome, RunResult
from second_brain.config import CompilationConfig, Config
from second_brain.llm import resolve_profile
from second_brain.mcp_server.tools import WikiTools
from second_brain.ollama.client import OllamaUnavailableError
from second_brain.wiki.schema import register_domains, write_default_schema
from second_brain.wiki.structure import serialize_page
from tests.test_compilation.fakes import FakeResponse, FakeUsage, install_fake, status_error


class FakeBlock:
    """A tool_use block that triggers a safe read-only glob each turn."""

    type = "tool_use"
    name = "glob_files"
    id = "tool-1"
    input = {"pattern": "*.md"}  # noqa: A003


class SemanticSearchBlock:
    """A tool_use block that asks for a meaning-based search."""

    type = "tool_use"
    name = "semantic_search"
    id = "tool-search"
    input = {"query": "gradient descent"}  # noqa: A003


def _make_config(
    tmp_path: Path,
    *,
    max_iterations: int = 20,
    cost_cap: float = 0.0,
    explore_tools: bool = False,
) -> Config:
    config = Config(
        data_dir=tmp_path / "sb",
        compilation=CompilationConfig(
            max_iterations=max_iterations,
            max_cost_per_build_usd=cost_cap,
            explore_tools=explore_tools,
        ),
    )
    config.ensure_directories()
    return config


def _run(config: Config) -> RunResult:
    return loop.run_agent(
        config,
        ["a.md"],
        started_at="2026-01-01T00:00:00+00:00",
    )


def test_end_turn_completes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path)
    client = install_fake(monkeypatch, FakeResponse("end_turn", FakeUsage(10, 10), []))

    result = _run(config)

    assert client.messages.calls == 1
    assert result.outcome is RunOutcome.COMPLETED


def test_a_rejected_tool_call_is_returned_to_the_model_for_correction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _make_config(tmp_path)
    finished = FakeResponse("end_turn", FakeUsage(10, 10), [])
    client = install_fake(monkeypatch, finished)
    page = config.wiki_dir / "concepts/energy.md"
    arguments = {"type": "concept", "title": "Energy", "body": "Body."}

    def respond(**kwargs: Any) -> FakeResponse:
        client.messages.calls += 1
        if client.messages.calls == 1:
            call = SimpleNamespace(
                type="tool_use", id="invalid", name="write_page", input=arguments
            )
            return FakeResponse("tool_use", FakeUsage(10, 10), [call])

        message = kwargs["messages"][-1]
        assert message["role"] == "user"
        reply = message["content"][0]
        assert reply["type"] == "tool_result"
        if client.messages.calls == 2:
            assert reply["tool_use_id"] == "invalid"
            assert reply["content"].startswith("Error: domains")
            assert not page.exists()
            call = SimpleNamespace(
                type="tool_use",
                id="corrected",
                name="write_page",
                input={**arguments, "domains": ["physics"]},
            )
            return FakeResponse("tool_use", FakeUsage(10, 10), [call])

        assert reply["tool_use_id"] == "corrected"
        assert reply["content"].startswith("Wrote")
        assert page.exists()
        return finished

    monkeypatch.setattr(client.messages, "create", respond)

    result = _run(config)

    assert client.messages.calls == 3
    assert result.outcome is RunOutcome.COMPLETED
    assert result.changes == ({"action": "created", "path": "concepts/energy.md"},)


def test_a_truncated_reply_does_not_complete_the_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _make_config(tmp_path)
    install_fake(monkeypatch, FakeResponse("max_tokens", FakeUsage(10, 10), []))

    result = _run(config)

    assert result.outcome is RunOutcome.EXHAUSTED
    assert result.reason == "The model stopped before finishing this source."


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
    response = FakeResponse("end_turn", FakeUsage(10, 10), [])
    client = install_fake(monkeypatch, response)
    instructions: list[str] = []

    def capture_request(**kwargs: Any) -> FakeResponse:
        instructions.append(kwargs["messages"][0]["content"][0]["text"])
        assert "list_domains" not in [tool["name"] for tool in kwargs["tools"]]
        return response

    monkeypatch.setattr(client.messages, "create", capture_request)

    _run(config)
    (concepts / "cells.md").write_text(
        serialize_page({"title": "Cells", "domains": ["biology"]}, "Body."), encoding="utf-8"
    )
    _run(config)

    assert len(instructions) == 2
    assert "Existing domains:\n- mathematics\n- physics" in instructions[0]
    assert "biology" not in instructions[0]
    assert "Existing domains:\n- biology\n- mathematics\n- physics" in instructions[1]


def test_iteration_cap_reports_runaway_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _make_config(tmp_path, max_iterations=3)
    # Every turn keeps requesting tools and never ends, so the loop bound trips.
    client = install_fake(monkeypatch, FakeResponse("tool_use", FakeUsage(1, 1), [FakeBlock()]))

    result = _run(config)

    assert client.messages.calls == 3
    assert result.outcome is RunOutcome.EXHAUSTED
    assert result.reason == "The model didn't finish within the 3-step limit."


def test_ollama_outage_during_search_fails_the_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _make_config(tmp_path, explore_tools=True)
    usage = FakeUsage(1_000, 100)
    client = install_fake(
        monkeypatch,
        FakeResponse("tool_use", usage, [SemanticSearchBlock()]),
    )

    def _down(*_args: object, **_kwargs: object) -> str:
        raise OllamaUnavailableError("down")

    monkeypatch.setattr(WikiTools, "semantic_search", _down)

    result = _run(config)

    profile = resolve_profile(config.compilation.provider, config.compilation.model)
    assert client.messages.calls == 1
    assert result.outcome is RunOutcome.FAILED
    assert result.reason == ("Ollama stopped responding. Start Ollama and build again.")
    assert result.cost == profile.estimate_cost(usage.input_tokens, usage.output_tokens)


def test_provider_failure_keeps_partial_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A credit failure after a billed turn keeps that turn's cost."""
    config = _make_config(tmp_path)
    client = install_fake(
        monkeypatch,
        FakeResponse("tool_use", FakeUsage(1_000, 100), [FakeBlock()]),
        successful_calls=1,
        error=status_error(402, "credit balance is too low"),
    )

    result = _run(config)

    assert client.messages.calls == 2
    assert result.outcome is RunOutcome.PROVIDER_FAILED
    assert result.reason == "Your API account is out of credits."
    assert result.cost > 0


def test_context_window_stop_is_too_large(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A window-exceeded stop is too large, so the same source is not retried."""
    config = _make_config(tmp_path)
    client = install_fake(
        monkeypatch,
        FakeResponse("model_context_window_exceeded", FakeUsage(10, 0), []),
    )

    result = _run(config)

    assert client.messages.calls == 1
    assert result.outcome is RunOutcome.TOO_LARGE
    assert result.reason.startswith("Too large to build in one run")


def test_cost_cap_stops_midrun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(tmp_path, max_iterations=50, cost_cap=2.0)
    # One turn of a million uncached input tokens (~$3 on the default model)
    # crosses the $2 build cap immediately.
    client = install_fake(
        monkeypatch, FakeResponse("tool_use", FakeUsage(1_000_000, 0), [FakeBlock()])
    )

    result = _run(config)

    assert client.messages.calls == 1
    assert result.outcome is RunOutcome.COST_CAPPED
    assert result.cost > 2.0


def test_iteration_allowance_scales_with_source_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A large inline source raises the turn bound above the configured floor."""
    config = _make_config(tmp_path, max_iterations=2)
    # ~40k chars ≈ 10k tokens → allowance max(2, 10000 // 4000) = 2 is too low;
    # use ~64k chars ≈ 16k tokens → max(2, 4) = 4 turns.
    (config.raw_dir / "big.md").write_text("y" * 64_000, encoding="utf-8")
    client = install_fake(monkeypatch, FakeResponse("tool_use", FakeUsage(1, 1), [FakeBlock()]))

    result = loop.run_agent(
        config,
        ["big.md"],
        started_at="2026-01-01T00:00:00+00:00",
    )

    assert client.messages.calls == 4
    assert result.outcome is RunOutcome.EXHAUSTED
