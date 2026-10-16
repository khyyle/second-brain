"""CLI interface — all second-brain commands."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import click

from second_brain.config import DEFAULT_DATA_DIR, Config, ConfigError, load_config
from second_brain.errors import SecondBrainError
from second_brain.ollama import OllamaUnavailableError
from second_brain.run_record import CRASH_REASON, StageOutcome, exit_code_for, record_run
from second_brain.status import now_iso

logger = logging.getLogger(__name__)

_EMPTY_INGEST_COUNTS: dict[str, int] = {"completed": 0, "failed": 0}
_EMPTY_COMPILE_COUNTS: dict[str, int] = {"completed": 0, "deferred": 0, "left": 0}
_EMPTY_COUNTS_BY_STAGE: dict[str, dict[str, int]] = {
    "ingest": _EMPTY_INGEST_COUNTS,
    "compile": _EMPTY_COMPILE_COUNTS,
}


def _setup_logging(verbose: bool) -> None:
    """
    Configure root logger format and level.

    Parameters
    ----------
    verbose: bool
        If ``True``, set level to DEBUG; otherwise INFO.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


def _load_config(config_path: str | None) -> Config:
    """
    Load application config from an optional explicit path.

    Parameters
    ----------
    config_path: str | None
        Filesystem path to ``config.yaml``, or ``None`` for default.

    Returns
    -------
    Config
        Parsed and resolved configuration.
    """
    path = Path(config_path) if config_path else None
    return load_config(path)


def _preflight_check() -> None:
    """
    Warn about missing optional parser packages before processing.

    Checks for ``docling``, ``chandra_ocr``, and ``anthropic`` and
    prints installation instructions for any that are absent.
    """
    import importlib.util

    checks = {
        "docling": "PDF parsing (born-digital) — uv pip install docling",
        "chandra": "PDF parsing (handwritten) — uv pip install chandra-ocr",
        "anthropic": "Claude fallback parser + compilation — uv pip install anthropic",
    }
    missing = []
    for module, desc in checks.items():
        if importlib.util.find_spec(module) is None:
            missing.append(desc)

    if missing:
        click.echo("Missing optional packages:")
        for m in missing:
            click.echo(f"  - {m}")
        click.echo("PDFs requiring these parsers will be skipped.\n")


def _require_ollama(config: Config) -> None:
    """Fail fast when Ollama is not ready before a build, the paid step.

    This guards a build, not every command. A server that is down before
    the build starts is an actionable setup error.
    """
    from second_brain.ollama import check_ollama

    status = check_ollama(config)
    if not status.healthy:
        raise click.ClickException(status.message())


def _ingest_outcome(completed: int, failed: int) -> tuple[StageOutcome, str]:
    """
    Classify an ingest from how many files finished and how many failed.

    Parameters
    ----------
    completed: int
        Files ingested on this run.
    failed: int
        Files that did not ingest on this run.

    Returns
    -------
    tuple[StageOutcome, str]
        Outcome and a one-sentence reason, empty when nothing failed.
    """
    if failed == 0:
        return StageOutcome.OK, ""
    noun = "file" if failed == 1 else "files"
    reason = f"{failed} {noun} failed."
    if completed == 0:
        return StageOutcome.FAILED, reason
    return StageOutcome.PARTIAL, reason


def _exit_for_stage(
    ctx: click.Context,
    outcome: StageOutcome,
    reason: str,
    *,
    failure_message: str,
) -> None:
    """
    Exit from a finished stage using the outcome's status code.

    Parameters
    ----------
    ctx: click.Context
        Active command context.
    outcome: StageOutcome
        How the stage ended.
    reason: str
        Sentence printed for a partial or capped run.
    failure_message: str
        Message raised when the stage failed. Click turns that into exit 1.
    """
    if outcome is StageOutcome.FAILED:
        raise click.ClickException(failure_message)
    if outcome in (StageOutcome.PARTIAL, StageOutcome.CAPPED):
        click.echo(reason)
        ctx.exit(exit_code_for(outcome))


def _sync_search_index(config: Config) -> None:
    """Reconcile the search index with the wiki after an edit.

    Domain edits change page frontmatter, so the keyword index's domain column
    goes stale until reconciled. ``sync_from_wiki`` only touches the pages whose
    content actually changed.
    """
    if not config.wiki_dir.exists():
        return
    from second_brain.mcp_server.search import SearchIndex

    SearchIndex(config.search_db_path, config.search).sync_from_wiki(config.wiki_dir)


