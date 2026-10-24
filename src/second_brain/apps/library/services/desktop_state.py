"""Compose the desktop's read model from the services that own its data."""

from second_brain.apps.compilation.services.build_log import list_build_actions
from second_brain.apps.compilation.services.build_requests import get_grouping
from second_brain.apps.ingestion.services.file_imports import list_imports
from second_brain.apps.jobs.models import Job
from second_brain.apps.library.services.library_state import get_state
from second_brain.apps.library.services.manual_triage import list_decisions
from second_brain.apps.preferences.services.user_settings import get_pipeline_config


def get_desktop_state() -> dict:
    """Read library state, active jobs, and the latest terminal attempt in each lane.

    Database and filesystem reads do not form one atomic snapshot.

    Returns
    -------
    dict
        Library state, jobs with progress, imports, triage decisions, grouping, build
        actions, and the compilation model. Completed index jobs are omitted.
    """
    config = get_pipeline_config()
    jobs = Job.objects.defer("payload", "config_snapshot").order_by("-created_at", "-id")
    active = [Job.Status.RUNNING, Job.Status.QUEUED]
    displayed = list(jobs.filter(status__in=active))
    for lane in ("ingest", "build"):
        latest = jobs.filter(lane=lane).exclude(status__in=active).exclude(kind="index").first()
        if latest is not None:
            displayed.append(latest)
    return {
        "state": get_state(config),
        "jobs": [{"job": job, "progress": job.progress} for job in displayed],
        "imports": list_imports(),
        "triage": list_decisions(),
        "grouping_plan": get_grouping(),
        "build_actions": list_build_actions(),
        "compilation_model": config.compilation.model,
    }
