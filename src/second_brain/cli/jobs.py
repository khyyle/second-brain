"""Job inspection, cancellation, and retries."""

import json
from uuid import UUID

import click

from .common import json_output, print_result, print_submission, request, summarize_job


@click.group()
def jobs() -> None:
    """Inspect, cancel, and retry background jobs."""


@jobs.command("list")
@click.option("--lane", type=click.Choice(["ingest", "build"]))
@click.option("--status", "job_status", help="Filter by job status, such as failed or running.")
@click.option("-n", "--limit", type=click.IntRange(1, 200), default=50, show_default=True)
@click.option("--offset", type=click.IntRange(min=0), default=0)
@json_output
@click.pass_context
def jobs_list(
    context: click.Context,
    lane: str | None,
    job_status: str | None,
    limit: int,
    offset: int,
    as_json: bool,
) -> None:
    """List attempts, newest first."""
    params = {"limit": limit, "offset": offset}
    if lane:
        params["lane"] = lane
    if job_status:
        params["status"] = job_status
    result = request(context, "GET", "/jobs", params=params)
    lines = [summarize_job(job) for job in result["items"]]
    print_result(result, as_json, "\n".join(lines) or "No jobs found.")


@jobs.command("show")
@click.argument("job_id", type=click.UUID)
@json_output
@click.pass_context
def jobs_show(context: click.Context, job_id: UUID, as_json: bool) -> None:
    """Show an attempt's status, progress, and result."""
    result = request(context, "GET", f"/jobs/{job_id}")
    job = result["job"]
    lines = [summarize_job(job)]
    if result["progress"].get("current_activity"):
        lines.append(result["progress"]["current_activity"])
    if job["result"]:
        lines.append(json.dumps(job["result"], indent=2))
    print_result(result, as_json, "\n".join(lines))


@jobs.command("cancel")
@click.argument("job_id", type=click.UUID)
@json_output
@click.pass_context
def jobs_cancel(context: click.Context, job_id: UUID, as_json: bool) -> None:
    """Cancel queued work or request a stop for a running attempt."""
    result = request(context, "POST", f"/jobs/{job_id}/cancel")
    print_result(result, as_json, summarize_job(result))


@jobs.command("retry")
@click.argument("job_id", type=click.UUID)
@json_output
@click.pass_context
def jobs_retry(context: click.Context, job_id: UUID, as_json: bool) -> None:
    """Submit a fresh attempt for a failed, cancelled, or interrupted job."""
    print_submission(request(context, "POST", f"/jobs/{job_id}/retry"), as_json)
