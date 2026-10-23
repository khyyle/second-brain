"""Run one model-and-tool conversation for a source or chat group."""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from anthropic.types import ContentBlock

from second_brain.compilation.agent.prompt import (
    COMPILATION_SYSTEM_PROMPT,
    EXPLORE_TOOLS_GUIDANCE,
    WIKI_TOOLS,
)
from second_brain.compilation.agent.tools import (
    WikiToolExecutor,
    build_compilation_prompt,
    build_source_block,
    compact_history,
    describe_tool_call,
    explore_tool_schemas,
)
from second_brain.config import Config
from second_brain.llm import (
    ModelError,
    ModelErrorKind,
    ProviderProfile,
    create_client,
    request_turn,
    resolve_profile,
)
from second_brain.ollama.client import OllamaUnavailableError
from second_brain.wiki.domain_ops import list_domains
from second_brain.wiki.structure import build_link_graph, discover_all_pages, rank_gaps

logger = logging.getLogger(__name__)

_MAX_OUTPUT_TOKENS_PER_TURN = 16384
_SOURCE_TOKENS_PER_EXTRA_ITERATION = 4000
_MODEL_CONTEXT_WINDOW_EXCEEDED = "model_context_window_exceeded"

OLLAMA_BUILD_STOPPED_REASON = "Ollama stopped responding. Start Ollama and build again."
READING_SOURCES_ACTIVITY = "Reading the sources"


class RunOutcome(Enum):
    COMPLETED = "completed"
    EXHAUSTED = "exhausted"
    COST_CAPPED = "cost_capped"
    TOO_LARGE = "too_large"
    FAILED = "failed"
    STOPPED = "stopped"
    PROVIDER_FAILED = "provider_failed"


class RunResult(NamedTuple):
    cost: float
    outcome: RunOutcome
    reason: str = ""
    changes: tuple[dict, ...] = ()


class _AgentRequest(NamedTuple):
    system: str | list[dict]
    tools: list[dict]
    messages: list[dict]
    source_tokens: int


def too_large_reason(estimated_tokens: int) -> str:
    return (
        f"Too large to build in one run (about {estimated_tokens // 1000}k tokens). "
        "Split it into smaller files and add them again."
    )


def estimate_source_tokens(raw_dir: Path, sources: list[str]) -> int:
    """Estimate inline source tokens from file sizes, allowing four bytes per token."""
    total_bytes = sum(
        (raw_dir / rel).stat().st_size for rel in sources if (raw_dir / rel).exists()
    )
    return total_bytes // 4


def _create_executor(config: Config, sources: list[str]) -> WikiToolExecutor:
    """Create wiki tools with an optional search snapshot for this conversation."""
    read_tools = None
    if config.compilation.explore_tools:
        from second_brain.mcp_server.search import SearchIndex
        from second_brain.mcp_server.tools import WikiTools

        # Search stays fixed to the wiki before this conversation; file tools
        # can still read pages written during it.
        search_index = SearchIndex(config.search_db_path, config.search)
        search_index.sync_from_wiki(config.wiki_dir)
        read_tools = WikiTools(config.wiki_dir, config.raw_dir, search_index)

    return WikiToolExecutor(
        config.wiki_dir, config.raw_dir, read_tools=read_tools, sources=sources
    )


