"""Compilation orchestrator — agentic content synthesis then deterministic rebuild."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

import anthropic

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
from second_brain.ingestion.manifest import DEFERRED_DECISION, Manifest
from second_brain.llm_providers import ProviderProfile, resolve_profile
from second_brain.wiki.structure import rebuild_structure

logger = logging.getLogger(__name__)


class MissingAPIKeyError(RuntimeError):
    """
    Raised when a build is attempted without the configured provider's API key.
    """


# Per-turn output cap for one agent Messages API call.
# NOTE: provider limits are Opus/Sonnet 128K, Haiku 64K, DeepSeek V4 ~384K.
_MAX_OUTPUT_TOKENS_PER_TURN = 16384

# Holding folder under raw/ for skipped sources: kept out of the build
# but recoverable until a build finalizes the curation.
SKIPPED_DIRNAME = ".skipped"


def _purge_skipped(raw_dir: Path) -> None:
    """Permanently remove the skipped-source holding folder.

    Skip moves a source into ``raw/.skipped/`` so it can be un-skipped; a
    completed build finalizes those decisions, so the folder is cleared to
    reclaim disk. The skip verdicts stay in the manifest, so a later
    re-import of the same export is still recognized as skipped.

    Parameters
    ----------
    raw_dir: Path
        The raw output directory containing the holding folder.
    """
    skipped_dir = raw_dir / SKIPPED_DIRNAME
    if not skipped_dir.exists():
        return
    try:
        shutil.rmtree(skipped_dir)
    except OSError as e:
        logger.warning("Could not purge skipped folder: %s", e)


def _split_oversized(clusters: list[list[str]], max_size: int) -> list[list[str]]:
    """Split clusters larger than ``max_size`` into bounded batches.

    A single agent run has a bounded token budget, so any cluster larger
    than the cap is divided into batches that each fit within one run.

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

    A reviewed cluster preview that still covers exactly the staged set is
    honored as-is, so the build matches what the user saw and tuned —
    regardless of the ``clustering.enabled`` flag, since producing a preview
    is itself the intent to cluster. With no matching preview, sources are
    clustered fresh only when clustering is enabled (the unattended path);
    otherwise each source compiles on its own. Oversized groups are split to
    stay within the per-run token budget.

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
    from second_brain.clustering.preview import (
        load_preview,
        preview_members,
        work_units_from_preview,
    )

    preview = load_preview(config.data_dir)
    if preview is not None and preview_members(preview) == set(new_sources):
        clusters = work_units_from_preview(config.data_dir) or [[s] for s in new_sources]
        logger.info("Using reviewed cluster preview (%d groups)", len(clusters))
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


