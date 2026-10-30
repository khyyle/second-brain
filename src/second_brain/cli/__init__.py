"""Terminal commands for the local backend."""

import json
from pathlib import Path

import click

from second_brain.backend_client import BackendClient

from .common import json_output, print_result, print_submission, request
from .jobs import jobs, jobs_list
from .wiki import domain, health, query, wiki


@click.group()
@click.option("--vault", type=click.Path(path_type=Path), envvar="SECOND_BRAIN_VAULT")
@click.option("--port", type=click.IntRange(1, 65535), envvar="SECOND_BRAIN_PORT")
@click.pass_context
def main(context: click.Context, vault: Path | None, port: int | None) -> None:
    """Use the local backend. Start it with `make backend` before running commands."""
    context.obj = BackendClient(vault, port)


@main.command("api")
@click.argument(
    "method", type=click.Choice(["GET", "POST", "PATCH", "PUT", "DELETE"], case_sensitive=False)
)
@click.argument("path")
@click.option("--json", "body", help="JSON request body.")
@click.pass_context
def api(context: click.Context, method: str, path: str, body: str | None) -> None:
    """Make a raw /v1 request for debugging, for example: api GET /catalog."""
    try:
        payload = json.loads(body) if body else None
    except ValueError as exc:
        raise click.ClickException("The request body is not valid JSON.") from exc
    result = request(context, method.upper(), path, payload)
    if result is not None:
        click.echo(json.dumps(result, indent=2))


@main.command()
@click.option(
    "--path", type=click.Path(path_type=Path), multiple=True, help="File to import; repeatable."
)
@click.option(
    "--chatgpt", type=click.Path(path_type=Path), help="ChatGPT conversations.json to import."
)
@click.option("--drops-only", is_flag=True, help="Scan drops without scanning watched folders.")
@json_output
@click.pass_context
def ingest(
    context: click.Context,
    path: tuple[Path, ...],
    chatgpt: Path | None,
    drops_only: bool,
    as_json: bool,
) -> None:
    """Queue imports or scan configured inputs; return the job immediately."""
    if sum((bool(path), chatgpt is not None, drops_only)) > 1:
        raise click.UsageError("Choose --path, --chatgpt, or --drops-only.")
    if path or chatgpt:
        selected = [chatgpt] if chatgpt else path
        result = request(
            context,
            "POST",
            "/imports",
            {
                "paths": [str(p.expanduser().absolute()) for p in selected],
                "source_type": "chatgpt" if chatgpt else "documents",
            },
        )
    else:
        result = request(context, "POST", "/ingestion/runs", {"drops_only": drops_only})
    print_submission(result, as_json)


@main.command("compile")
@click.option("--full", is_flag=True)
@click.option("--dry-run", is_flag=True)
@json_output
@click.pass_context
def compile_command(context: click.Context, full: bool, dry_run: bool, as_json: bool) -> None:
    """Queue a manual build and print its job ID."""
    result = request(context, "POST", "/builds", {"full": full, "dry_run": dry_run})
    print_submission(result, as_json)


@main.command("preview-clusters")
@json_output
@click.pass_context
def preview_clusters(context: click.Context, as_json: bool) -> None:
    """Queue a chat-grouping preview for the staged sources."""
    print_submission(request(context, "POST", "/grouping-plan/preview"), as_json)


@main.command()
@click.option("--sources", is_flag=True, help="Show configured watched folders instead of jobs.")
@json_output
@click.pass_context
def status(context: click.Context, sources: bool, as_json: bool) -> None:
    """List recent jobs and their states."""
    if sources:
        result = request(context, "GET", "/watched-folders")
        lines = [
            f"{item['name']}: {item['path']} ({'enabled' if item['enabled'] else 'disabled'})"
            for item in result
        ]
        print_result(result, as_json, "\n".join(lines) or "No watched folders configured.")
    else:
        context.invoke(jobs_list, as_json=as_json)