def _prepare_request(
    config: Config, sources: list[str], profile: ProviderProfile
) -> _AgentRequest:
    """Prepare the source context and provider-specific prompt caching.

    Parameters
    ----------
    config: Config
        Wiki paths and enabled tools.
    sources: list[str]
        Raw-relative paths compiled in this conversation.
    profile: ProviderProfile
        Provider caching capabilities.

    Returns
    -------
    _AgentRequest
        Initial messages, system prompt, tools, and estimated source tokens.
    """
    pages = discover_all_pages(config.wiki_dir)
    instructions = build_compilation_prompt(
        sources,
        gaps=rank_gaps(pages, build_link_graph(pages)),
        domains=[domain.name for domain in list_domains(config.wiki_dir)],
    )
    explore_schemas: list[dict] = []
    if config.compilation.explore_tools:
        instructions = f"{instructions}\n\n{EXPLORE_TOOLS_GUIDANCE}"
        explore_schemas = explore_tool_schemas()
    source_block = build_source_block(sources, config.raw_dir)
    source_tokens = len(source_block) // 4
    user_content: list[dict] = [
        {"type": "text", "text": instructions},
        {"type": "text", "text": f"Source documents:\n\n{source_block}"},
    ]
    tools = [dict(tool) for tool in (*WIKI_TOOLS, *explore_schemas)]
    system: str | list[dict] = COMPILATION_SYSTEM_PROMPT
    if profile.prompt_caching:
        # Static instructions survive gaps between groups; source text changes
        # per group and uses the provider's shorter default cache lifetime.
        system = [
            {
                "type": "text",
                "text": COMPILATION_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
        ]
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral", "ttl": "1h"}}
        if source_tokens >= profile.min_cacheable_tokens:
            user_content[-1]["cache_control"] = {"type": "ephemeral"}
    return _AgentRequest(system, tools, [{"role": "user", "content": user_content}], source_tokens)


def _execute_tools(
    executor: WikiToolExecutor,
    content: list[ContentBlock],
    report: Callable[[str], None],
) -> list[dict]:
    """Execute requested tools in order and collect their replies.

    Parameters
    ----------
    executor: WikiToolExecutor
        Wiki tools scoped to the current sources.
    content: list[ContentBlock]
        Model response blocks, including any tool requests.
    report: Callable[[str], None]
        Publish the activity before each tool executes.

    Returns
    -------
    list[dict]
        Tool results to include in the next model turn.
    """
    replies: list[dict] = []
    for block in content:
        if block.type != "tool_use":
            continue
        report(describe_tool_call(block.name, block.input))
        reply = executor.execute(block.name, block.input)
        logger.debug("Tool %s(%s) -> %s", block.name, block.input, reply[:200])
        replies.append({"type": "tool_result", "tool_use_id": block.id, "content": reply})
    return replies


def run_agent(
    config: Config,
    sources: list[str],
    *,
    started_at: str,
    base_cost: float = 0.0,
    progress: tuple[int, int] | None = None,
    unit_started_at: str = "",
) -> RunResult:
    """Run model turns and tools until one source conversation ends.

    Parameters
    ----------
    config: Config
        Provider, source paths, and conversation limits.
    sources: list[str]
        Raw-relative paths of one document, chat, or chat group.
    started_at: str
        Start time of the overall build.
    base_cost: float
        Cost spent by earlier conversations in this build.
    progress: tuple[int, int] | None
        Current conversation index and total count for progress reporting.
    unit_started_at: str
        Start time of this conversation.

    Returns
    -------
    RunResult
        Accumulated cost, outcome, reason, and wiki changes. The caller decides
        whether to commit or roll back the conversation's edits.
    """
    from second_brain.api.status import stop_requested, write_status

    profile = resolve_profile(config.compilation.provider, config.compilation.model)
    client = create_client(profile)
    executor = _create_executor(config, sources)
    request = _prepare_request(config, sources, profile)
    max_iterations = max(
        config.compilation.max_iterations,
        request.source_tokens // _SOURCE_TOKENS_PER_EXTRA_ITERATION,
    )
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_read_tokens = 0
    total_cache_write_tokens = 0
    cost = 0.0
    activity = READING_SOURCES_ACTIVITY
    current, total = progress if progress else (0, 0)

    def report(description: str) -> None:
        nonlocal activity
        activity = description
        write_status(
            config.data_dir,
            phase="compile",
            current=current,
            total=total,
            started_at=started_at,
            cost_usd=base_cost + cost,
            current_sources=tuple(sources),
            current_started_at=unit_started_at,
            current_cost_usd=cost,
            current_activity=activity,
        )

    outcome = RunOutcome.EXHAUSTED
    reason = f"The model didn't finish within the {max_iterations}-step limit."
    cost_cap = config.compilation.max_cost_per_build_usd
    for iteration in range(max_iterations):
        if stop_requested(config.data_dir):
            logger.info("Stop requested after %d model turns", iteration)
            break
        compact_history(request.messages)
        try:
            response = request_turn(
                client,
                model=profile.model,
                max_tokens=_MAX_OUTPUT_TOKENS_PER_TURN,
                system=request.system,
                tools=request.tools,
                messages=request.messages,
            )
        except ModelError as error:
            if error.kind is ModelErrorKind.TOO_LARGE:
                outcome = RunOutcome.TOO_LARGE
                reason = too_large_reason(estimate_source_tokens(config.raw_dir, sources))
            else:
                outcome = RunOutcome.PROVIDER_FAILED
                reason = error.reason
            logger.warning("Provider error for %s: %s", ", ".join(sources), reason)
            break

        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens
        total_cache_read_tokens += getattr(response.usage, "cache_read_input_tokens", 0) or 0
        total_cache_write_tokens += getattr(response.usage, "cache_creation_input_tokens", 0) or 0
        cost = profile.estimate_cost(
            total_input_tokens,
            total_output_tokens,
            cache_read_tokens=total_cache_read_tokens,
            cache_write_tokens=total_cache_write_tokens,
        )
        report(activity)
        logger.debug(
            "Turn %d: +%d in (+%d cached), +%d out (~$%.2f)",
            iteration + 1,
            response.usage.input_tokens,
            getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            response.usage.output_tokens,
            cost,
        )
        if response.stop_reason == _MODEL_CONTEXT_WINDOW_EXCEEDED:
            outcome = RunOutcome.TOO_LARGE
            reason = too_large_reason(estimate_source_tokens(config.raw_dir, sources))
            break
        request.messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "end_turn":
            outcome = RunOutcome.COMPLETED
            reason = ""
            logger.info("Agent completed after %d model turns", iteration + 1)
            break
        if cost_cap > 0 and base_cost + cost >= cost_cap:
            outcome = RunOutcome.COST_CAPPED
            reason = "Reached the spend cap per build."
            logger.warning("Build cost cap reached (~$%.2f >= $%.2f)", base_cost + cost, cost_cap)
            break

        try:
            tool_results = _execute_tools(executor, response.content, report)
        except OllamaUnavailableError:
            outcome = RunOutcome.FAILED
            reason = OLLAMA_BUILD_STOPPED_REASON
            logger.warning("Ollama isn't responding for %s: %s", ", ".join(sources), reason)
            break
        if not tool_results:
            reason = "The model stopped before finishing this source."
            logger.warning("Model stopped early (stop_reason=%s)", response.stop_reason)
            break
        request.messages.append({"role": "user", "content": tool_results})

    executor.finalize_provenance()
    logger.info(
        "Agent finished (%s): %d changes, %d in + %d out tokens (%d cache read), ~$%.2f",
        outcome.value,
        len(executor.changes),
        total_input_tokens,
        total_output_tokens,
        total_cache_read_tokens,
        cost,
    )
    return RunResult(cost, outcome, reason, tuple(executor.changes))
