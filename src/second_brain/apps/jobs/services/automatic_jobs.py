"""Schedule ingest and index jobs in response to changed files."""

import hashlib
import json
from typing import Literal

from django.db import transaction

from second_brain.apps.jobs.models import Job
from second_brain.apps.jobs.services.job_queue import enqueue_job

_ACTIVE = (Job.Status.QUEUED, Job.Status.RUNNING)


def _version_digest(inputs: tuple) -> str:
    return hashlib.sha256(json.dumps(inputs).encode()).hexdigest()


def schedule_for_file_change(
    kind: Literal["ingest", "index"],
    *,
    observed_inputs: tuple,
    drop_versions: tuple = (),
    missing_index: bool = False,
    queue_after_running: bool = False,
) -> Literal["queued", "covered", "busy"]:
    """Decide whether to queue a file-triggered job under one SQLite write lock.

    Parameters
    ----------
    kind: Literal["ingest", "index"]
        A full source scan or wiki index pass.
    observed_inputs: tuple
        Wiki page versions, or ingestion preferences and watched originals.
    drop_versions: tuple
        Only new or edited drops should wake a stopped attempt:
        ingestion itself removes processed drops.
    missing_index: bool
        Rebuild a missing search database even if the last index failed on the same wiki.
    queue_after_running: bool
        Queue a successor when a wiki edit may have arrived after a running index read it.

    Returns
    -------
    Literal["queued", "covered", "busy"]
        Queued a job, accounted for these inputs already, or must check again later.
    """
    version = _version_digest(observed_inputs)
    drop_digests = {_version_digest(entry) for entry in drop_versions}
    jobs = Job.objects.filter(kind=kind)
    with transaction.atomic():
        last_scan = (
            jobs.filter(payload__has_key="watch_version")
            .order_by("-created_at", "-id")
            .only("payload", "status")
            .first()
        )
        if (
            last_scan is not None
            and last_scan.payload["watch_version"] == version
            and drop_digests.issubset(last_scan.payload.get("watch_drops", ()))
        ):
            if last_scan.status in _ACTIVE:
                return "covered"
            # The scan records which files were seen; a later scoped retry may be the job stopped.
            last_status = (
                jobs.exclude(status__in=_ACTIVE)
                .order_by("-finished_at", "-created_at", "-id")
                .values_list("status", flat=True)
                .first()
            )
            rebuild_missing_index = missing_index and last_status == Job.Status.FAILED
            if last_status != Job.Status.SUCCEEDED and not rebuild_missing_index:
                return "covered"
        queued = jobs.filter(status=Job.Status.QUEUED).first()
        if queued is not None:
            # Every index job reads the whole wiki. An ingest may cover selected paths only.
            if kind == "ingest":
                return "busy"
            queued.payload = {**queued.payload, "watch_version": version}
            queued.save(update_fields=["payload"])
            return "covered"
        if not queue_after_running and jobs.filter(status=Job.Status.RUNNING).exists():
            return "busy"
        payload: dict = {"watch_version": version}
        if drop_digests:
            payload["watch_drops"] = sorted(drop_digests)
        enqueue_job(kind, payload)
        return "queued"