class _CliGroup(click.Group):
    """Shows an expected failure as its one-line reason instead of a traceback."""

    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except SecondBrainError as exc:
            raise click.ClickException(exc.reason) from exc


@click.group(cls=_CliGroup)
@click.option("--config", "config_path", default=None, help="Path to config.yaml")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
@click.pass_context
def main(ctx: click.Context, config_path: str | None, verbose: bool) -> None:
    """Second Brain — local knowledge base pipeline."""
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    try:
        ctx.obj["config"] = _load_config(config_path)
    except ConfigError as exc:
        _record_unstartable_run(ctx.invoked_subcommand, exc.reason)
        raise


def _record_unstartable_run(command: str | None, reason: str) -> None:
    """
    Record an ingest or build that couldn't start because the config is broken.

    Without a config the vault location is unknown, so the record goes to the
    default vault, which is the one the app reads.

    Parameters
    ----------
    command: str | None
        The subcommand that was about to run.
    reason: str
        Why the config couldn't be loaded.
    """
    if command not in _EMPTY_COUNTS_BY_STAGE:
        return
    record_run(
        DEFAULT_DATA_DIR,
        command,
        StageOutcome.FAILED,
        reason,
        _EMPTY_COUNTS_BY_STAGE[command],
        now_iso(),
    )


@main.command()
@click.option("--path", "file_path", default=None, help="Ingest a specific file")
@click.option("--chatgpt", "chatgpt_path", default=None, help="Import ChatGPT export")
@click.option("--watch", is_flag=True, help="Watch for changes continuously")
@click.option(
    "--drops-only",
    is_flag=True,
    help="Scan only the drop folders, skipping registered watched folders",
)
@click.pass_context
def ingest(
    ctx: click.Context,
    file_path: str | None,
    chatgpt_path: str | None,
    watch: bool,
    drops_only: bool,
) -> None:
    """Process pending files from watched directories."""
    from second_brain.ingestion.dispatch import ingest_file
    from second_brain.ingestion.manifest import Manifest
    from second_brain.state import emit_state
    from second_brain.triage.pipeline import TriageInterruptedError

    config: Config = ctx.obj["config"]
    started_at = now_iso()
    completed = 0
    failed = 0
    watched = False

    def record_failure(reason: str) -> None:
        # Watch mode never ends on its own, so it has no outcome to record.
        if watched:
            return
        counts = {"completed": completed, "failed": failed}
        record_run(config.data_dir, "ingest", StageOutcome.FAILED, reason, counts, started_at)

    try:
        config.ensure_directories()
        _preflight_check()

        manifest = Manifest(config.manifest_db_path)

        if chatgpt_path:
            from second_brain.ingestion.chatgpt_parser import process_chatgpt_export

            export_path = Path(chatgpt_path).expanduser().resolve()
            output_dir = config.raw_dir / "chatgpt"
            paths = process_chatgpt_export(export_path, output_dir)
            click.echo(f"Imported {len(paths)} conversations")
            for conversation in paths:
                manifest.mark_processing(conversation, "chatgpt")
                manifest.mark_complete(
                    conversation, parse_lane="passthrough", raw_output=str(conversation)
                )
            completed = len(paths)
            emit_state(config)
        elif file_path:
            ingested = ingest_file(Path(file_path).expanduser().resolve(), config, manifest)
            if ingested:
                completed = 1
            else:
                failed = 1
            emit_state(config)
        elif watch:
            from second_brain.ingestion.watcher import watch_sources

            watched = True
            watch_sources(
                config,
                manifest,
                lambda path, source_type: ingest_file(
                    path, config, manifest, source_type=source_type
                ),
            )
        else:
            from second_brain.ingestion.watcher import batch_scan

            def _process(path: Path, source_type: str) -> None:
                nonlocal completed, failed
                if ingest_file(path, config, manifest, source_type=source_type):
                    completed += 1
                else:
                    failed += 1

            count = batch_scan(config, manifest, _process, drops_only=drops_only)
            click.echo(f"Processed {count} files")

            # Triage runs here (local Gemma, free) rather than at the paid compile
            # step, so every ingested source has a decision before the user build_store.
            from second_brain.triage.pipeline import triage_pending

            triaged = triage_pending(config)
            if sum(triaged.values()):
                click.echo(
                    f"Triaged: {triaged['worthwhile']} worthwhile, "
                    f"{triaged['review']} review, {triaged['skip']} skip"
                )
            emit_state(config)
    except SecondBrainError as exc:
        if isinstance(exc, TriageInterruptedError):
            # Files parsed before the sort stopped still changed what is staged.
            emit_state(config)
        record_failure(exc.reason)
        raise
    except Exception:
        record_failure(CRASH_REASON)
        raise

    if watched:
        return

    outcome, reason = _ingest_outcome(completed, failed)
    record_run(
        config.data_dir,
        "ingest",
        outcome,
        reason,
        {"completed": completed, "failed": failed},
        started_at,
    )
    _exit_for_stage(ctx, outcome, reason, failure_message=reason)


