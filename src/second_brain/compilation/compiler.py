"""Compilation orchestrator — agentic content synthesis then deterministic rebuild."""

from __future__ import annotations

import logging
import subprocess
import threading
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from second_brain.compilation.agent import (
    COMPILATION_SYSTEM_PROMPT,
    EXPLORE_TOOLS_GUIDANCE,
    WIKI_TOOLS,
    WikiToolExecutor,
    build_compilation_prompt,
    build_source_block,
    compact_history,
    explore_tool_schemas,
)
from second_brain.config import Config
from second_brain.ingestion.manifest import Manifest
from second_brain.llm import (
    ModelError,
    ModelErrorKind,
    create_client,
    request_turn,
    require_api_key,
    resolve_profile,
)
from second_brain.run_record import StageOutcome
from second_brain.triage.skipped import purge_skipped
from second_brain.wiki.structure import rebuild_structure

logger = logging.getLogger(__name__)


# Per-turn output cap for one agent Messages API call.
# NOTE: provider limits are Opus/Sonnet 128K, Haiku 64K, DeepSeek V4 ~384K.
_MAX_OUTPUT_TOKENS_PER_TURN = 16384

# Fraction of the model's context window a unit's inline source text may occupy
_INLINE_SOURCE_WINDOW_FRACTION = 0.6
_SOURCE_TOKENS_PER_EXTRA_ITERATION = 4000

# The prompt fit, but the reply had no room left in the context window.
_MODEL_CONTEXT_WINDOW_EXCEEDED = "model_context_window_exceeded"

_UNEXPECTED_ERROR_REASON = "An unexpected error stopped the build; see logs/pipeline.log"


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


def _split_oversized(clusters: list[list[str]], max_size: int) -> list[list[str]]:
    """Split clusters larger than ``max_size`` into bounded batches.

    A single agent run stays a bounded, coherent unit, so any cluster
    larger than the cap is divided into batches that each fit one run.

    Parameters
    ----------
    clusters: list[list[str]]
        Source clusters to bound.
    max_size: int
        Maximum number of sources per agent run.

    Returns
    -------
    list[list[str]]
        Work units, each with at most ``max_size`` sources.
    """
    units: list[list[str]] = []
    for cluster in clusters:
        if len(cluster) <= max_size:
            units.append(cluster)
            continue
        for start in range(0, len(cluster), max_size):
            units.append(cluster[start : start + max_size])
    return units


def _build_work_units(config: Config, raw_dir: Path, new_sources: list[str]) -> list[list[str]]:
    """Group staged sources into the per-run work units for this build.

    A reviewed cluster preview drives the build where each group is narrowed to the
    sources still staged, and any staged source the preview did not cover is
    compiled on its own. With no preview, sources are clustered fresh when
    clustering is enabled, otherwise each compiles alone. Groups beyond the
    per-run source budget are split.

    Parameters
    ----------
    config: Config
        Application configuration (clustering and embedding settings).
    raw_dir: Path
        Directory containing raw parsed source files.
    new_sources: list[str]
        Staged source paths relative to ``raw_dir``.

    Returns
    -------
    list[list[str]]
        Source groups, one per agent run.
    """
    from second_brain.clustering import cluster_scoped_sources, get_clusterer
    from second_brain.clustering.preview import reconcile_work_units

    reconciled = reconcile_work_units(config.data_dir, new_sources)
    if reconciled is not None:
        clusters = reconciled
        logger.info("Using reviewed cluster preview (%d work units)", len(clusters))
    elif config.clustering.enabled:
        clusters = cluster_scoped_sources(
            new_sources,
            raw_dir,
            config.search,
            get_clusterer(config.clustering),
            config.clustering.sources,
            signature_chars=config.clustering.signature_chars,
        )
        logger.info("Auto-clustered %d sources into %d groups", len(new_sources), len(clusters))
    else:
        clusters = [[source] for source in new_sources]

    return _split_oversized(clusters, config.clustering.max_sources_per_run)


