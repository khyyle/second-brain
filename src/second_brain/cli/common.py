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
    """Make an API request and report transport or backend failures as Click errors.

    Parameters
    ----------
    context: click.Context
        Click context whose obj is the configured backend client.
    method: str
        HTTP method.
    path: str
        API path relative to the backend base URL.
    payload: dict[str, Any] | None
        Optional JSON request body.
    params: dict[str, str | int] | None
        Optional URL query parameters.

    Returns
    -------
    Any
        Decoded JSON response, or None for HTTP 204.
    """
    try:
        return context.obj.request(method, path, payload=payload, params=params)
    except (OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
        raise click.ClickException(str(exc)) from exc


def print_result(result: Any, as_json: bool, message: str) -> None:
    """Write JSON output or a human-readable message to standard output.

    Parameters
    ----------
    result: Any
        JSON-serializable response value.
    as_json: bool
        Whether to print the response as indented JSON.
    message: str
        Text printed when JSON output is disabled.

    Returns
    -------
    None
    """
    click.echo(json.dumps(result, indent=2) if as_json else message)


def summarize_job(job: dict[str, Any]) -> str:
    """Format a job status, cancellation flag, and any failure reason.

    Parameters
    ----------
    job: dict[str, Any]
        Serialized job with id and status, plus optional kind, result, and error fields.

    Returns
    -------
    str
        Terminal summary with a second line for an error or outcome reason.
    """
    message = f"{job['id']}  {job['status']}  {job.get('kind', '')}".rstrip()
    if job.get("cancel_requested") and job["status"] == "running":
        message += " (stop requested)"
    if job.get("error"):
        message += f"\n  {job['error']}"
    elif job.get("result", {}).get("reason"):
        message += f"\n  {job['result']['reason']}"
    return message


def print_submission(result: Any, as_json: bool) -> None:
    """Print a job submission and the command for inspecting its status.

    Parameters
    ----------
    result: Any
        Serialized job or submission object containing a job key.
    as_json: bool
        Whether to print the full submission as JSON instead.

    Returns
    -------
    None
    """
    job = result.get("job", result)
    print_result(
        result, as_json, f"{summarize_job(job)}\nInspect with: second-brain jobs show {job['id']}"
    )


json_output = click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