def _source_tokens(rel_path: str, raw_dir: Path) -> int:
    """Estimate a source's token count from its byte size (~4 chars/token)."""
    path = raw_dir / rel_path
    return (path.stat().st_size // 4) if path.exists() else 0


def _defer_oversized(
    config: Config,
    manifest: Manifest,
    profile: ProviderProfile,
    sources: list[str],
    raw_dir: Path,
    dry_run: bool,
) -> tuple[list[str], int]:
    """Split sources into those that fit the model's window and those too large.

    A source larger than ``context_window_tokens * window_reserve`` cannot be
    compiled faithfully by the current model, so it is recorded as deferred and
    held out of the build (rather than half-compiled or lossily summarized).
    A source previously deferred that now fits — e.g. after switching to a
    larger-window model — is restored, so the verdict self-heals. The verdict
    is recomputed every run; in a dry run the partition is returned without
    recording anything.

    Returns
    -------
    tuple[list[str], int]
        The sources that fit, and the count deferred as too large.
    """
    window_cap = int(profile.context_window_tokens * config.compilation.window_reserve)
    decisions = manifest.get_triage_decisions()
    fitting: list[str] = []
    deferred = 0
    for rel in sources:
        tokens = _source_tokens(rel, raw_dir)
        if tokens > window_cap:
            deferred += 1
            logger.warning(
                "Deferring %s: ~%d tokens exceeds the usable window for %s (%d). "
                "Switch to a larger-window model to compile it.",
                rel,
                tokens,
                profile.model,
                window_cap,
            )
            if not dry_run:
                manifest.record_triage(rel, DEFERRED_DECISION, 1.0, f"oversized:{window_cap}")
        else:
            fitting.append(rel)
            if not dry_run and decisions.get(rel) == DEFERRED_DECISION:
                manifest.record_triage(rel, "worthwhile", 1.0, "fits-window")
    return fitting, deferred


def _find_new_sources(config: Config, manifest: Manifest) -> list[str]:
    """
    Find raw source files that haven't been compiled yet.

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
    new_sources: list[str] = []

    for md_file in raw_dir.rglob("*.md"):
        relative = md_file.relative_to(raw_dir)
        if any(part.startswith(".") for part in relative.parts):
            continue  # skip the .skipped/ holding folder and other dotfiles
        rel = str(relative)
        if rel not in compiled:
            new_sources.append(rel)

    return sorted(new_sources)


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
        Summary statistics including ``sources_compiled`` and
        all keys from ``rebuild_structure``.
    """
    wiki_dir = config.wiki_dir
    raw_dir = config.raw_dir

    from second_brain.wiki.schema import write_default_schema

    if not (wiki_dir / "_meta" / "topic_schema.yaml").exists():
        write_default_schema(wiki_dir)

    if force_full:
        new_sources = sorted(str(f.relative_to(raw_dir)) for f in raw_dir.rglob("*.md"))
    else:
        new_sources = _find_new_sources(config, manifest)

    if not new_sources:
        logger.info("No new sources to compile")
        stats = rebuild_structure(wiki_dir)
        return {**stats, "sources_compiled": 0, "sources_deferred": 0}

    # Triage already ran during ingestion (free, local). Here we just
    # filter to the worthwhile set from the recorded decisions; any
    # untriaged file (e.g. triage was disabled) passes through.
    if config.triage.enabled:
        from second_brain.triage.pipeline import triage_pending, worthwhile_sources

        # Catch anything ingested before triage existed.
        triage_pending(config, manifest)
        new_sources = worthwhile_sources(manifest, new_sources)

    # Hold out sources too large for the current model (and restore any that
    # now fit). Recomputed every run, so the verdict self-heals on model change.
    profile = resolve_profile(config.compilation.provider, config.compilation.model)
    new_sources, deferred_count = _defer_oversized(
        config, manifest, profile, new_sources, raw_dir, dry_run=dry_run
    )

    if not new_sources:
        if deferred_count:
            logger.info("Nothing to compile — %d source(s) deferred as too large", deferred_count)
        else:
            logger.info("Nothing worthwhile to compile")
        stats = rebuild_structure(wiki_dir)
        return {**stats, "sources_compiled": 0, "sources_deferred": deferred_count}

    logger.info("Compiling %d worthwhile sources (%d deferred)", len(new_sources), deferred_count)

    compiled_count = 0
    if not dry_run:
        # Fail fast on a missing key, before any heartbeat or git work.
        if not os.environ.get(profile.api_key_env):
            raise MissingAPIKeyError(
                f"{profile.api_key_env} is not set. Add your {profile.name} API key "
                "in the app's Settings, or to a .env file at the repository root, "
                "then build again."
            )

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
        was_stopped = False
        try:
            for index, unit in enumerate(work_units):
                if stop_requested(config.data_dir):
                    logger.info("Stop requested — halting before group %d/%d", index + 1, total)
                    was_stopped = True
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
                    break
                write_status(
                    config.data_dir,
                    phase="compile",
                    current=index,
                    total=total,
                    started_at=started,
                    cost_usd=cumulative_cost,
                )
                try:
                    cost = _run_agent(
                        config,
                        wiki_dir,
                        raw_dir,
                        unit,
                        started_at=started,
                        base_cost=cumulative_cost,
                        progress=(index, total),
                    )
                except Exception:
                    # A transient failure (rate limit, network, an API call
                    # killed by the machine sleeping) shouldn't abort the batch.
                    # Discard the group's partial, uncommitted pages so an
                    # interrupted build leaves nothing dangling, and leave it
                    # uncompiled for the next build to redo cleanly.
                    logger.exception("Compile failed for %s — rolling back", ", ".join(unit))
                    _git_restore(wiki_dir)
                    continue
                if stop_requested(config.data_dir):
                    # Stopped mid-group: discard its partial, uncommitted
                    # pages and leave it uncompiled so the next build redoes
                    # it cleanly. Earlier groups are already committed.
                    _git_restore(wiki_dir)
                    logger.info("Stopped during %s — rolled back partial work", ", ".join(unit))
                    was_stopped = True
                    break
                cumulative_cost += cost
                manifest.mark_compiled(unit)
                _git_commit(wiki_dir)  # commit each completed group
                compiled_count += len(unit)
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=2.0)
            clear_stop(config.data_dir)
            clear_status(config.data_dir)
        logger.info("Compiled %d sources for ~$%.2f", compiled_count, cumulative_cost)

        # A finished build finalizes the user's curation, so reclaim the
        # skipped-source holding folder and consume the cluster preview. A
        # user-stopped build leaves them, since the decisions aren't final.
        if not was_stopped:
            _purge_skipped(raw_dir)
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

    return {**stats, "sources_compiled": compiled_count, "sources_deferred": deferred_count}