def _too_large_reason(estimated_tokens: int) -> str:
    return (
        f"too large to compile in one run (~{estimated_tokens // 1000}k tokens); "
        "split it into parts and re-drop"
    )


def _estimate_unit_tokens(raw_dir: Path, unit: list[str]) -> int:
    """Rough token count of a unit's inline source text (bytes / 4)."""
    total_bytes = sum((raw_dir / rel).stat().st_size for rel in unit if (raw_dir / rel).exists())
    return total_bytes // 4


def find_new_sources(config: Config, manifest: Manifest) -> list[str]:
    """
    Find raw source files that haven't been compiled yet.

    Deferred sources (a prior run ended without completing) are excluded,
    so scheduled builds don't retry them unattended.

    Parameters
    ----------
    config: Config
        Application configuration.
    manifest: Manifest
        Ingestion manifest tracking compiled paths.

    Returns
    -------
    list[str]
        Sorted relative paths of uncompiled raw sources.
    """
    raw_dir = config.raw_dir
    if not raw_dir.exists():
        return []

    compiled = manifest.get_compiled_raw_paths()
    deferred = manifest.get_deferred_sources()
    new_sources: list[str] = []

    for md_file in raw_dir.rglob("*.md"):
        relative = md_file.relative_to(raw_dir)
        if any(part.startswith(".") for part in relative.parts):
            continue  # skip the .skipped/ holding folder and other dotfiles
        rel = str(relative)
        if rel not in compiled and rel not in deferred:
            new_sources.append(rel)

    return sorted(new_sources)


def _source_count(count: int) -> str:
    noun = "source" if count == 1 else "sources"
    return f"{count} {noun}"


def _set_aside_reason(deferred: int) -> str:
    verb = "was" if deferred == 1 else "were"
    return f"{_source_count(deferred)} {verb} set aside"


def _spend_cap_reason(left: int) -> str:
    """Sentence for a build the spend cap stopped, including sources still staged."""
    return f"Reached the spend cap with {_source_count(left)} left"


def _finalize_outcome(
    outcome: StageOutcome,
    reason: str,
    deferred: int,
    left: int,
) -> tuple[StageOutcome, str]:
    """Fill in the run outcome once every unit has been classified.

    A user stop, a failure, or the spend cap is decided in the loop. Anything
    else is a partial run when sources were set aside, and a clean run
    otherwise.
    """
    if outcome is StageOutcome.STOPPED:
        return outcome, ""
    if outcome is StageOutcome.FAILED:
        return outcome, reason
    if outcome is StageOutcome.CAPPED:
        return outcome, _spend_cap_reason(left)
    if deferred:
        return StageOutcome.PARTIAL, _set_aside_reason(deferred)
    return StageOutcome.OK, ""


def _outcome_fields(
    outcome: StageOutcome,
    reason: str,
    completed: int = 0,
    deferred: int = 0,
    left: int = 0,
) -> dict[str, StageOutcome | str | dict[str, int]]:
    return {
        "outcome": outcome,
        "reason": reason,
        "counts": {
            "completed": completed,
            "deferred": deferred,
            "left": left,
        },
    }


