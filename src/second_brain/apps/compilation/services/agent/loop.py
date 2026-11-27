"""Run one model-and-tool conversation for a source or chat group."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from second_brain.apps.compilation.services.agent.prompt import (
    COMPILATION_SYSTEM_PROMPT,
    EXPLORE_TOOLS_GUIDANCE,
    WIKI_TOOLS,
)
from second_brain.apps.compilation.services.agent.tools import (
    WikiToolExecutor,
    build_compilation_prompt,
    build_source_block,
    compact_history,
    describe_tool_call,
    explore_tool_schemas,
)
from second_brain.apps.inference.errors import InferenceUnavailableError
from second_brain.apps.wiki.services.domain_actions import list_domains
from second_brain.apps.wiki.services.wiki_structure import (
    build_link_graph,
    discover_all_pages,
    rank_gaps,
)
from second_brain.cloud_llm.pi import pi_helper
from second_brain.pipeline_config import PipelineConfig

logger = logging.getLogger(__name__)

_SOURCE_TOKENS_PER_EXTRA_ITERATION = 4000

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
    cost: float | None  # None when the provider's charge for some turn is unknown.
    outcome: RunOutcome
    reason: str = ""
    changes: tuple[dict, ...] = ()


# Turns that produced no reply to act on: how the conversation ends, and what the person
# can do about it.
_PROVIDER_FAILED = (RunOutcome.PROVIDER_FAILED, "The model provider did not answer. Build again.")
_TURN_FAILURES = {
    "not_connected": (RunOutcome.FAILED, "Connect a provider in Settings."),
    "model_unavailable": (RunOutcome.FAILED, "Choose a model on the Build tab."),
    "error": _PROVIDER_FAILED,
    "incomplete": (RunOutcome.EXHAUSTED, "The model stopped before finishing this source."),
}


def add_cost(total: float | None, cost: float | None) -> float | None:
    """Add a cost to a running total. An unknown cost makes the total unknown, not smaller."""
    return None if total is None or cost is None else total + cost


def describe_cost(cost: float | None) -> str:
    """Format a cost for the log."""
    return "an unknown cost" if cost is None else f"~${cost:.2f}"


def too_large_reason(estimated_tokens: int) -> str:
    """Describe why a source exceeds the model input limit.

    Parameters
    ----------
    estimated_tokens: int
        Estimated input size in tokens.

    Returns
    -------
    str
        Error reason with the approximate size and advice to split the source.
    """
    return (
        f"Too large to build in one run (about {estimated_tokens // 1000}k tokens). "
        "Split it into smaller files and add them again."
    )


def estimate_source_tokens(raw_dir: Path, sources: list[str]) -> int:
    """Estimate inline source tokens from file sizes, allowing four bytes per token.

    Parameters
    ----------
    raw_dir: Path
        Root containing raw Markdown sources.
    sources: list[str]
        Paths relative to raw_dir; missing files contribute no tokens.

    Returns
    -------
    int
        Sum of existing file sizes divided by four, rounded down to whole tokens.
    """
    total_bytes = sum(
        (raw_dir / rel).stat().st_size for rel in sources if (raw_dir / rel).exists()
    )
    return total_bytes // 4


def _create_executor(config: PipelineConfig, sources: list[str]) -> WikiToolExecutor:
    """Create wiki tools with an optional search snapshot for this conversation."""
    read_tools = None
    if config.compilation.explore_tools:
        from second_brain.apps.wiki.services.assistant_tools import WikiTools
        from second_brain.apps.wiki.services.search_index import SearchIndex

        # Search stays fixed to the wiki before this conversation; file tools
        # can still read pages written during it.
        search_index = SearchIndex(config.search_db_path)
        search_index.sync_from_wiki(config.wiki_dir)
        read_tools = WikiTools(config.wiki_dir, config.raw_dir, search_index)

    return WikiToolExecutor(
        config.wiki_dir, config.raw_dir, read_tools=read_tools, sources=sources
    )


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _prepare_request(config: PipelineConfig, sources: list[str]) -> tuple[dict, int]:
    """Prepare the opening Pi context for one conversation.

    Parameters
    ----------
    config: PipelineConfig
        Wiki paths and enabled tools.
    sources: list[str]
        Raw-relative paths compiled in this conversation.

    Returns
    -------
    tuple[dict, int]
        Pi context (system prompt, tools, and the first user message) and the
        estimated source tokens.
    """
    pages = discover_all_pages(config.wiki_dir)
    instructions = build_compilation_prompt(
        sources,
        gaps=rank_gaps(pages, build_link_graph(pages)),
        domains=[domain.name for domain in list_domains(config.wiki_dir)],
    )
    schemas = list(WIKI_TOOLS)
    if config.compilation.explore_tools:
        instructions = f"{instructions}\n\n{EXPLORE_TOOLS_GUIDANCE}"
        schemas += explore_tool_schemas()
    source_block = build_source_block(sources, config.raw_dir)
    context = {
        "systemPrompt": COMPILATION_SYSTEM_PROMPT,
        "tools": [
            {
                "name": schema["name"],
                "description": schema["description"],
                "parameters": schema["input_schema"],
            }
            for schema in schemas
        ],
        # Instructions and sources stay separate blocks, so a provider can cache the
        # prefix they form without the conversation that follows.
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": instructions},
                    {"type": "text", "text": f"Source documents:\n\n{source_block}"},
                ],
                "timestamp": _now_ms(),
            }
        ],
    }
    return context, len(source_block) // 4


def _execute_tools(
    executor: WikiToolExecutor,
    reply: dict,
    report: Callable[[str], None],
    stop_requested: Callable[[], bool],
) -> list[dict]:
    """Execute requested tools in order and collect their results.

    Parameters
    ----------
    executor: WikiToolExecutor
        Wiki tools scoped to the current sources.
    reply: dict
        Pi assistant message from a tool-use turn.
    report: Callable[[str], None]
        Publish the activity before each tool executes.
    stop_requested: Callable[[], bool]
        Checked before each tool. Once true, the remaining tools do not run.

    Returns
    -------
    list[dict]
        Pi tool-result messages to include in the next model turn.
    """
    results: list[dict] = []
    for call in reply["content"]:
        if call["type"] != "toolCall":
            continue
        if stop_requested():
            break
        if "argumentsError" in call:
            # Pi could not read these arguments, so there is nothing to run. The model
            # gets the parse error back and can ask again.
            text, failed = call["argumentsError"], True
        else:
            report(describe_tool_call(call["name"], call["arguments"]))
            text, failed = executor.execute(call["name"], call["arguments"]), False
            logger.debug("Tool %s(%s) -> %s", call["name"], call["arguments"], text[:200])
        results.append(
            {
                "role": "toolResult",
                "toolCallId": call["id"],
                "toolName": call["name"],
                "content": [{"type": "text", "text": text}],
                "isError": failed,
                "timestamp": _now_ms(),
            }
        )
    return results


def run_agent(
    config: PipelineConfig,
    sources: list[str],
    *,
    started_at: str,
    base_cost: float | None = 0.0,
    progress: tuple[int, int] | None = None,
    unit_started_at: str = "",
) -> RunResult:
    """Run model turns and tools until one source conversation ends.

    Parameters
    ----------
    config: PipelineConfig
        Provider, model, source paths, and conversation limits.
    sources: list[str]
        Raw-relative paths of one document, chat, or chat group.
    started_at: str
        Start time of the overall build.
    base_cost: float | None
        Cost spent by earlier conversations in this build, or None if unknown.
    progress: tuple[int, int] | None
        Current conversation index and total count for progress reporting.
    unit_started_at: str
        Start time of this conversation.

    Returns
    -------
    RunResult
        Accumulated cost, outcome, reason, and wiki changes. Edits remain uncommitted
        in the working tree.
    """
    from second_brain.apps.jobs.services.job_progress import stop_requested, update_progress

    executor = _create_executor(config, sources)
    context, source_tokens = _prepare_request(config, sources)
    messages: list[dict] = context["messages"]
    max_iterations = max(
        config.compilation.max_iterations,
        source_tokens // _SOURCE_TOKENS_PER_EXTRA_ITERATION,
    )
    cost: float | None = 0.0
    activity = READING_SOURCES_ACTIVITY
    current, total = progress if progress else (0, 0)

    def report(description: str) -> None:
        nonlocal activity
        activity = description
        update_progress(
            phase="compile",
            current=current,
            total=total,
            started_at=started_at,
            cost_usd=add_cost(base_cost, cost),
            current_sources=tuple(sources),
            current_started_at=unit_started_at,
            current_cost_usd=cost,
            current_activity=activity,
        )

    outcome = RunOutcome.EXHAUSTED
    reason = f"The model didn't finish within the {max_iterations}-step limit."
    cost_cap = config.compilation.max_cost_per_build_usd
    for iteration in range(max_iterations):
        if stop_requested():
            logger.info("Stop requested after %d model turns", iteration)
            break
        compact_history(messages)
        try:
            turn = pi_helper.run_turn(
                config.compilation.provider, config.compilation.model, context, stop_requested
            )
        except pi_helper.PiHelperError as error:
            outcome, reason = RunOutcome.PROVIDER_FAILED, error.reason
            logger.warning("Provider helper failed for %s: %s", ", ".join(sources), reason)
            break
        if turn is None:
            logger.info("Stop requested during model turn %d", iteration + 1)
            break

        # The helper reports no cost when it sent nothing, and null when the cost is unknown.
        cost = add_cost(cost, turn.get("cost_usd", 0.0))
        report(activity)
        logger.debug("Turn %d: %s (%s)", iteration + 1, turn["outcome"], describe_cost(cost))
        if turn["outcome"] == "too_large":
            outcome = RunOutcome.TOO_LARGE
            reason = too_large_reason(estimate_source_tokens(config.raw_dir, sources))
            break
        if turn["outcome"] == "stop":
            outcome = RunOutcome.COMPLETED
            reason = ""
            logger.info("Agent completed after %d model turns", iteration + 1)
            break
        spent = add_cost(base_cost, cost)
        # An unknown cost cannot be held to the cap.
        if cost_cap > 0 and spent is not None and spent >= cost_cap:
            outcome = RunOutcome.COST_CAPPED
            reason = "Reached the spend cap per build."
            logger.warning("Build cost cap reached (~$%.2f >= $%.2f)", spent, cost_cap)
            break
        if turn["outcome"] != "toolUse":
            # Only a tool-use turn carries calls to run. Anything else ends the conversation.
            outcome, reason = _TURN_FAILURES.get(turn["outcome"], _PROVIDER_FAILED)
            logger.warning("No usable reply for %s: %s", ", ".join(sources), turn["outcome"])
            break

        # Appended as Pi returned it: its thinking blocks and signatures must be replayed intact.
        messages.append(turn["message"])
        try:
            messages += _execute_tools(executor, turn["message"], report, stop_requested)
        except InferenceUnavailableError as exc:
            outcome = RunOutcome.FAILED
            reason = exc.reason
            logger.warning("Local inference failed for %s: %s", ", ".join(sources), reason)
            break

    executor.finalize_provenance()
    logger.info(
        "Agent finished (%s): %d changes, %s",
        outcome.value,
        len(executor.changes),
        describe_cost(cost),
    )
    return RunResult(cost, outcome, reason, tuple(executor.changes))