def _run_agent(
    config: Config,
    wiki_dir: Path,
    raw_dir: Path,
    sources: list[str],
    started_at: str,
    base_cost: float = 0.0,
    progress: tuple[int, int] | None = None,
) -> float:
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
        heartbeat shows a cumulative figure.
    progress: tuple[int, int] | None
        ``(index, total)`` of this file within the Build, for the i/n
        readout.

    Returns
    -------
    float
        The USD cost of this single agent run.
    """
    from second_brain.status import stop_requested, write_status

    profile = resolve_profile(config.compilation.provider, config.compilation.model)
    client = anthropic.Anthropic(**profile.client_kwargs())

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

    max_iterations = config.compilation.max_iterations
    token_budget = config.compilation.token_budget_per_run
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_read_tokens = 0
    total_cache_write_tokens = 0
    cur, tot = progress if progress else (0, 0)

    for iteration in range(max_iterations):
        # Honor a cancel between turns (the costly call is below), so a
        # stop lands within one agent round-trip.
        if stop_requested(config.data_dir):
            logger.info("Stop requested — halting agent after %d iterations", iteration)
            break

        # Shrink stale tool outputs before re-sending the history, so a
        # large early file read isn't billed on every later turn.
        compact_history(messages)

        response = client.messages.create(
            model=profile.model,
            max_tokens=_MAX_OUTPUT_TOKENS_PER_TURN,
            system=system,
            tools=tools,
            messages=messages,
        )

        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens
        total_cache_read_tokens += getattr(response.usage, "cache_read_input_tokens", 0) or 0
        total_cache_write_tokens += getattr(response.usage, "cache_creation_input_tokens", 0) or 0
        # The budget is a runaway-loop guard on newly processed tokens.
        # ``input_tokens`` already excludes cache reads, so a large cached
        # source re-sent each turn does not inflate it.
        uncached_total = total_input_tokens + total_output_tokens
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
            "Iteration %d: +%d in (+%d cached), +%d out (cumulative %d / budget %d)",
            iteration + 1,
            response.usage.input_tokens,
            getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            response.usage.output_tokens,
            uncached_total,
            token_budget,
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            logger.info("Agent completed after %d iterations", iteration + 1)
            break

        # Stop before the next turn once over budget. Checked after appending
        # the assistant turn so work already done stays on disk.
        if uncached_total >= token_budget:
            logger.warning(
                "Token budget exceeded (%d >= %d) after %d iterations — stopping early",
                uncached_total,
                token_budget,
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
        "Agent finished: %d changes, %d in + %d out tokens (%d cache read), ~$%.2f",
        len(executor.changes),
        total_input_tokens,
        total_output_tokens,
        total_cache_read_tokens,
        cost,
    )
    return cost


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
    Auto-commit wiki changes if the directory is a git repo.

    Initializes a repository if none exists, then stages and
    commits all changes.

    Parameters
    ----------
    wiki_dir: Path
        Root directory of the wiki.
    """
    git_dir = wiki_dir / ".git"
    if not git_dir.exists():
        try:
            subprocess.run(
                ["git", "init"],
                cwd=wiki_dir,
                capture_output=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.warning("Could not initialize git in wiki directory")
            return

    try:
        subprocess.run(
            ["git", "add", "-A"],
            cwd=wiki_dir,
            capture_output=True,
            check=True,
        )
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=wiki_dir,
            capture_output=True,
            text=True,
        )
        if not result.stdout.strip():
            logger.info("No wiki changes to commit")
            return

        subprocess.run(
            ["git", "commit", "-m", "auto: compilation + structure rebuild"],
            cwd=wiki_dir,
            capture_output=True,
            check=True,
        )
        logger.info("Committed wiki changes")
    except subprocess.CalledProcessError as e:
        logger.warning("Git commit failed: %s", e.stderr)
