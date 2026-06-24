"""CLI interface — all second-brain commands."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

from second_brain.config import Config, load_config

if TYPE_CHECKING:
    from second_brain.ingestion.manifest import Manifest

logger = logging.getLogger(__name__)


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
    """Fail fast with a clear message when Ollama is not ready.

    Ollama hosts the local models triage and embeddings depend on; the call
    sites degrade silently without it, so a build could quietly mis-triage or
    skip clustering. This turns that into an explicit, actionable error.
    """
    from second_brain.dependencies import check_ollama

    status = check_ollama(config)
    if not status.healthy:
        raise click.ClickException(status.message())


@click.group()
@click.option("--config", "config_path", default=None, help="Path to config.yaml")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
@click.pass_context
def main(ctx: click.Context, config_path: str | None, verbose: bool) -> None:
    """Second Brain — local knowledge base pipeline."""
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["config"] = _load_config(config_path)


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
    config: Config = ctx.obj["config"]
    config.ensure_directories()

    _preflight_check()
    _require_ollama(config)

    from second_brain.ingestion.manifest import Manifest

    manifest = Manifest(config.manifest_db_path)

    if chatgpt_path:
        from second_brain.ingestion.chatgpt_parser import process_chatgpt_export

        export_path = Path(chatgpt_path).expanduser().resolve()
        output_dir = config.raw_dir / "chatgpt"
        paths = process_chatgpt_export(export_path, output_dir)
        click.echo(f"Imported {len(paths)} conversations")
        for p in paths:
            manifest.mark_processing(p, "chatgpt")
            manifest.mark_complete(p, parse_lane="passthrough", raw_output=str(p))
        return

    if file_path:
        _ingest_single(Path(file_path).expanduser().resolve(), config, manifest)
        return

    if watch:
        from second_brain.ingestion.watcher import watch_sources

        watch_sources(
            config,
            manifest,
            lambda p, s: _ingest_single(p, config, manifest, source_type=s),
        )
        return

    from second_brain.ingestion.watcher import _batch_scan

    count = _batch_scan(
        config,
        manifest,
        lambda p, s: _ingest_single(p, config, manifest, source_type=s),
        drops_only=drops_only,
    )
    click.echo(f"Processed {count} files")

    # Triage runs here (local Gemma, free) rather than at the paid compile
    # step, so every ingested source has a decision before the user builds.
    from second_brain.triage.pipeline import triage_pending

    counts = triage_pending(config, manifest)
    if sum(counts.values()):
        click.echo(
            f"Triaged: {counts['worthwhile']} worthwhile, "
            f"{counts['review']} review, {counts['skip']} skip"
        )


def _ingest_single(
    file_path: Path,
    config: Config,
    manifest: Manifest,
    source_type: str = "document",
) -> None:
    """
    Route a single file to the appropriate parser and record the result.

    Parameters
    ----------
    file_path: Path
        Absolute path to the file to ingest.
    config: Config
        Application configuration (used for output directories).
    manifest: Manifest
        Ingestion manifest to record processing status.
    source_type: str
        Source category label (e.g. ``"document"``, ``"chatgpt"``).
    """
    if not file_path.exists():
        click.echo(f"File not found: {file_path}", err=True)
        return

    source_cfg = config.sources.get(source_type)
    force_lane = source_cfg.force_parse_lane if source_cfg else None

    manifest.mark_processing(file_path, source_type)

    ingested = False
    try:
        suffix = file_path.suffix.lower()

        if suffix == ".pdf":
            from second_brain.ingestion.pdf_handler import process_pdf_sync

            rel = _relative_output_dir(file_path, config)
            output_dir = config.raw_dir / rel
            ingest = process_pdf_sync(
                file_path,
                output_dir,
                config,
                force_lane=force_lane,
                manifest=manifest,
            )
            lane_label = force_lane or "auto"
            manifest.mark_complete(
                file_path,
                parse_lane=lane_label,
                raw_output=str(ingest.md_path.relative_to(config.raw_dir)),
                content_hash=ingest.content_hash,
            )
            if ingest.cache_stats is not None and ingest.cache_stats.pages_total > 0:
                stats = ingest.cache_stats
                click.echo(
                    f"  cache: {stats.pages_from_cache}/{stats.pages_total} hits"
                    f" ({stats.pages_ocrd} OCR'd)"
                )
            if ingest.content_unchanged:
                click.echo("  content unchanged — compilation can skip this file")
            ingested = True
        elif suffix in (".md", ".txt", ".tex"):
            from second_brain.ingestion.text_handler import process_text_file

            rel = _relative_output_dir(file_path, config)
            output_dir = config.raw_dir / rel
            md_path = process_text_file(file_path, output_dir)
            manifest.mark_complete(
                file_path,
                parse_lane="passthrough",
                raw_output=str(md_path.relative_to(config.raw_dir)),
            )
            ingested = True
        elif suffix == ".json":
            from second_brain.ingestion.chatgpt_parser import process_chatgpt_export

            output_dir = config.raw_dir / "chatgpt"
            paths = process_chatgpt_export(file_path, output_dir)
            for p in paths:
                manifest.mark_complete(
                    file_path,
                    parse_lane="passthrough",
                    raw_output=str(p.relative_to(config.raw_dir)),
                )
            ingested = True
        else:
            click.echo(f"Unsupported file type: {suffix}", err=True)
            manifest.mark_failed(file_path, f"unsupported type: {suffix}")

    except Exception as e:
        manifest.mark_failed(file_path, str(e))

        from second_brain.ingestion.pdf_handler import ParserNotAvailableError

        if isinstance(e, ParserNotAvailableError):
            click.echo(f"SKIP {file_path.name}: {e}", err=True)
        else:
            logger.exception("Failed to ingest %s", file_path)
            click.echo(f"Error processing {file_path.name}: {e}", err=True)

    # drops/ is an ephemeral queue containing copies of files so we delete
    # after ingestion completes.
    if ingested:
        _remove_drop_copy(file_path, config)


def _remove_drop_copy(file_path: Path, config: Config) -> None:
    """Permanently delete a successfully-ingested file from the drops folder."""
    try:
        file_path.relative_to(config.drops_dir)
    except ValueError:
        return  # not in drops (e.g. a direct --path ingest); leave it
    try:
        file_path.unlink()
    except OSError as exc:
        logger.warning("Could not remove drop copy %s: %s", file_path, exc)


def _relative_output_dir(file_path: Path, config: Config) -> str:
    """
    Determine the output subdirectory based on which source owns the file.

    Parameters
    ----------
    file_path: Path
        Absolute path to the ingested file.
    config: Config
        Application configuration with registered sources.

    Returns
    -------
    str
        Source name if matched, otherwise ``"documents"``.
    """
    for name, source in config.sources.items():
        try:
            file_path.relative_to(source.path)
            return name
        except ValueError:
            continue
    return "documents"


@main.command()
@click.option("--full", is_flag=True, help="Force full recompilation")
@click.option("--dry-run", is_flag=True, help="Show changes without writing")
@click.pass_context
def compile(ctx: click.Context, full: bool, dry_run: bool) -> None:
    """Compile new/changed sources into the wiki."""
    config: Config = ctx.obj["config"]
    config.ensure_directories()
    _require_ollama(config)

    from second_brain.compilation.compiler import (
        MissingAPIKeyError,
        run_compilation,
    )
    from second_brain.ingestion.manifest import Manifest

    manifest = Manifest(config.manifest_db_path)
    try:
        stats = run_compilation(config, manifest, force_full=full, dry_run=dry_run)
    except MissingAPIKeyError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Sources compiled: {stats['sources_compiled']}")
    if stats.get("sources_deferred"):
        click.echo(
            f"Sources deferred (too large for {config.compilation.model}): "
            f"{stats['sources_deferred']}"
        )
    click.echo(f"Wiki pages: {stats['total_pages']}")
    click.echo(f"Total links: {stats['total_links']}")
    click.echo(f"Orphans: {stats['orphans']}")
    click.echo(f"Gaps: {stats['gaps']}")
    domains = stats.get("domains") or {}
    if domains:
        summary = ", ".join(f"{name} ({count})" for name, count in sorted(domains.items()))
        click.echo(f"Domains: {summary}")


@main.command(name="preview-clusters")
@click.pass_context
def preview_clusters(ctx: click.Context) -> None:
    """Group staged sources and write the cluster preview artifact."""
    import threading

    config: Config = ctx.obj["config"]
    config.ensure_directories()

    from second_brain.clustering.preview import write_preview
    from second_brain.ingestion.manifest import Manifest
    from second_brain.status import clear_status, now_iso, touch_status, write_status

    manifest = Manifest(config.manifest_db_path)

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
        artifact = write_preview(config, manifest, progress=_on_progress)
    finally:
        stop.set()
        heartbeat.join(timeout=2.0)
        clear_status(config.data_dir)

    click.echo(
        f"{artifact['source_count']} sources -> {artifact['group_count']} groups "
        f"(~${artifact['estimated_cost_usd']:.2f})"
    )


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
        from second_brain.ingestion.manifest import DEFERRED_DECISION, Manifest

        manifest = Manifest(config.manifest_db_path)
        counts = manifest.count_by_status()
        click.echo(f"  Manifest: {dict(counts)}")
        deferred = manifest.count_triage_decision(DEFERRED_DECISION)
        if deferred:
            click.echo(f"  Deferred (too large for {config.compilation.model}): {deferred}")
    else:
        click.echo("  Manifest: not initialized (run 'ingest' first)")

    from second_brain.scheduler.launchd import status as sched_status

    click.echo(f"  Scheduler: {sched_status()}")


@main.command()
@click.argument("raw_path")
@click.pass_context
def forget(ctx: click.Context, raw_path: str) -> None:
    """Un-ingest a source: clear its manifest, compiled, and triage records.

    RAW_PATH is relative to the raw directory (e.g. documents/notes.md). This
    clears database records only; deleting the raw file on disk is separate.
    """
    config: Config = ctx.obj["config"]
    from second_brain.ingestion.manifest import Manifest

    Manifest(config.manifest_db_path).forget_source(raw_path)
    click.echo(f"Forgot {raw_path}")


@main.command(name="forget-drop")
@click.argument("path")
@click.pass_context
def forget_drop(ctx: click.Context, path: str) -> None:
    """Remove a queued/failed dropped file's manifest row (by absolute PATH)."""
    config: Config = ctx.obj["config"]
    from second_brain.ingestion.manifest import Manifest

    removed = Manifest(config.manifest_db_path).remove_entries([Path(path)])
    click.echo(f"Removed {removed} manifest row(s)")


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
    from second_brain.ingestion.manifest import Manifest

    Manifest(config.manifest_db_path).record_triage(
        raw_path, decision, confidence=1.0, reason="manual override"
    )
    click.echo(f"Set {raw_path} -> {decision}")


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
@click.pass_context
def mcp_serve(ctx: click.Context) -> None:
    """Start the MCP server."""
    _require_ollama(ctx.obj["config"])

    from second_brain.mcp_server.server import serve

    serve()