@main.command()
@click.option("--full", is_flag=True, help="Force full recompilation")
@click.option("--dry-run", is_flag=True, help="Show changes without writing")
@click.pass_context
def compile(ctx: click.Context, full: bool, dry_run: bool) -> None:
    """Compile new/changed sources into the wiki."""
    from second_brain.compilation.compiler import OLLAMA_BUILD_STOPPED_REASON, run_compilation
    from second_brain.state import emit_state

    config: Config = ctx.obj["config"]
    started_at = now_iso()

    def record_failure(reason: str) -> None:
        record_run(
            config.data_dir,
            "compile",
            StageOutcome.FAILED,
            reason,
            _EMPTY_COMPILE_COUNTS,
            started_at,
        )

    try:
        config.ensure_directories()
        _require_ollama(config)

        stats = run_compilation(config, force_full=full, dry_run=dry_run)
        click.echo(f"Sources compiled: {stats['sources_compiled']}")
        click.echo(f"Wiki pages: {stats['total_pages']}")
        click.echo(f"Total links: {stats['total_links']}")
        click.echo(f"Orphans: {stats['orphans']}")
        click.echo(f"Gaps: {stats['gaps']}")
        domains = stats.get("domains") or {}
        if domains:
            summary = ", ".join(f"{name} ({count})" for name, count in sorted(domains.items()))
            click.echo(f"Domains: {summary}")
        emit_state(config)
    except SecondBrainError as exc:
        # The low-level Ollama message doesn't say the build stopped or what to do.
        reason = (
            OLLAMA_BUILD_STOPPED_REASON if isinstance(exc, OllamaUnavailableError) else exc.reason
        )
        record_failure(reason)
        raise click.ClickException(reason) from exc
    except click.ClickException as exc:
        record_failure(str(exc))
        raise
    except Exception:
        record_failure(CRASH_REASON)
        raise

    if not dry_run:
        record_run(
            config.data_dir,
            "compile",
            stats["outcome"],
            stats["reason"],
            stats["counts"],
            started_at,
        )
    _exit_for_stage(
        ctx,
        stats["outcome"],
        stats["reason"],
        failure_message=f"Build stopped: {stats['reason']}",
    )


@main.command(name="recompile")
@click.argument("raw_paths", nargs=-1, required=True)
@click.pass_context
def recompile(ctx: click.Context, raw_paths: tuple[str, ...]) -> None:
    """Requeue RAW_PATHS for the next build, releasing set-aside sources.

    Clears a source's compiled mark and any deferral, so the next build
    redoes it. Paths are relative to the raw directory.
    """
    from second_brain.compilation.store import BuildStore

    config: Config = ctx.obj["config"]
    build_store = BuildStore(config.manifest_db_path)

    for raw_path in raw_paths:
        if not (config.raw_dir / raw_path).exists():
            click.echo(f"Warning: {raw_path} not found under raw/", err=True)

    released = build_store.clear_deferred(list(raw_paths))
    uncompiled = build_store.unmark_compiled(list(raw_paths))

    from second_brain.state import emit_state

    emit_state(config)
    click.echo(
        f"Requeued {len(raw_paths)} source(s) for the next build "
        f"({released} released from set-aside, {uncompiled} previously compiled)"
    )


