"""Schedule ingest and index jobs in response to file changes or a newly ready model.

Watchers identify changed files and unfinished work. Job history determines
whether to queue a new attempt, wait for one in progress, or leave a stopped job alone.
"""

import hashlib
import json
from collections.abc import Callable
from typing import Literal

from django.db import transaction
from django.db.models import QuerySet

from second_brain.apps.inference.catalog import ModelPurpose
from second_brain.apps.inference.services.worker_status import read_worker_status
from second_brain.apps.jobs.models import Job
from second_brain.apps.jobs.outcomes import WAITING_FOR
from second_brain.apps.jobs.services.job_queue import enqueue_job
from second_brain.django_config.paths import get_vault_paths

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


class ModelReadyTrigger:
    """Consider pending jobs once per ready model server, not on every watcher scan."""

    def __init__(self, purpose: ModelPurpose) -> None:
        self._purpose = purpose
        self._kind = "ingest" if purpose == "generation" else "index"
        self._handled_server_pid: int | None = None

    def schedule_pending(
        self, has_pending: Callable[[], bool], *, wiki_version: tuple = ()
    ) -> bool:
        """Queue unfinished model work unless an active or stopped job takes precedence.

        Parameters
        ----------
        has_pending: Callable[[], bool]
            Check raw chats or missing embeddings only when the model is ready and the job idle.
        wiki_version: tuple
            Wiki inputs an index pass covers. A triage-only pass covers no input files.

        Returns
        -------
        bool
            True if a new job was queued. A busy queue leaves readiness for the next scan.
        """
        worker = read_worker_status(get_vault_paths().backend_dir, self._purpose)
        if (
            worker is None
            or worker.phase != "ready"
            or worker.server_pid == self._handled_server_pid
        ):
            return False

        jobs = Job.objects.filter(kind=self._kind)
        active_jobs = jobs.filter(status__in=_ACTIVE)
        if active_jobs.exists():
            return False
        # Check the vault outside the queue's write lock. Even an empty result can go
        # stale if an ingest starts producing chats while the watcher is scanning.
        pending = has_pending()
        with transaction.atomic():
            if active_jobs.exists():
                return False
            queued = pending and self._enqueue_if_resumable(jobs, wiki_version)
        self._handled_server_pid = worker.server_pid
        return queued

    def _enqueue_if_resumable(self, jobs: QuerySet[Job], wiki_version: tuple) -> bool:
        payload: dict[str, str | list[str]]
        if self._kind == "ingest":
            # A cancelled selection that never ran produced no raw chats. A cancelled
            # retry or triage-only pass, on the other hand, stops this same work.
            jobs = jobs.exclude(
                status=Job.Status.CANCELLED,
                started_at__isnull=True,
                retry_of__isnull=True,
                payload__paths__0__isnull=False,
            )
            payload = {"paths": []}  # Triage existing raw chats without parsing any inputs.
        else:
            payload = {"watch_version": _version_digest(wiki_version)}

        # A newer submission can be cancelled before an older one finishes.
        last = jobs.order_by("-finished_at", "-created_at", "-id").first()
        retry_of = None
        if last is not None:
            if last.cancel_requested or last.status in (
                Job.Status.CANCELLED,
                Job.Status.INTERRUPTED,
            ):
                return False
            if last.status == Job.Status.FAILED:
                if last.result.get(WAITING_FOR) != self._purpose:
                    return False
                retry_of = last
        enqueue_job(self._kind, payload, retry_of=retry_of)
        return True
