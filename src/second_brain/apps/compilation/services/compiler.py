"""Compile staged sources, checkpoint completed conversations, and rebuild wiki views."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import assert_never

from second_brain.apps.compilation.services.agent.loop import (
    READING_SOURCES_ACTIVITY,
    RunOutcome,
    RunResult,
    estimate_source_tokens,
    run_agent,
    too_large_reason,
)
from second_brain.apps.compilation.services.build_log import record_built_pages
from second_brain.apps.compilation.services.build_records import BuildStore
from second_brain.apps.compilation.services.staging import find_new_sources
from second_brain.apps.ingestion.services.triage.skipped import purge_skipped
from second_brain.apps.jobs.outcomes import CRASH_REASON, StageOutcome
from second_brain.apps.wiki.services.wiki_structure import rebuild_structure
from second_brain.errors import SecondBrainError
from second_brain.file_hashing import hash_file
from second_brain.llm import require_api_key, resolve_profile
from second_brain.pipeline_config import PipelineConfig

logger = logging.getLogger(__name__)

# Sources leave room for instructions, tools, and the conversation history.
_INLINE_SOURCE_WINDOW_FRACTION = 0.6


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


def _build_work_units(
    config: PipelineConfig, raw_dir: Path, new_sources: list[str]
) -> list[list[str]]:
    """Group staged sources into the per-run work units for this build.

    A reviewed cluster preview drives the build where each group is narrowed to the
    sources still staged, and any staged source the preview did not cover is
    compiled on its own. With no preview, sources are clustered fresh when
    clustering is enabled, otherwise each compiles alone. Groups beyond the
    per-run source budget are split.

    Parameters
    ----------
    config: PipelineConfig
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
    from second_brain.apps.compilation.services.clustering import (
        cluster_scoped_sources,
        get_clusterer,
    )
    from second_brain.apps.compilation.services.clustering.preview import reconcile_work_units

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


def _hash_sources(raw_dir: Path, raw_paths: list[str]) -> dict[str, str]:
    return {rel: hash_file(raw_dir / rel) for rel in raw_paths}


def _source_count(count: int) -> str:
    noun = "source" if count == 1 else "sources"
    return f"{count} {noun}"


def _set_aside_reason(deferred: int) -> str:
    verb = "was" if deferred == 1 else "were"
    return f"{_source_count(deferred)} {verb} set aside."


def _spend_cap_reason(left: int) -> str:
    """Sentence for a build the spend cap stopped, including sources still staged."""
    return f"Reached the spend cap per build with {_source_count(left)} left."


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
    match outcome:
        case StageOutcome.STOPPED:
            return outcome, ""
        case StageOutcome.FAILED:
            return outcome, reason
        case StageOutcome.CAPPED:
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


@contextmanager
def _build_progress() -> Iterator[None]:
    """Mark progress idle on exit, including cancellation or a failed conversation."""
    from second_brain.apps.jobs.services.job_progress import finish_progress

    try:
        yield
    finally:
        finish_progress()


def _run_unit(
    config: PipelineConfig,
    sources: list[str],
    *,
    started_at: str,
    base_cost: float,
    progress: tuple[int, int],
) -> RunResult:
    """Run a conversation with progress and recoverable failure reporting.

    Parameters
    ----------
    config: PipelineConfig
        Vault paths and model settings.
    sources: list[str]
        Sources in this conversation.
    started_at: str
        Overall build start time.
    base_cost: float
        Cost spent by earlier conversations.
    progress: tuple[int, int]
        Conversation index and total count.

    Returns
    -------
    RunResult
        Conversation outcome, including an explicit stop or unexpected failure.
    """
    from second_brain.apps.jobs.services.job_progress import (
        now_iso,
        stop_requested,
        update_progress,
    )

    unit_started_at = now_iso()
    current, total = progress
    update_progress(
        phase="compile",
        current=current,
        total=total,
        started_at=started_at,
        cost_usd=base_cost,
        current_sources=tuple(sources),
        current_started_at=unit_started_at,
        current_activity=READING_SOURCES_ACTIVITY,
    )
    try:
        result = run_agent(
            config,
            sources,
            started_at=started_at,
            base_cost=base_cost,
            progress=progress,
            unit_started_at=unit_started_at,
        )
    except SecondBrainError as exc:
        # Credentials/dependencies can change after request-time validation.
        # Preserve the expected reason while allowing the caller to roll back this unit.
        logger.warning("Compile stopped for %s: %s", ", ".join(sources), exc.reason)
        result = RunResult(0.0, RunOutcome.FAILED, exc.reason)
    except Exception:
        # Unexpected failures still need a result so the caller can roll back
        # the current conversation before stopping the build.
        logger.exception("Compile failed for %s", ", ".join(sources))
        result = RunResult(0.0, RunOutcome.FAILED, CRASH_REASON)
    if stop_requested():
        return RunResult(result.cost, RunOutcome.STOPPED)
    return result


