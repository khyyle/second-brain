"""HTTP entry points for job lifecycle, progress, attempts, and cancellation."""

from uuid import UUID

from django.http import HttpRequest
from ninja import Query, Router, Status

from second_brain.apps.jobs.models import Job
from second_brain.apps.jobs.schemas import JobOut, JobPage, JobProgress
from second_brain.apps.jobs.services import job_queue
from second_brain.django_config.schemas import ErrorResponse

router = Router(tags=["Jobs"])


@router.get(
    "/jobs", response={200: JobPage, 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse}
)
def list_jobs(
    request: HttpRequest,
    lane: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    """List attempts newest first, with optional lane and status filters.

    Parameters
    ----------
    request: HttpRequest
        Incoming authenticated HTTP request.
    lane: str | None
        Optional ingest or build lane filter.
    status: str | None
        Optional job-status filter.
    limit: int
        Maximum number of jobs, from 1 to 200.
    offset: int
        Number of matching jobs to skip.

    Returns
    -------
    dict
        Jobs ordered newest first, matching total, limit, and offset.
    """
    return job_queue.list_jobs(lane=lane, status=status, limit=limit, offset=offset)


@router.get(
    "/jobs/{job_id}",
    response={200: JobProgress, 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse},
)
def get_job(request: HttpRequest, job_id: UUID) -> dict:
    """Read an attempt and its latest recorded progress.

    Parameters
    ----------
    request: HttpRequest
        Incoming authenticated HTTP request.
    job_id: UUID
        Job UUID.

    Returns
    -------
    dict
        Stored job and its current progress.
    """
    job = job_queue.get_job(job_id)
    return {"job": job, "progress": job.progress}


@router.post(
    "/jobs/{job_id}/cancel",
    response={200: JobOut, 400: ErrorResponse, 404: ErrorResponse, 409: ErrorResponse},
)
def cancel_job(request: HttpRequest, job_id: UUID) -> Job:
    """Cancel queued work or request a cooperative stop for a running attempt.

    Parameters
    ----------
    request: HttpRequest
        Incoming authenticated HTTP request.
    job_id: UUID
        Job UUID.

    Returns
    -------
    Job
        Cancelled queued job, running job with cancellation requested, or unchanged terminal
        job.
    """
    return job_queue.cancel(job_id)


@router.post(
    "/jobs/{job_id}/retry",
    response={
        202: JobOut,
        400: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
        500: ErrorResponse,
        503: ErrorResponse,
    },
)
def retry_job(request: HttpRequest, job_id: UUID) -> Status[Job]:
    """Accept a fresh attempt, applying the same prerequisites as the original request.

    Parameters
    ----------
    request: HttpRequest
        Incoming authenticated HTTP request.
    job_id: UUID
        UUID of a failed, cancelled, or interrupted job.

    Returns
    -------
    Status[Job]
        HTTP 202 containing a new queued attempt.
    """
    return Status(202, job_queue.retry(job_id))