@main.command()
@json_output
@click.pass_context
def state(context: click.Context, as_json: bool) -> None:
    """Show staged sources, built-source counts, and pending triage."""
    result = request(context, "GET", "/state")
    lines = [
        f"Built: {result['built_count']}  Review: {result['needs_review']}  "
        f"Skipped: {result['skipped']}",
        f"Staged: {len(result['staged'])}  Pending triage: {len(result['pending_triage'])}",
    ]
    lines.extend(
        f"  {item['rel']}"
        + (f" (set aside: {item['defer_reason']})" if item["defer_reason"] else "")
        for item in result["staged"]
    )
    print_result(result, as_json, "\n".join(lines))


@main.command()
@click.argument("raw_path")
@click.pass_context
def forget(context: click.Context, raw_path: str) -> None:
    """Trash a parsed source and clear its records. RAW_PATH is relative to raw/."""
    request(context, "DELETE", "/sources", params={"raw_path": raw_path})
    click.echo(f"Removed {raw_path}")


@main.command("forget-drop")
@click.argument("path", type=click.Path(path_type=Path))
@click.pass_context
def forget_drop(context: click.Context, path: Path) -> None:
    """Remove a captured input and its records. PATH identifies a file in drops/."""
    target = str(path.expanduser().absolute())
    request(context, "DELETE", "/imports", params={"file_path": target})
    click.echo(f"Removed {target}")


@main.command()
@click.argument("raw_paths", nargs=-1, required=True)
@click.pass_context
def recompile(context: click.Context, raw_paths: tuple[str, ...]) -> None:
    """Restage RAW_PATHS for the next manual build; paths are relative to raw/."""
    request(context, "POST", "/sources/recompile", {"raw_paths": list(raw_paths)})
    click.echo(f"Staged {len(set(raw_paths))} source(s) for the next build.")


@main.group()
def triage() -> None:
    """View and override chat triage decisions."""


@triage.command("list")
@click.option("--decision", type=click.Choice(["worthwhile", "review", "skip"]))
@json_output
@click.pass_context
def triage_list(context: click.Context, decision: str | None, as_json: bool) -> None:
    """List chat verdicts, optionally filtered by decision."""
    result = request(
        context, "GET", "/triage", params={"decision": decision} if decision else None
    )
    lines = [f"{item['decision']:10} {item['raw_path']}" for item in result]
    print_result(result, as_json, "\n".join(lines) or "No triage decisions.")


@triage.command("set")
@click.argument("raw_path")
@click.argument("decision", type=click.Choice(["worthwhile", "review", "skip"]))
@click.pass_context
def triage_set(context: click.Context, raw_path: str, decision: str) -> None:
    """Set a chat's verdict and move its raw copy into or out of skipped storage."""
    request(context, "PATCH", "/triage", {"raw_paths": [raw_path], "decision": decision})
    click.echo(f"Set {raw_path} -> {decision}")


@main.command()
@json_output
@click.pass_context
def doctor(context: click.Context, as_json: bool) -> None:
    """Check local model dependencies through the backend."""
    result = request(context, "GET", "/diagnostics")
    marker = "OK" if result["healthy"] else "FAIL"
    print_result(result, as_json, f"[{marker}] {result.get('message', '')}".rstrip())
    if not result["healthy"]:
        context.exit(1)


@main.group()
def mcp() -> None:
    """Expose this backend to desktop assistants."""


@mcp.command("serve")
@click.pass_context
def mcp_serve(context: click.Context) -> None:
    """Serve the stdio MCP adapter; all operations go through the HTTP backend."""
    from second_brain.apps.mcp import server

    server.serve(context.obj)


@mcp.command("install")
@click.option(
    "--target", type=click.Choice(["claude-desktop", "chatgpt-desktop", "cursor"]), required=True
)
@json_output
@click.pass_context
def mcp_install(context: click.Context, target: str, as_json: bool) -> None:
    """Configure a supported desktop assistant to use this backend."""
    result = request(context, "PUT", f"/integrations/mcp/{target}")
    print_result(result, as_json, result["message"])


for command in (jobs, domain, health, query, wiki):
    main.add_command(command)
