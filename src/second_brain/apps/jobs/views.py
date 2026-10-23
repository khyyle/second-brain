"""HTTP entry points for job lifecycle, progress, attempts, and cancellation."""

from uuid import UUID

from django.http import HttpRequest
from ninja import Query, Router

from second_brain.apps.jobs.schemas import JobOut, JobPage, JobProgress
from second_brain.django_config.scaffold import ErrorResponse, pending

router = Router(tags=["Jobs"])


@router.get("/jobs", response={200: JobPage, 501: ErrorResponse})
def list_jobs(
    request: HttpRequest,
    lane: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> JobPage:
    """TODO: Query/filter Job, order by -created_at/id, count and slice."""
    pending("List jobs")


@router.get("/jobs/{job_id}", response={200: JobProgress, 501: ErrorResponse})
def get_job(request: HttpRequest, job_id: UUID) -> JobProgress:
    """TODO: Load Job and per-job progress; 404 if absent.

    services.status currently writes one shared .status.json. Make progress job-scoped
    before allowing ingest and build workers to run at the same time.
    """
    pending("Read job")


@router.post("/jobs/{job_id}/cancel", response={200: JobOut, 501: ErrorResponse})
def cancel_job(request: HttpRequest, job_id: UUID) -> JobOut:
    """TODO: Atomically cancel queued jobs or request cooperative cancellation of running jobs.

    Adapt services.status.request_stop to target a job; finished jobs stay finished.
    """
    pending("Cancel job")


@router.post("/jobs/{job_id}/retry", response={202: JobOut, 501: ErrorResponse})
def retry_job(request: HttpRequest, job_id: UUID) -> tuple[int, JobOut]:
    """TODO: Create a new attempt with retry_of; enqueue with transaction.on_commit.

    Validate terminal status and reusable inputs. Do not overwrite the failed attempt.
    """
    pending("Retry job")