def run_compilation(
    config: Config,
    manifest: Manifest,
    force_full: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Run the full compilation pipeline.

    Executes agent-driven content synthesis, deterministic
    structure rebuild, and git auto-commit.

    Parameters
    ----------
    config: Config
        Application configuration.
    manifest: Manifest
        Ingestion manifest tracking compiled paths.
    force_full: bool
        If ``True``, recompile all sources regardless of
        manifest state.
    dry_run: bool
        If ``True``, skip agent execution and git commit.

    Returns
    -------
    dict
        Summary statistics including ``sources_compiled``, ``outcome``,
        ``reason`` (one sentence, or empty), ``counts`` (``completed``,
        ``deferred``, and ``left`` source counts), and the
        structure rebuild's keys.
    """
    wiki_dir = config.wiki_dir
    raw_dir = config.raw_dir

    from second_brain.wiki.schema import write_default_schema

    if not (wiki_dir / "_meta" / "topic_schema.yaml").exists():
        write_default_schema(wiki_dir)

    if force_full:
        new_sources = sorted(str(f.relative_to(raw_dir)) for f in raw_dir.rglob("*.md"))
    else:
        new_sources = find_new_sources(config, manifest)

    if not new_sources:
        logger.info("No new sources to compile")
        stats = rebuild_structure(wiki_dir)
        return {**stats, "sources_compiled": 0, **_outcome_fields(StageOutcome.OK, "")}

    # Triage already ran during ingestion (free, local). Here we just
    # filter to the worthwhile set from the recorded decisions; any
    # untriaged file (e.g. triage was disabled) passes through.
    if config.triage.enabled:
        from second_brain.triage.pipeline import triage_pending, worthwhile_sources

        # Catch anything ingested before triage existed.
        triage_pending(config, manifest)
        new_sources = worthwhile_sources(manifest, new_sources)

    if not new_sources:
        logger.info("Nothing worthwhile to compile")
        stats = rebuild_structure(wiki_dir)
        return {**stats, "sources_compiled": 0, **_outcome_fields(StageOutcome.OK, "")}

    profile = resolve_profile(config.compilation.provider, config.compilation.model)
    logger.info("Compiling %d worthwhile sources", len(new_sources))

    compiled_count = 0
    deferred_count = 0
    left_count = 0
    outcome = StageOutcome.OK
    reason = ""
    if not dry_run:
        # Fail fast on a missing key, before any heartbeat or git work.
        require_api_key(profile)

        from second_brain.status import (
            clear_status,
            clear_stop,
            now_iso,
            stop_requested,
            touch_status,
            write_status,
        )

        # Group related sources into one run so a topic compiles once
        # rather than once per near-duplicate source.
        clear_stop(config.data_dir)  # drop any stale flag from a prior run
        started = now_iso()

        work_units = _build_work_units(config, raw_dir, new_sources)
        staged_count = sum(len(unit) for unit in work_units)

        total = len(work_units)
        cumulative_cost = 0.0

        # A single agent turn can block longer than the heartbeat staleness
        # window; refresh it on a timer so progress stays visible.
        stop_heartbeat = threading.Event()

        def _keepalive() -> None:
            while not stop_heartbeat.wait(5.0):
                touch_status(config.data_dir)

        heartbeat = threading.Thread(target=_keepalive, daemon=True)
        heartbeat.start()
        cost_cap = config.compilation.max_cost_per_build_usd
        try:
            for index, unit in enumerate(work_units):
                if stop_requested(config.data_dir):
                    logger.info("Stop requested — halting before group %d/%d", index + 1, total)
                    outcome = StageOutcome.STOPPED
                    break
                if cost_cap > 0 and cumulative_cost >= cost_cap:
                    logger.info(
                        "Cost cap reached (~$%.2f >= $%.2f) — stopping before group %d/%d; "
                        "%d left staged",
                        cumulative_cost,
                        cost_cap,
                        index + 1,
                        total,
                        total - index,
                    )
                    outcome = StageOutcome.CAPPED
                    break
                # determine if this source can leave working room in model context window
                estimated_tokens = _estimate_unit_tokens(raw_dir, unit)
                window = resolve_profile(
                    config.compilation.provider, config.compilation.model
                ).context_window_tokens
                if estimated_tokens > int(window * _INLINE_SOURCE_WINDOW_FRACTION):
                    manifest.defer_sources(unit, _too_large_reason(estimated_tokens))
                    logger.warning(
                        "Deferred %s pre-flight: ~%dk tokens exceeds the %dk-token window",
                        ", ".join(unit),
                        estimated_tokens // 1000,
                        window // 1000,
                    )
                    deferred_count += len(unit)
                    continue
                write_status(
                    config.data_dir,
                    phase="compile",
                    current=index,
                    total=total,
                    started_at=started,
                    cost_usd=cumulative_cost,
                )

                try:
                    result = _run_agent(
                        config,
                        wiki_dir,
                        raw_dir,
                        unit,
                        started_at=started,
                        base_cost=cumulative_cost,
                        progress=(index, total),
                    )
                except Exception:
                    # Caught rather than raised so the group in progress is rolled
                    # back below before the build stops.
                    logger.exception("Compile failed for %s", ", ".join(unit))
                    result = RunResult(0.0, RunOutcome.FAILED, _UNEXPECTED_ERROR_REASON)
                if stop_requested(config.data_dir):
                    result = RunResult(result.cost, RunOutcome.STOPPED)

                cumulative_cost += result.cost
                if result.outcome is RunOutcome.COMPLETED:
                    manifest.mark_compiled(unit)
                    _git_commit(wiki_dir)
                    compiled_count += len(unit)
                    continue

                # discard partial, uncommitted pages
                _git_restore(wiki_dir)
                if result.outcome is RunOutcome.STOPPED:
                    logger.info("Stopped during %s — rolled back partial work", ", ".join(unit))
                    outcome = StageOutcome.STOPPED
                    break
                elif result.outcome is RunOutcome.COST_CAPPED:
                    logger.info(
                        "Cost cap reached during %s — rolled back partial work; "
                        "%d group(s) left staged",
                        ", ".join(unit),
                        total - index,
                    )
                    outcome = StageOutcome.CAPPED
                    break
                elif result.outcome in (RunOutcome.PROVIDER_FAILED, RunOutcome.FAILED):
                    # A provider failure or an error in our own code would hit every
                    # later group the same way, and each attempt can already have
                    # spent money, so leave them all staged.
                    logger.error("Build stopped: %s", result.reason)
                    outcome = StageOutcome.FAILED
                    reason = result.reason
                    break
                elif result.outcome in (RunOutcome.EXHAUSTED, RunOutcome.TOO_LARGE):
                    # Retrying unattended would fail the same way and re-bill,
                    # so park the group where the user can see why.
                    manifest.defer_sources(unit, result.reason)
                    logger.warning("Deferred %s: %s", ", ".join(unit), result.reason)
                    deferred_count += len(unit)
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=2.0)
            clear_stop(config.data_dir)
            clear_status(config.data_dir)
        logger.info("Compiled %d sources for ~$%.2f", compiled_count, cumulative_cost)

        # Units the loop did not compile or defer are still staged, including
        # the one rolled back by a stop, a cap, or a failure.
        left_count = staged_count - compiled_count - deferred_count
        outcome, reason = _finalize_outcome(outcome, reason, deferred_count, left_count)

        # Only a build that compiled every staged source finalizes the user's
        # curation; anything less leaves decisions open for the next attempt.
        if compiled_count == staged_count:
            purge_skipped(raw_dir)
            from second_brain.clustering.preview import clear_preview

            clear_preview(config.data_dir)
    else:
        logger.info("[dry-run] Would compile: %s", new_sources)

    stats = rebuild_structure(wiki_dir)

    if not dry_run:
        # Record any domains the agent introduced this build so the schema stays
        # the canonical vocabulary it reuses next time.
        from second_brain.wiki.schema import register_domains

        register_domains(wiki_dir, set(stats.get("domains", {})))
        _git_commit(wiki_dir)

    return {
        **stats,
        "sources_compiled": compiled_count,
        **_outcome_fields(outcome, reason, compiled_count, deferred_count, left_count),
    }


def _run_agent(
    config: Config,
    wiki_dir: Path,
    raw_dir: Path,
    sources: list[str],
    started_at: str,
    base_cost: float = 0.0,
    progress: tuple[int, int] | None = None,
) -> RunResult:
    """
    Invoke the compilation agent via the Anthropic API.

    Runs a multi-turn tool-use loop (up to ``max_iterations``) where the
    agent reads sources and writes/edits wiki pages, updating the live
    status heartbeat (elapsed + cumulative cost) as it goes.

    Parameters
    ----------
    config: Config
        Application configuration (provides model name).
    wiki_dir: Path
        Root directory of the wiki.
    raw_dir: Path
        Directory containing raw parsed source files.
    sources: list[str]
        Relative paths of source documents to compile (typically one).
    started_at: str
        ISO timestamp of the overall Build run (for elapsed display).
    base_cost: float
        Cost already spent by earlier files in this Build, so the
        heartbeat shows a cumulative figure and the build-level cost cap
        can bind mid-run.
    progress: tuple[int, int] | None
        ``(index, total)`` of this file within the Build, for the i/n
        readout.

    Returns
    -------
    RunResult
        The run's USD cost, how it ended, and a human-readable reason
        when the run did not complete. Provider failures come back as
        outcomes rather than exceptions.
    """
    from second_brain.status import stop_requested, write_status

    profile = resolve_profile(config.compilation.provider, config.compilation.model)
    client = create_client(profile)

    # When exploration is enabled, give the agent the read-only wiki tools backed by a
    # one-time pre-run index snapshot. The agent's own writes don't touch the index, so
    # search/graph results stay fixed to the wiki as it was before this run.
    read_tools = None
    explore_schemas: list[dict] = []
    if config.compilation.explore_tools:
        from second_brain.mcp_server.search import SearchIndex
        from second_brain.mcp_server.tools import WikiTools

        search_index = SearchIndex(config.search_db_path, config.search)
        search_index.sync_from_wiki(wiki_dir)
        read_tools = WikiTools(wiki_dir, raw_dir, search_index)
        explore_schemas = explore_tool_schemas()

    executor = WikiToolExecutor(
        wiki_dir, raw_dir, data_dir=config.data_dir, read_tools=read_tools, sources=sources
    )

    # Present the source content inline so it can be cached as a stable prefix;
    # the agent then synthesizes rather than spending turns re-reading it.
    instructions = build_compilation_prompt(sources)
    if config.compilation.explore_tools:
        instructions = f"{instructions}\n\n{EXPLORE_TOOLS_GUIDANCE}"
    source_block = build_source_block(sources, raw_dir)
    user_content: list[dict] = [
        {"type": "text", "text": instructions},
        {"type": "text", "text": f"Source documents:\n\n{source_block}"},
    ]

    # Cache the static prefix (system + tools) at a 1h TTL so it survives the
    # gaps between groups, and the per-unit source at the default 5m TTL (it
    # changes each unit). Only Anthropic honors cache_control; DeepSeek caches
    # prefixes automatically.
    system: str | list[dict]
    tools = [dict(t) for t in (*WIKI_TOOLS, *explore_schemas)]
    if profile.prompt_caching:
        system = [
            {
                "type": "text",
                "text": COMPILATION_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
        ]
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral", "ttl": "1h"}}
        if len(source_block) // 4 >= profile.min_cacheable_tokens:
            user_content[-1]["cache_control"] = {"type": "ephemeral"}
    else:
        system = COMPILATION_SYSTEM_PROMPT

    messages: list[dict] = [{"role": "user", "content": user_content}]

    # A larger source warrants proportionally more turns
    max_iterations = max(
        config.compilation.max_iterations,
        len(source_block) // 4 // _SOURCE_TOKENS_PER_EXTRA_ITERATION,
    )
    cost_cap = config.compilation.max_cost_per_build_usd
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_read_tokens = 0
    total_cache_write_tokens = 0
    cur, tot = progress if progress else (0, 0)
    outcome = RunOutcome.EXHAUSTED
    outcome_reason = f"failed to compile within {max_iterations} agent turns"

    for iteration in range(max_iterations):
        # Honor a cancel between turns (the costly call is below), so a
        # stop lands within one agent round-trip.
        if stop_requested(config.data_dir):
            logger.info("Stop requested — halting agent after %d iterations", iteration)
            break

        # Shrink stale tool outputs before re-sending the history, so a
        # large early file read isn't billed on every later turn.
        compact_history(messages)

        try:
            response = request_turn(
                client,
                model=profile.model,
                max_tokens=_MAX_OUTPUT_TOKENS_PER_TURN,
                system=system,
                tools=tools,
                messages=messages,
            )
        except ModelError as exc:
            if exc.kind is ModelErrorKind.TOO_LARGE:
                outcome = RunOutcome.TOO_LARGE
                outcome_reason = _too_large_reason(_estimate_unit_tokens(raw_dir, sources))
            else:
                outcome = RunOutcome.PROVIDER_FAILED
                outcome_reason = exc.reason
            logger.warning("Provider error for %s: %s", ", ".join(sources), outcome_reason)
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
        write_status(
            config.data_dir,
            phase="compile",
            current=cur,
            total=tot,
            started_at=started_at,
            cost_usd=base_cost + cost,
        )
        logger.debug(
            "Iteration %d: +%d in (+%d cached), +%d out (~$%.2f)",
            iteration + 1,
            response.usage.input_tokens,
            getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            response.usage.output_tokens,
            cost,
        )

        if response.stop_reason == _MODEL_CONTEXT_WINDOW_EXCEEDED:
            outcome = RunOutcome.TOO_LARGE
            outcome_reason = _too_large_reason(_estimate_unit_tokens(raw_dir, sources))
            break

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            outcome = RunOutcome.COMPLETED
            outcome_reason = ""
            logger.info("Agent completed after %d iterations", iteration + 1)
            break

        # Stop before the next turn once the build's spend ceiling is hit.
        # The caller rolls this unit back, so stopping here only avoids
        # paying for further turns.
        if cost_cap > 0 and base_cost + cost >= cost_cap:
            outcome = RunOutcome.COST_CAPPED
            outcome_reason = "build cost cap reached"
            logger.warning(
                "Build cost cap reached (~$%.2f >= $%.2f) after %d iterations — stopping this run",
                base_cost + cost,
                cost_cap,
                iteration + 1,
            )
            break

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = executor.execute(block.name, block.input)
                logger.debug("Tool %s(%s) -> %s", block.name, block.input, result[:200])
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    }
                )

        if not tool_results:
            # Treat a turn that requested no tools but did not end cleanly
            # as not completed.
            outcome_reason = f"ended without completing (stop_reason={response.stop_reason})"
            break

        messages.append({"role": "user", "content": tool_results})

    # Stamp the build unit's sources onto every page the agent touched, so a page
    # updated from a new source accumulates it instead of losing earlier ones.
    executor.finalize_provenance()

    cost = profile.estimate_cost(
        total_input_tokens,
        total_output_tokens,
        cache_read_tokens=total_cache_read_tokens,
        cache_write_tokens=total_cache_write_tokens,
    )
    logger.info(
        "Agent finished (%s): %d changes, %d in + %d out tokens (%d cache read), ~$%.2f",
        outcome.value,
        len(executor.changes),
        total_input_tokens,
        total_output_tokens,
        total_cache_read_tokens,
        cost,
    )
    return RunResult(cost, outcome, outcome_reason)


def _git_restore(wiki_dir: Path) -> None:
    """
    Discard uncommitted wiki changes, restoring to the last commit.

    Parameters
    ----------
    wiki_dir: Path
        Root directory of the wiki.
    """
    if not (wiki_dir / ".git").exists():
        return
    try:
        subprocess.run(["git", "reset", "--hard"], cwd=wiki_dir, capture_output=True, check=True)
        subprocess.run(["git", "clean", "-fd"], cwd=wiki_dir, capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning("Could not roll back wiki to last commit: %s", e)


def _git_commit(wiki_dir: Path) -> None:
    """
    Checkpoint the wiki after a build step.
    """
    from second_brain.wiki.repo import commit_all

    commit_all(wiki_dir, "auto: compilation + structure rebuild")