@main.command(name="preview-clusters")
@click.pass_context
def preview_clusters(ctx: click.Context) -> None:
    """Group staged sources and write the cluster preview artifact."""
    import threading

    config: Config = ctx.obj["config"]
    config.ensure_directories()

    from second_brain.clustering.preview import write_preview
    from second_brain.status import clear_status, now_iso, touch_status, write_status

    # Embedding the staged set can take minutes; report per-source progress
    # (with a keepalive between, for the occasional slow multi-chunk source)
    # so the menu bar shows a live "Grouping i/n" instead of going stale.
    started = now_iso()

    def _on_progress(index: int, total: int) -> None:
        write_status(
            config.data_dir, phase="cluster", current=index, total=total, started_at=started
        )

    write_status(config.data_dir, phase="cluster", current=0, total=0, started_at=started)
    stop = threading.Event()

    def _keepalive() -> None:
        while not stop.wait(5.0):
            touch_status(config.data_dir)

    heartbeat = threading.Thread(target=_keepalive, daemon=True)
    heartbeat.start()
    try:
        artifact = write_preview(config, progress=_on_progress)
    except OllamaUnavailableError as exc:
        raise click.ClickException(
            "Couldn't group chats because Ollama isn't responding. Your grouping wasn't changed."
        ) from exc
    finally:
        stop.set()
        heartbeat.join(timeout=2.0)
        clear_status(config.data_dir)

    click.echo(
        f"{artifact['source_count']} sources -> {artifact['group_count']} groups "
        f"(~${artifact['estimated_cost_usd']:.2f})"
    )

    from second_brain.state import emit_state

    emit_state(config)


@main.command()
@click.argument("question")
@click.pass_context
def query(ctx: click.Context, question: str) -> None:
    """Search the wiki for information."""
    config: Config = ctx.obj["config"]

    from second_brain.mcp_server.search import SearchIndex

    index = SearchIndex(config.search_db_path)
    if config.wiki_dir.exists():
        index.rebuild_from_wiki(config.wiki_dir)

    hits = index.search(question)
    if not hits:
        click.echo("No results found.")
        return

    for h in hits:
        click.echo(f"\n{'=' * 60}")
        click.echo(f"{h.title} ({h.content_type})")
        click.echo(f"Domains: {', '.join(h.domains)}")
        click.echo(f"{h.snippet}")


@main.command()
@click.option("--sources", is_flag=True, help="Show registered sources")
@click.pass_context
def status(ctx: click.Context, sources: bool) -> None:
    """Show pipeline health and status."""
    config: Config = ctx.obj["config"]

    if sources:
        for name, src in config.sources.items():
            exists = src.path.exists()
            status_str = "OK" if exists else "MISSING"
            click.echo(f"  {name}: {src.path} [{status_str}]")
        return

    click.echo("Pipeline Status")
    click.echo(f"  Data dir: {config.data_dir}")
    click.echo(f"  Wiki dir: {config.wiki_dir}")

    if config.manifest_db_path.parent.exists():
        from second_brain.ingestion.manifest import Manifest

        manifest = Manifest(config.manifest_db_path)
        counts = manifest.count_by_status()
        click.echo(f"  Manifest: {dict(counts)}")
    else:
        click.echo("  Manifest: not initialized (run 'ingest' first)")

    from second_brain.scheduler.launchd import status as sched_status

    click.echo(f"  Scheduler: {sched_status()}")


@main.command()
@click.argument("raw_path")
@click.pass_context
def forget(ctx: click.Context, raw_path: str) -> None:
    """Un-ingest a source: clear its ingestion, triage, and build records.

    RAW_PATH is relative to the raw directory (e.g. documents/notes.md). This
    clears database records only; deleting the raw file on disk is separate.
    """
    config: Config = ctx.obj["config"]
    from second_brain.sources import forget_source

    forget_source(config, raw_path)
    click.echo(f"Forgot {raw_path}")
    from second_brain.state import emit_state

    emit_state(config)


@main.command(name="forget-drop")
@click.argument("path")
@click.pass_context
def forget_drop(ctx: click.Context, path: str) -> None:
    """Remove a queued/failed dropped file's manifest row (by absolute PATH)."""
    config: Config = ctx.obj["config"]
    from second_brain.ingestion.manifest import Manifest

    removed = Manifest(config.manifest_db_path).remove_entries([Path(path)])
    click.echo(f"Removed {removed} manifest row(s)")
    from second_brain.state import emit_state

    emit_state(config)


@main.command(name="state")
@click.pass_context
def state(ctx: click.Context) -> None:
    """Recompute the derived state file the menu bar app reads."""
    config: Config = ctx.obj["config"]
    from second_brain.state import emit_state

    emit_state(config)


@main.group()
def triage() -> None:
    """Manage triage decisions."""
    pass


