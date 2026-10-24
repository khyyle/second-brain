"""HTTP requests and terminal output for command handlers."""

import json
from typing import Any

import click
import httpx


def request(
    context: click.Context,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    params: dict[str, str | int] | None = None,
) -> Any:
    """Make an API request and report transport or backend failures as Click errors."""
    try:
        return context.obj.request(method, path, payload=payload, params=params)
    except (OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
        raise click.ClickException(str(exc)) from exc


def print_result(result: Any, as_json: bool, message: str) -> None:
    click.echo(json.dumps(result, indent=2) if as_json else message)


def summarize_job(job: dict[str, Any]) -> str:
    message = f"{job['id']}  {job['status']}  {job.get('kind', '')}".rstrip()
    if job.get("cancel_requested") and job["status"] == "running":
        message += " (stop requested)"
    if job.get("error"):
        message += f"\n  {job['error']}"
    elif job.get("result", {}).get("reason"):
        message += f"\n  {job['result']['reason']}"
    return message


def print_submission(result: Any, as_json: bool) -> None:
    job = result.get("job", result)
    print_result(
        result, as_json, f"{summarize_job(job)}\nInspect with: second-brain jobs show {job['id']}"
    )


json_output = click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
