"""Per-job progress and cancellation for a single-operation worker process."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID

from django.utils import timezone

from second_brain.apps.jobs.models import Job

_job_id = None
_shutdown = False


@dataclass(frozen=True)
class PipelineStatus:
    """A snapshot of pipeline progress stored on the active Job row.

    Parameters
    ----------
    running: bool
        Whether progress is active. False retains the last counters and costs;
        it does not distinguish success, failure, and cancellation.
    phase: str
        Stage reporting progress, such as ingest, triage, compile, or grouping.
    current: int
        Work items accounted for in this phase, excluding the item in progress.
        Ingestion counts successfully processed files.
    total: int
        Work items in the phase; zero for stages without per-item progress.
    updated_at: str
        UTC ISO 8601 timestamp of this progress snapshot.
    started_at: str
        ISO 8601 timestamp when this phase's run began.
    cost_usd: float
        Accumulated provider API cost in US dollars for this run.
    current_sources: tuple[str, ...]
        Paths relative to raw/ for the sources currently being processed;
        empty when individual sources are not reported.
    queued_sources: tuple[str, ...]
        Paths relative to raw/ waiting after the current sources; empty when
        the queue is exhausted or not reported.
    current_started_at: str
        ISO 8601 start time of the current sources, or an empty string if unreported.
    current_cost_usd: float
        Provider API cost for the current sources, already included in cost_usd.
    current_activity: str
        Short description of the current activity, or an empty string if unreported.
    """

    running: bool
    phase: str
    current: int
    total: int
    updated_at: str
    started_at: str
    cost_usd: float
    current_sources: tuple[str, ...]
    queued_sources: tuple[str, ...]
    current_started_at: str
    current_cost_usd: float
    current_activity: str


def now_iso() -> str:
    """Read the current UTC time.

    Returns
    -------
    str
        Timezone-aware ISO 8601 timestamp.
    """
    return datetime.now(UTC).isoformat()


def update_progress(
    phase: str,
    current: int,
    total: int,
    started_at: str,
    current_sources: tuple[str, ...] = (),
    cost_usd: float = 0.0,
    current_started_at: str = "",
    current_cost_usd: float = 0.0,
    current_activity: str = "",
    queued_sources: tuple[str, ...] = (),
) -> None:
    """Replace progress and refresh the heartbeat of the bound running job.

    Does nothing when no job is bound or the job is no longer running.

    Parameters
    ----------
    phase: str
        Current stage label, such as ingest, triage, compile, or grouping.
    current: int
        Items processed so far.
    total: int
        Total items in this run (0 when not a per-item stage).
    started_at: str
        ISO timestamp when this run began.
    current_sources: tuple[str, ...]
        Paths relative to raw/ for the sources currently being processed.
    cost_usd: float
        Accumulated API cost so far this run.
    current_started_at: str
        ISO timestamp when processing of the current sources began.
    current_cost_usd: float
        API cost so far for the sources being compiled.
    current_activity: str
        What the build agent is doing for them, as a short phrase.
    queued_sources: tuple[str, ...]
        Raw paths still waiting in this stage after the current ones.

    Returns
    -------
    None
    """
    status = PipelineStatus(
        running=True,
        phase=phase,
        current=current,
        total=total,
        updated_at=now_iso(),
        started_at=started_at,
        cost_usd=cost_usd,
        current_sources=current_sources,
        queued_sources=queued_sources,
        current_started_at=current_started_at,
        current_cost_usd=current_cost_usd,
        current_activity=current_activity,
    )
    if _job_id is not None:
        Job.objects.filter(pk=_job_id, status=Job.Status.RUNNING).update(
            progress=asdict(status), heartbeat_at=timezone.now()
        )


def touch_heartbeat() -> None:
    """Refresh the active attempt's liveness without overwriting its progress.

    Returns
    -------
    None
    """
    if _job_id is not None:
        Job.objects.filter(pk=_job_id, status=Job.Status.RUNNING).update(
            heartbeat_at=timezone.now()
        )


def finish_progress() -> None:
    """Mark bound-job progress as not running, retaining its counters and cost.

    Returns
    -------
    None
    """
    if _job_id is not None:
        job = Job.objects.filter(pk=_job_id).first()
        if job is not None:
            progress = {**job.progress, "running": False}
            Job.objects.filter(pk=_job_id).update(progress=progress)


def stop_requested() -> bool:
    """Check cooperative cancellation for this process's bound attempt.

    Returns
    -------
    bool
        True if process shutdown or cancellation of the bound job was requested.
    """
    return _shutdown or (
        _job_id is not None and Job.objects.filter(pk=_job_id, cancel_requested=True).exists()
    )


def bind_job(job_id: UUID | None) -> None:
    """Set the active job identity shared by this process and its heartbeat thread.

    Parameters
    ----------
    job_id: UUID | None
        Running job to update, or None to clear the binding.

    Returns
    -------
    None
    """
    global _job_id
    _job_id = job_id


def request_shutdown() -> None:
    """Signal a cooperative stop without doing database I/O inside a signal handler.

    Returns
    -------
    None
    """
    global _shutdown
    _shutdown = True
