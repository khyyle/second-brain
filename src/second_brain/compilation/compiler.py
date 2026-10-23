"""Compile staged sources, checkpoint completed conversations, and rebuild wiki views."""

from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import assert_never

from second_brain.api.build_log import record_built_pages
from second_brain.api.run_record import CRASH_REASON, StageOutcome
from second_brain.compilation.agent.loop import (
    READING_SOURCES_ACTIVITY,
    RunOutcome,
    RunResult,
    estimate_source_tokens,
    run_agent,
    too_large_reason,
)
from second_brain.compilation.store import BuildStore, DeferredSource
from second_brain.config import Config
from second_brain.hashing import hash_file
from second_brain.ingestion.triage.skipped import purge_skipped
from second_brain.llm import require_api_key, resolve_profile
from second_brain.wiki.structure import rebuild_structure

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
    from second_brain.compilation.clustering import cluster_scoped_sources, get_clusterer
    from second_brain.compilation.clustering.preview import reconcile_work_units

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


def _raw_sources(raw_dir: Path) -> list[str]:
    """Relative paths of every raw markdown source, outside hidden folders such as
    the skipped holding folder."""
    if not raw_dir.exists():
        return []
    sources: list[str] = []
    for md_file in raw_dir.rglob("*.md"):
        relative = md_file.relative_to(raw_dir)
        if not any(part.startswith(".") for part in relative.parts):
            sources.append(str(relative))
    return sorted(sources)


def _record_is_current(recorded_hash: str | None, raw_file: Path) -> bool:
    # Records made before hashes were kept have none; trust them rather than
    # paying to rebuild every source.
    return recorded_hash is None or recorded_hash == hash_file(raw_file)


def _hash_sources(raw_dir: Path, raw_paths: list[str]) -> dict[str, str]:
    return {rel: hash_file(raw_dir / rel) for rel in raw_paths}


def find_new_sources(config: Config) -> list[str]:
    """
    Find raw source files that haven't been compiled in their current version.

    A source whose file changed since it was built is staged again. Set-aside
    sources (a prior run ended without completing) are excluded until their
    file changes, so scheduled builds don't retry them unattended.

    Parameters
    ----------
    config: Config
        Application configuration.

    Returns
    -------
    list[str]
        Sorted relative paths of raw sources to compile.
    """
    raw_dir = config.raw_dir
    build_store = BuildStore(config.manifest_db_path)
    compiled_hashes = build_store.get_compiled()
    deferred_sources = build_store.get_deferred()

    new_sources: list[str] = []
    for rel in _raw_sources(raw_dir):
        raw_file = raw_dir / rel
        if rel in deferred_sources and _record_is_current(
            deferred_sources[rel].content_hash, raw_file
        ):
            continue
        if rel in compiled_hashes and _record_is_current(compiled_hashes[rel], raw_file):
            continue
        new_sources.append(rel)
    return new_sources


def find_set_aside_sources(config: Config) -> dict[str, DeferredSource]:
    """
    Find set-aside sources whose file is still the version that was set aside.

    Parameters
    ----------
    config: Config
        Application configuration.

    Returns
    -------
    dict[str, DeferredSource]
        Relative raw path mapped to why the build set it aside.
    """
    raw_dir = config.raw_dir
    return {
        rel: deferred
        for rel, deferred in BuildStore(config.manifest_db_path).get_deferred().items()
        if (raw_dir / rel).exists() and _record_is_current(deferred.content_hash, raw_dir / rel)
    }


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
def _build_heartbeat(data_dir: Path) -> Iterator[None]:
    """Keep blocking model calls visible and clear build flags on exit.

    Parameters
    ----------
    data_dir: Path
        Vault containing the progress and stop files.

    Yields
    ------
    None
        The build can run while a thread refreshes its heartbeat.
    """
    from second_brain.api.status import clear_status, clear_stop, touch_status

    stop = threading.Event()

    def keepalive() -> None:
        while not stop.wait(5.0):
            touch_status(data_dir)

    heartbeat = threading.Thread(target=keepalive, daemon=True)
    heartbeat.start()
    try:
        yield
    finally:
        stop.set()
        heartbeat.join(timeout=2.0)
        clear_stop(data_dir)
        clear_status(data_dir)


def _run_unit(
    config: Config,
    sources: list[str],
    *,
    started_at: str,
    base_cost: float,
    progress: tuple[int, int],
) -> RunResult:
    """Run a conversation with progress and recoverable failure reporting.

    Parameters
    ----------
    config: Config
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
    from second_brain.api.status import now_iso, stop_requested, write_status

    unit_started_at = now_iso()
    current, total = progress
    write_status(
        config.data_dir,
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
    except Exception:
        # Unexpected failures still need a result so the caller can roll back
        # the current conversation before stopping the build.
        logger.exception("Compile failed for %s", ", ".join(sources))
        result = RunResult(0.0, RunOutcome.FAILED, CRASH_REASON)
    if stop_requested(config.data_dir):
        return RunResult(result.cost, RunOutcome.STOPPED)
    return result


def _compile_work_units(
    config: Config,
    work_units: list[list[str]],
    build_store: BuildStore,
    *,
    started_at: str,
    on_unit_finished: Callable[[], None] | None,
) -> dict:
    """Compile each conversation and apply its commit, deferral, or stop outcome.

    Parameters
    ----------
    config: Config
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
    from second_brain.api.status import stop_requested

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

    with _build_heartbeat(config.data_dir):
        for index, unit in enumerate(work_units):
            if stop_requested(config.data_dir):
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
                    build_store.mark_compiled(unit_hashes)
                    _git_commit(config.wiki_dir)
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
    if compiled_count == staged_count:
        from second_brain.compilation.clustering.preview import clear_preview

        purge_skipped(config.raw_dir)
        clear_preview(config.data_dir)
    return {
        "sources_compiled": compiled_count,
        **_outcome_fields(outcome, reason, compiled_count, deferred_count, left_count),
    }


def run_compilation(
    config: Config,
    force_full: bool = False,
    dry_run: bool = False,
    on_unit_finished: Callable[[], None] | None = None,
) -> dict:
    """
    Run the full compilation pipeline.

    Executes agent-driven content synthesis, deterministic
    structure rebuild, and git auto-commit.

    Parameters
    ----------
    config: Config
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
    build_store = BuildStore(config.manifest_db_path)

    from second_brain.wiki.schema import write_default_schema

    if not (wiki_dir / "_meta" / "topic_schema.yaml").exists():
        write_default_schema(wiki_dir)

    if force_full:
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
    if config.triage.enabled:
        from second_brain.ingestion.triage.pipeline import triage_pending, worthwhile_sources

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
        from second_brain.api.status import clear_stop, now_iso

        require_api_key(profile)
        clear_stop(config.data_dir)
        started_at = now_iso()
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
        from second_brain.wiki.schema import register_domains

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
    from second_brain.wiki.repo import commit_all

    commit_all(wiki_dir, "auto: compilation + structure rebuild")
