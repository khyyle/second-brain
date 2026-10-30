"""Durable FIFO jobs. SQLite is both the application database and the queue."""

from uuid import UUID

from django.db import transaction
from django.utils import timezone

from second_brain.apps.jobs.models import Job
from second_brain.apps.preferences.services.user_settings import get_preferences
from second_brain.django_config.errors import ConflictError, NotFoundError, ServiceError

KINDS = {"ingest": "ingest", "build": "build", "preview": "build", "index": "build"}
TERMINAL = {Job.Status.SUCCEEDED, Job.Status.FAILED, Job.Status.CANCELLED, Job.Status.INTERRUPTED}


def enqueue_job(
    kind: str, payload: dict, *, snapshot: dict | None = None, job_id: UUID | None = None
) -> Job:
    """Persist a queued operation with its arguments and captured preferences.

    Parameters
    ----------
    kind: str
        ingest, build, preview, or index; determines the independent single-worker lane.
    payload: dict
        Internal validated operation arguments. Never contains credentials.
    snapshot: dict | None
        Captured preferences, or None to capture current preferences.
    job_id: UUID | None
        Preallocated identity when a build's input directory has already been prepared.

    Returns
    -------
    Job
        Queued record visible to the worker only after its transaction commits.
    """
    if kind not in KINDS:
        raise ServiceError("Unsupported job kind.")
    values = {
        "kind": kind,
        "lane": KINDS[kind],
        "payload": payload,
        "config_snapshot": snapshot
        if snapshot is not None
        else get_preferences().model_dump(mode="json"),
    }
    if job_id is not None:
        values["id"] = job_id
    return Job.objects.create(**values)


def get_job(job_id: UUID) -> Job:
    """Return an attempt or raise NotFoundError if its ID is unknown.

    Parameters
    ----------
    job_id: UUID
        Primary key of the job to read.

    Returns
    -------
    Job
        Stored job; raises NotFoundError if it does not exist.
    """
    try:
        return Job.objects.get(pk=job_id)
    except Job.DoesNotExist as exc:
        raise NotFoundError("This job does not exist.") from exc


def list_jobs(*, lane: str | None, status: str | None, limit: int, offset: int) -> dict:
    """Filter and paginate attempts, newest first.

    Parameters
    ----------
    lane: str | None
        Optional ingest or build lane filter.
    status: str | None
        Optional job-status filter.
    limit: int
        Maximum number of rows to return.
    offset: int
        Number of matching rows to skip.

    Returns
    -------
    dict
        Matching jobs under items, ordered newest first, plus total count, limit,
        and offset.
    """
    if lane is not None and lane not in set(KINDS.values()):
        raise ServiceError("Unknown worker lane.")
    if status is not None and status not in Job.Status.values:
        raise ServiceError("Unknown job status.")
    jobs = Job.objects.all()
    if lane is not None:
        jobs = jobs.filter(lane=lane)
    if status is not None:
        jobs = jobs.filter(status=status)
    return {
        "items": list(jobs.order_by("-created_at", "-id")[offset : offset + limit]),
        "total": jobs.count(),
        "limit": limit,
        "offset": offset,
    }


def claim_next_job(lane: str) -> Job | None:
    """Claim the oldest queued attempt in one short IMMEDIATE SQLite transaction.

    Parameters
    ----------
    lane: str
        Worker lane whose oldest queued job should be claimed.

    Returns
    -------
    Job | None
        The claimed job, or None. The caller must hold the exclusive lane OS lock.
        Execution starts after this transaction commits.
    """
    with transaction.atomic():
        job = (
            Job.objects.filter(lane=lane, status=Job.Status.QUEUED)
            .order_by("created_at", "id")
            .first()
        )
        if job is None:
            return None
        now = timezone.now()
        claimed = Job.objects.filter(pk=job.pk, status=Job.Status.QUEUED).update(
            status=Job.Status.RUNNING, started_at=now, heartbeat_at=now
        )
        return get_job(job.pk) if claimed else None


def cancel(job_id: UUID) -> Job:
    """Cancel queued work immediately or request a cooperative stop at a running checkpoint.

    Completed attempts remain unchanged. A model/OCR call already in progress may
    finish before cancellation is observed; completed work remains recorded.

    Parameters
    ----------
    job_id: UUID
        Job to cancel.

    Returns
    -------
    Job
        Updated job. Queued jobs become cancelled immediately; running jobs receive a
        cancellation flag. Terminal jobs stay unchanged.
    """
    with transaction.atomic():
        job = get_job(job_id)
        if job.status == Job.Status.QUEUED:
            Job.objects.filter(pk=job_id, status=Job.Status.QUEUED).update(
                status=Job.Status.CANCELLED, cancel_requested=True, finished_at=timezone.now()
            )
        elif job.status == Job.Status.RUNNING:
            Job.objects.filter(pk=job_id, status=Job.Status.RUNNING).update(cancel_requested=True)
        return get_job(job_id)


def retry(job_id: UUID) -> Job:
    """Create a fresh attempt from current inputs/settings after a failed or stopped attempt.

    The old attempt and its snapshot remain intact. Already built source versions
    are skipped unless the original request explicitly asked for a full rebuild.
    Ingestion retries include unchanged failed files within the original request's scope.

    Parameters
    ----------
    job_id: UUID
        Failed, cancelled, or interrupted job to retry.

    Returns
    -------
    Job
        New queued attempt. Build inputs and preferences are captured again.
    """
    old = get_job(job_id)
    if old.status not in {Job.Status.FAILED, Job.Status.CANCELLED, Job.Status.INTERRUPTED}:
        raise ConflictError("Only failed, cancelled, or interrupted jobs can be retried.")
    if old.kind in {"build", "preview"}:
        from second_brain.apps.compilation.services.build_requests import request_build

        new = request_build(
            full=old.payload.get("full", False),
            dry_run=old.payload.get("dry_run", False),
            preview=old.kind == "preview",
        )
    elif old.kind == "ingest":
        new = enqueue_job(old.kind, {**old.payload, "retry_failed": True})
    else:
        new = enqueue_job(old.kind, old.payload)
    Job.objects.filter(pk=new.pk).update(retry_of=old)
    new.retry_of = old
    return new


def recover_lane(lane: str) -> int:
    """Mark abandoned attempts interrupted after acquiring exclusive lane ownership.

    The caller must hold the exclusive lane OS lock, proving the previous worker
    has released ownership. Queued attempts remain queued; interrupted work requires
    an explicit retry.

    Parameters
    ----------
    lane: str
        Lane whose worker lock is already held exclusively by this process.

    Returns
    -------
    int
        Number of running jobs marked interrupted; queued jobs are unchanged.
    """
    return Job.objects.filter(lane=lane, status=Job.Status.RUNNING).update(
        status=Job.Status.INTERRUPTED,
        finished_at=timezone.now(),
        error=(
            "The worker stopped before this attempt finished. Review its progress before retrying."
        ),
    )