@triage.command(name="set")
@click.argument("raw_path")
@click.argument("decision", type=click.Choice(["worthwhile", "review", "skip"]))
@click.pass_context
def triage_set(ctx: click.Context, raw_path: str, decision: str) -> None:
    """Override the triage DECISION for RAW_PATH (a manual Keep/Skip)."""
    config: Config = ctx.obj["config"]
    from second_brain.triage.store import TriageStore

    TriageStore(config.manifest_db_path).record(
        raw_path, decision, confidence=1.0, reason="manual override"
    )
    click.echo(f"Set {raw_path} -> {decision}")
    from second_brain.state import emit_state

    emit_state(config)


@main.group()
def domain() -> None:
    """View and edit the wiki's domain vocabulary."""
    pass


@domain.command(name="list")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON")
@click.pass_context
def domain_list(ctx: click.Context, as_json: bool) -> None:
    """List domains with their page counts."""
    config: Config = ctx.obj["config"]
    from second_brain.wiki.domain_ops import list_domains

    domains = list_domains(config.wiki_dir)
    if as_json:
        payload = [
            {"name": d.name, "count": d.page_count, "in_schema": d.in_schema} for d in domains
        ]
        click.echo(json.dumps(payload))
        return
    if not domains:
        click.echo("No domains yet.")
        return
    for d in domains:
        click.echo(f"  {d.name} ({d.page_count})")


@domain.command(name="rename")
@click.argument("old")
@click.argument("new")
@click.pass_context
def domain_rename(ctx: click.Context, old: str, new: str) -> None:
    """Rename domain OLD to NEW across every page and the schema."""
    config: Config = ctx.obj["config"]
    from second_brain.wiki.domain_ops import rename_domain

    changed = rename_domain(config.wiki_dir, old, new)
    _sync_search_index(config)
    click.echo(f"Renamed '{old}' to '{new}' across {changed} page(s)")


@domain.command(name="merge")
@click.argument("dest")
@click.argument("sources", nargs=-1, required=True)
@click.pass_context
def domain_merge(ctx: click.Context, dest: str, sources: tuple[str, ...]) -> None:
    """Merge one or more SOURCES domains into DEST."""
    config: Config = ctx.obj["config"]
    from second_brain.wiki.domain_ops import merge_domains

    changed = merge_domains(config.wiki_dir, list(sources), dest)
    _sync_search_index(config)
    click.echo(f"Merged {', '.join(sources)} into '{dest}' across {changed} page(s)")


@domain.command(name="delete")
@click.argument("name")
@click.pass_context
def domain_delete(ctx: click.Context, name: str) -> None:
    """Remove domain NAME from every page and the schema."""
    config: Config = ctx.obj["config"]
    from second_brain.wiki.domain_ops import delete_domain

    changed = delete_domain(config.wiki_dir, name)
    _sync_search_index(config)
    click.echo(f"Deleted '{name}' from {changed} page(s)")


@main.group()
def wiki() -> None:
    """Inspect and repair the compiled wiki repository."""
    pass


@wiki.command(name="log")
@click.option("-n", "--limit", default=10, show_default=True, help="Commits to show")
@click.pass_context
def wiki_log(ctx: click.Context, limit: int) -> None:
    """List the wiki's most recent build commits."""
    config: Config = ctx.obj["config"]
    from second_brain.wiki.repo import count_commits, list_commits

    commits = list_commits(config.wiki_dir, limit)
    total = count_commits(config.wiki_dir)
    if not commits:
        click.echo("No wiki commits yet.")
        return
    for commit in commits:
        click.echo(
            f"{commit.short_hash}  {commit.date}  "
            f"{commit.files_changed:>4} file(s)  {commit.subject}"
        )
    if len(commits) < total:
        click.echo(f"Showing {len(commits)} of {total} commits. Pass -n <count> to see more.")


@wiki.command(name="rollback")
@click.argument("count", type=click.IntRange(min=1), default=1)
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt")
@click.pass_context
def wiki_rollback(ctx: click.Context, count: int, yes: bool) -> None:
    """Undo the last COUNT wiki build commits and requeue their sources."""
    config: Config = ctx.obj["config"]
    from second_brain.wiki.repo import rollback
    from second_brain.wiki.structure import rebuild_structure

    if not yes:
        click.confirm(
            f"Roll the wiki back {count} commit(s)? The affected pages are "
            "discarded and their sources recompile on the next build.",
            abort=True,
        )
    sources = rollback(config.wiki_dir, count)

    from second_brain.compilation.store import BuildStore

    # the wiki records sources as raw/<path>; build records key on <path>
    build_store = BuildStore(config.manifest_db_path)
    requeued = build_store.unmark_compiled([s.removeprefix("raw/") for s in sources])

    rebuild_structure(config.wiki_dir)
    _sync_search_index(config)
    from second_brain.state import emit_state

    emit_state(config)
    click.echo(f"Rolled back {count} commit(s); {requeued} source(s) requeued for the next build")


