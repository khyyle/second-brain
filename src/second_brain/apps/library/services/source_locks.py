"""Protect user source changes while workers own affected inputs."""

from second_brain.apps.jobs.models import Job
from second_brain.apps.library.errors import SourceConflictError


def require_sources_idle(raw_paths: list[str]) -> None:
    """Reject edits during ingestion or while a queued/running build holds these sources."""
    active = Job.objects.filter(status__in=["queued", "running"])
    if active.filter(lane="ingest").exists():
        raise SourceConflictError("Wait for ingestion to finish before changing source records.")
    selected = set(raw_paths)
    if any(
        selected.intersection(payload.get("sources", []))
        for payload in active.filter(lane="build").values_list("payload", flat=True)
    ):
        raise SourceConflictError(
            "These sources belong to an active job. Finish or cancel it first."
        )