def _compile_work_units(
    config: PipelineConfig,
    work_units: list[list[str]],
    build_store: BuildStore,
    *,
    started_at: str,
    on_unit_finished: Callable[[], None] | None,
) -> dict:
    """Compile each conversation and apply its commit, deferral, or stop outcome.

    Parameters
    ----------
    config: PipelineConfig
        Vault paths and build limits.
    work_units: list[list[str]]
        Source groups, in build order.
    build_store: BuildStore
        Compiled and set-aside source records.
    started_at: str
        Overall build start time.
    on_unit_finished: Callable[[], None] | None
        Refresh derived state after a commit or deferral.

    Returns
    -------
    dict
        Completed source count, build outcome, reason, and remaining counts.
    """
    from second_brain.apps.jobs.services.job_progress import stop_requested

    compiled_count = 0
    deferred_count = 0
    cumulative_cost = 0.0
    outcome = StageOutcome.OK
    reason = ""
    total = len(work_units)
    staged_count = sum(len(unit) for unit in work_units)
    cost_cap = config.compilation.max_cost_per_build_usd
    window = resolve_profile(
        config.compilation.provider, config.compilation.model
    ).context_window_tokens

    with _build_progress():
        for index, unit in enumerate(work_units):
            if stop_requested():
                logger.info("Stop requested before group %d/%d", index + 1, total)
                outcome = StageOutcome.STOPPED
                break
            if cost_cap > 0 and cumulative_cost >= cost_cap:
                logger.info("Build cost cap reached before group %d/%d", index + 1, total)
                outcome = StageOutcome.CAPPED
                break
            # An edit made after this snapshot remains staged for a later build.
            unit_hashes = _hash_sources(config.raw_dir, unit)
            estimated_tokens = estimate_source_tokens(config.raw_dir, unit)
            if estimated_tokens > int(window * _INLINE_SOURCE_WINDOW_FRACTION):
                build_store.defer_sources(
                    unit_hashes, too_large_reason(estimated_tokens), retryable=False
                )
                logger.warning("Deferred oversized source group: %s", ", ".join(unit))
                deferred_count += len(unit)
                if on_unit_finished is not None:
                    on_unit_finished()
                continue

            result = _run_unit(
                config,
                unit,
                started_at=started_at,
                base_cost=cumulative_cost,
                progress=(index, total),
            )
            cumulative_cost += result.cost
            if result.outcome is not RunOutcome.COMPLETED:
                _git_restore(config.wiki_dir)
            match result.outcome:
                case RunOutcome.COMPLETED:
                    _git_commit(config.wiki_dir)
                    build_store.mark_compiled(unit_hashes)
                    record_built_pages(config.data_dir, list(result.changes), unit)
                    compiled_count += len(unit)
                case RunOutcome.STOPPED:
                    logger.info("Stopped during %s; unfinished edits discarded", ", ".join(unit))
                    outcome = StageOutcome.STOPPED
                    break
                case RunOutcome.COST_CAPPED:
                    logger.info("Build cost cap reached during %s", ", ".join(unit))
                    outcome = StageOutcome.CAPPED
                    break
                case RunOutcome.PROVIDER_FAILED | RunOutcome.FAILED:
                    # These failures would affect later groups too; leave them staged.
                    logger.error("Build stopped: %s", result.reason)
                    outcome = StageOutcome.FAILED
                    reason = result.reason
                    break
                case RunOutcome.EXHAUSTED | RunOutcome.TOO_LARGE:
                    build_store.defer_sources(
                        unit_hashes,
                        result.reason,
                        retryable=result.outcome is RunOutcome.EXHAUSTED,
                    )
                    logger.warning("Deferred %s: %s", ", ".join(unit), result.reason)
                    deferred_count += len(unit)
                case _:
                    assert_never(result.outcome)
            if on_unit_finished is not None:
                on_unit_finished()

    logger.info("Compiled %d sources for ~$%.2f", compiled_count, cumulative_cost)
    left_count = staged_count - compiled_count - deferred_count
    outcome, reason = _finalize_outcome(outcome, reason, deferred_count, left_count)
    # Incomplete builds leave curation open for a later attempt.
    if compiled_count == staged_count and config.raw_input_dir is None:
        from second_brain.apps.compilation.services.clustering.preview import clear_preview

        purge_skipped(config.raw_dir)
        clear_preview(config.data_dir)
    return {
        "sources_compiled": compiled_count,
        **_outcome_fields(outcome, reason, compiled_count, deferred_count, left_count),
    }