@wiki.command(name="repair-links")
@click.option("--dry-run", is_flag=True, help="Show what would change without writing")
@click.pass_context
def wiki_repair_links(ctx: click.Context, dry_run: bool) -> None:
    """Repoint links to pages that were renamed outside the pipeline."""
    config: Config = ctx.obj["config"]
    from second_brain.wiki.repo import detect_renames, repair_links
    from second_brain.wiki.structure import rebuild_structure

    mapping = detect_renames(config.wiki_dir)
    repairs = repair_links(config.wiki_dir, mapping, dry_run=dry_run)

    if not mapping:
        click.echo("No renamed pages detected.")
        return
    click.echo("Detected renames:")
    for old, new in sorted(mapping.items()):
        click.echo(f"  {old} -> {new}")

    verb = "Would repoint" if dry_run else "Repointed"
    total = sum(repair.links_repointed for repair in repairs)
    click.echo(f"{verb} {total} link(s) across {len(repairs)} page(s)")
    for repair in repairs:
        click.echo(f"  {repair.rel_path}: {repair.links_repointed}")

    if not dry_run and repairs:
        rebuild_structure(config.wiki_dir)
        _sync_search_index(config)


@wiki.command(name="merge")
@click.argument("dest")
@click.argument("sources", nargs=-1, required=True)
@click.pass_context
def wiki_merge(ctx: click.Context, dest: str, sources: tuple[str, ...]) -> None:
    """Retire duplicate SOURCES pages into DEST, repointing every link.

    Move any prose worth keeping into DEST first; the merge carries over the
    retired pages' provenance and relationships, not their body text.
    """
    config: Config = ctx.obj["config"]
    from second_brain.wiki.repo import merge_pages

    repairs = merge_pages(config.wiki_dir, dest, list(sources))

    _sync_search_index(config)
    from second_brain.state import emit_state

    emit_state(config)
    total = sum(repair.links_repointed for repair in repairs)
    click.echo(
        f"Merged {', '.join(sources)} into '{dest}'; "
        f"repointed {total} link(s) across {len(repairs)} page(s)"
    )


@wiki.command(name="dismiss")
@click.argument("page_a")
@click.argument("page_b")
@click.pass_context
def wiki_dismiss(ctx: click.Context, page_a: str, page_b: str) -> None:
    """Mark PAGE_A and PAGE_B as not duplicates, hiding the suggestion."""
    config: Config = ctx.obj["config"]
    from second_brain.wiki.slugs import normalize_link_target
    from second_brain.wiki.store import DuplicateStore

    stem_a = normalize_link_target(page_a)
    stem_b = normalize_link_target(page_b)
    DuplicateStore(config.manifest_db_path).dismiss(stem_a, stem_b)
    click.echo(f"Dismissed duplicate suggestion: {stem_a} + {stem_b}")


@main.group()
def schedule() -> None:
    """Manage the launchd scheduler."""
    pass


@schedule.command(name="install")
@click.pass_context
def schedule_install(ctx: click.Context) -> None:
    """Install the launchd plist."""
    config: Config = ctx.obj["config"]
    from second_brain.scheduler.launchd import install

    project_dir = Path(__file__).resolve().parent.parent.parent
    result = install(config, project_dir)
    click.echo(result)


@schedule.command(name="uninstall")
def schedule_uninstall() -> None:
    """Remove the launchd plist."""
    from second_brain.scheduler.launchd import uninstall

    click.echo(uninstall())


@schedule.command(name="status")
def schedule_status() -> None:
    """Check scheduler status."""
    from second_brain.scheduler.launchd import status

    click.echo(status())


@main.group()
def mcp() -> None:
    """MCP server management."""
    pass


@mcp.command(name="serve")
def mcp_serve() -> None:
    """Start the MCP server."""
    from second_brain.mcp_server.server import serve

    serve()


