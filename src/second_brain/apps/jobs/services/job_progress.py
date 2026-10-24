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
    """A snapshot of pipeline progress stored on the active Job row."""

    running: bool
    phase: str  # "ingest" | "triage" | "compile" | "idle"
    current: int
    total: int
    updated_at: str
    started_at: str  # run start, for elapsed display
    cost_usd: float  # accumulated API cost this run (0 for free stages)
    current_sources: tuple[str, ...]
    queued_sources: tuple[str, ...]
    current_started_at: str
    current_cost_usd: float
    current_activity: str


def now_iso() -> str:
    """Current UTC time as an ISO string (run-start marker for elapsed)."""
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
    """
    Write a running heartbeat snapshot.

    Parameters
    ----------
    phase: str
        Current stage label (``"ingest"``, ``"triage"``, ``"compile"``).
    current: int
        Items processed so far.
    total: int
        Total items in this run (0 when not a per-item stage).
    started_at: str
        ISO timestamp when this run began (for elapsed display).
    current_sources: tuple[str, ...]
        Raw paths of the sources being compiled; empty outside a build.
    cost_usd: float
        Accumulated API cost so far this run.
    current_started_at: str
        ISO timestamp when the sources being compiled were started.
    current_cost_usd: float
        API cost so far for the sources being compiled.
    current_activity: str
        What the build agent is doing for them, as a short phrase.
    queued_sources: tuple[str, ...]
        Raw paths still waiting in this stage after the current ones.
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
    """Refresh the active attempt's liveness without overwriting its progress."""
    if _job_id is not None:
        Job.objects.filter(pk=_job_id, status=Job.Status.RUNNING).update(
            heartbeat_at=timezone.now()
        )


def finish_progress() -> None:
    """Mark the current stage idle while retaining its final counters and cost."""
    if _job_id is not None:
        job = Job.objects.filter(pk=_job_id).first()
        if job is not None:
            progress = {**job.progress, "running": False}
            Job.objects.filter(pk=_job_id).update(progress=progress)


def stop_requested() -> bool:
    """Check cooperative cancellation for this process's bound attempt."""
    return _shutdown or (
        _job_id is not None and Job.objects.filter(pk=_job_id, cancel_requested=True).exists()
    )


def bind_job(job_id: UUID | None) -> None:
    """Bind one worker attempt; heartbeat threads share this process-local identity."""
    global _job_id
    _job_id = job_id


def request_shutdown() -> None:
    """Signal a cooperative stop without doing database I/O inside a signal handler."""
    global _shutdown
    _shutdown = True