def run_compilation(
    config: PipelineConfig,
    force_full: bool = False,
    dry_run: bool = False,
    on_unit_finished: Callable[[], None] | None = None,
    *,
    source_paths: list[str] | None = None,
    work_units: list[list[str]] | None = None,
) -> dict:
    """
    Run the full compilation pipeline.

    Executes agent-driven content synthesis, deterministic
    structure rebuild, and git auto-commit.

    Parameters
    ----------
    config: PipelineConfig
        Application configuration.
    force_full: bool
        If ``True``, recompile all sources regardless of
        what is already built.
    dry_run: bool
        If ``True``, skip agent execution and git commit.
    on_unit_finished: Callable[[], None] | None
        Called after each group is committed or set aside, so a reader of
        the staged set sees finished sources leave it during the build.

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
    build_store = BuildStore()

    from second_brain.apps.wiki.services.topic_schema import write_default_schema

    if not (wiki_dir / "_meta" / "topic_schema.yaml").exists():
        write_default_schema(wiki_dir)

    if source_paths is not None:
        new_sources = source_paths
    elif force_full:
        new_sources = sorted(str(f.relative_to(raw_dir)) for f in raw_dir.rglob("*.md"))
    else:
        new_sources = find_new_sources(config)

    if not new_sources:
        logger.info("No new sources to compile")
        stats = rebuild_structure(wiki_dir)
        return {**stats, "sources_compiled": 0, **_outcome_fields(StageOutcome.OK, "")}

    # Triage already ran during ingestion (free, local). Catch up anything
    # still undecided, then drop review, skip, and chats that never got a
    # decision so an interrupted sort cannot reach the paid build.
    if config.triage.enabled and source_paths is None:
        from second_brain.apps.ingestion.services.triage.pipeline import (
            triage_pending,
            worthwhile_sources,
        )

        # Catch anything ingested before triage existed.
        triage_pending(config)
        new_sources = worthwhile_sources(config, new_sources)

    if not new_sources:
        logger.info("Nothing worthwhile to compile")
        stats = rebuild_structure(wiki_dir)
        return {**stats, "sources_compiled": 0, **_outcome_fields(StageOutcome.OK, "")}

    profile = resolve_profile(config.compilation.provider, config.compilation.model)
    logger.info("Compiling %d worthwhile sources", len(new_sources))
    summary = {"sources_compiled": 0, **_outcome_fields(StageOutcome.OK, "")}
    if dry_run:
        logger.info("[dry-run] Would compile: %s", new_sources)
    else:
        from second_brain.apps.jobs.services.job_progress import now_iso

        require_api_key(profile)
        _git_commit(wiki_dir)

        started_at = now_iso()
        if work_units is None:
            work_units = _build_work_units(config, raw_dir, new_sources)
        summary = _compile_work_units(
            config,
            work_units,
            build_store,
            started_at=started_at,
            on_unit_finished=on_unit_finished,
        )

    stats = rebuild_structure(wiki_dir)
    if not dry_run:
        from second_brain.apps.wiki.services.topic_schema import register_domains

        register_domains(wiki_dir, set(stats.get("domains", {})))
        _git_commit(wiki_dir)
    return {**stats, **summary}


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
    from second_brain.apps.wiki.services.git_history import commit_all

    commit_all(wiki_dir, "auto: compilation + structure rebuild")