@mcp.command(name="install")
@click.option(
    "--target",
    type=click.Choice(["claude-desktop", "cursor"]),
    required=True,
    help="Target application",
)
@click.pass_context
def mcp_install(ctx: click.Context, target: str) -> None:
    """Configure MCP server for a target application."""
    python_path = sys.executable
    module_path = "second_brain.mcp_server.server"

    mcp_config = {
        "mcpServers": {
            "second-brain": {
                "command": python_path,
                "args": ["-m", module_path],
            }
        }
    }

    if target == "claude-desktop":
        config_dir = Path.home() / "Library" / "Application Support" / "Claude"
        config_file = config_dir / "claude_desktop_config.json"
    else:
        config_dir = Path.home() / ".cursor"
        config_file = config_dir / "mcp.json"

    config_dir.mkdir(parents=True, exist_ok=True)

    existing = {}
    if config_file.exists():
        existing = json.loads(config_file.read_text())

    existing.setdefault("mcpServers", {})
    existing["mcpServers"]["second-brain"] = mcp_config["mcpServers"]["second-brain"]
    config_file.write_text(json.dumps(existing, indent=2))

    click.echo(f"Configured MCP server in {config_file}")


@main.command()
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON")
@click.pass_context
def doctor(ctx: click.Context, as_json: bool) -> None:
    """Check required local dependencies (Ollama + models).

    Exits non-zero when a dependency is missing, so callers (the app, CI) can
    gate on it.
    """
    config: Config = ctx.obj["config"]
    from second_brain.dependencies import check_ollama

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
@click.pass_context
def health(ctx: click.Context) -> None:
    """Run health checks on the wiki."""
    config: Config = ctx.obj["config"]

    from second_brain.wiki.health import run_health_check

    report = run_health_check(config.wiki_dir, config.raw_dir)
    click.echo(report.summary())

    if report.orphan_pages:
        click.echo(f"\nOrphans: {', '.join(report.orphan_pages[:10])}")
    if report.gap_links:
        click.echo(f"\nGaps: {', '.join(report.gap_links[:10])}")
    if report.oversized_pages:
        click.echo("\nOversized pages:")
        for stem, wc in report.oversized_pages:
            click.echo(f"  {stem}: {wc} words")


if __name__ == "__main__":
    main()