@mcp.command(name="install")
@click.option(
    "--target",
    type=click.Choice(["claude-desktop", "chatgpt-desktop", "cursor"]),
    required=True,
    help="Target application",
)
def mcp_install(target: str) -> None:
    """Configure MCP server for a target application."""
    from second_brain.mcp_server.install import install_mcp_client

    client = install_mcp_client(target)

    click.echo(
        f"Configured MCP server for {client.display_name} in {client.config_file}. "
        f"Restart {client.display_name} to connect."
    )


@main.command()
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON")
@click.pass_context
def doctor(ctx: click.Context, as_json: bool) -> None:
    """Check required local dependencies (Ollama + models).

    Exits non-zero when a dependency is missing, so callers (the app, CI) can
    gate on it.
    """
    config: Config = ctx.obj["config"]
    from second_brain.ollama import check_ollama

    status = check_ollama(config)

    if as_json:
        click.echo(
            json.dumps(
                {
                    "healthy": status.healthy,
                    "reachable": status.reachable,
                    "host": status.host,
                    "required_models": list(status.required_models),
                    "missing_models": list(status.missing_models),
                    "message": status.message(),
                }
            )
        )
    else:
        marker = "OK" if status.healthy else "FAIL"
        click.echo(f"[{marker}] {status.message()}")

    if not status.healthy:
        ctx.exit(1)


@main.command()
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON")
@click.pass_context
def health(ctx: click.Context, as_json: bool) -> None:
    """Run health checks on the wiki."""
    config: Config = ctx.obj["config"]

    from second_brain.mcp_server.search import SearchIndex
    from second_brain.wiki.health import MERGE_THRESHOLD, SPLIT_THRESHOLD, run_health_check
    from second_brain.wiki.store import DuplicateStore

    search = SearchIndex(config.search_db_path, config.search)
    dismissed = DuplicateStore(config.manifest_db_path).get_dismissed()
    report = run_health_check(
        config.wiki_dir,
        duplicate_pairs=search.near_duplicate_pairs(exclude=dismissed),
    )

    if as_json:
        # Each item carries a display string, the page stem to open (null when
        # not page-backed, as a gap points at a page that does not exist), and an
        # optional detail shown beside it. Categories are grouped into two
        # sections: "improve" (growth opportunities) and "health" (defects).
        categories = [
            (
                "gap_links",
                "Referenced but not written",
                "improve",
                [
                    {"text": stem, "page": None, "detail": f"{refs} ref{'' if refs == 1 else 's'}"}
                    for stem, refs in report.gap_links
                ],
            ),
            (
                "orphan_pages",
                "Not linked from any page",
                "improve",
                [{"text": s, "page": s} for s in report.orphan_pages],
            ),
            (
                "possible_duplicates",
                "Possible duplicates",
                "improve",
                [
                    {"text": a, "page": a, "pair": b, "detail": f"{similarity:.0%} similar"}
                    for a, b, similarity in report.possible_duplicates
                ],
            ),
            (
                "oversized_pages",
                f"Over {SPLIT_THRESHOLD:,} words",
                "health",
                [{"text": f"{s} ({w:,} words)", "page": s} for s, w in report.oversized_pages],
            ),
            (
                "undersized_pages",
                f"Under {MERGE_THRESHOLD:,} words",
                "health",
                [{"text": f"{s} ({w:,} words)", "page": s} for s, w in report.undersized_pages],
            ),
            (
                "missing_frontmatter",
                "Missing a title, type, or domain",
                "health",
                [{"text": m, "page": m.split(":", 1)[0]} for m in report.missing_frontmatter],
            ),
        ]
        click.echo(
            json.dumps(
                {
                    "healthy": report.is_healthy,
                    "categories": [
                        {"key": key, "label": label, "section": section, "items": items}
                        for key, label, section, items in categories
                    ],
                }
            )
        )
        return

    click.echo(report.summary())

    if report.gap_links:
        top_gaps = ", ".join(stem for stem, _ in report.gap_links[:10])
        click.echo(f"\nReferenced but not written: {top_gaps}")
    if report.orphan_pages:
        click.echo(f"\nNot linked from any page: {', '.join(report.orphan_pages[:10])}")
    if report.oversized_pages:
        click.echo(f"\nOver {SPLIT_THRESHOLD:,} words:")
        for stem, wc in report.oversized_pages:
            click.echo(f"  {stem}: {wc} words")


if __name__ == "__main__":
    main()
