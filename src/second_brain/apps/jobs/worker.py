"""A dedicated lane process, supervised by the backend launcher."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
from contextlib import nullcontext
from typing import TYPE_CHECKING

import django
from django.db import close_old_connections, connections
from django.utils import timezone
from pydantic_core import to_jsonable_python

if TYPE_CHECKING:
    from second_brain.apps.jobs.models import Job

logger = logging.getLogger(__name__)


def run_attempt(job: Job) -> None:
    """Execute a claimed attempt, retain progress, and publish exactly one terminal state."""
    from second_brain.apps.jobs.models import Job
    from second_brain.apps.jobs.services import job_progress
    from second_brain.apps.jobs.services.job_execution import execute_job
    from second_brain.errors import SecondBrainError

    job_progress.bind_job(job.pk)
    stop = threading.Event()

    def heartbeat() -> None:
        try:
            while not stop.wait(5):
                close_old_connections()
                job_progress.touch_heartbeat()
        finally:
            connections.close_all()

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    result, error = {}, ""
    terminal = Job.Status.FAILED
    try:
        result = to_jsonable_python(execute_job(job))
        if (
            job_progress.stop_requested()
            or result.get("cancelled")
            or result.get("outcome") == "stopped"
        ):
            terminal = Job.Status.CANCELLED
        elif result.get("outcome") == "failed" or result.get("failed", 0):
            error = (
                result.get("reason")
                or "Some inputs could not be processed. Review the source errors."
            )
        else:
            terminal = Job.Status.SUCCEEDED
    except SecondBrainError as exc:
        error = exc.reason
    except Exception:
        logger.exception("Job %s failed", job.pk)
        error = "The operation failed unexpectedly. Review backend logs before retrying."
    finally:
        stop.set()
        thread.join(timeout=6)
        if job_progress._shutdown:
            terminal = Job.Status.INTERRUPTED
            error = (
                "The backend stopped during this attempt. Review completed work before retrying."
            )
        Job.objects.filter(pk=job.pk, status=Job.Status.RUNNING).update(
            status=terminal,
            result=result,
            error=error,
            finished_at=timezone.now(),
            heartbeat_at=timezone.now(),
        )
        if terminal in {Job.Status.FAILED, Job.Status.INTERRUPTED}:
            logger.warning("Job %s (%s) %s: %s", job.pk, job.kind, terminal, error)
        else:
            logger.info("Job %s (%s) %s", job.pk, job.kind, terminal)
        job_progress.bind_job(None)
        connections.close_all()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lane", choices=["ingest", "build"])
    args = parser.parse_args()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "second_brain.django_config.settings.runtime")
    django.setup()
    from second_brain.apps.jobs.services import job_progress, job_queue
    from second_brain.django_config.vault_storage import vault_lock

    parent_pid = int(os.environ.get("SECOND_BRAIN_WORKER_PARENT_PID", os.getppid()))

    def watch_parent() -> None:
        # A hard-killed launcher cannot run cleanup. Release the lane after the
        # current operation reaches a cancellation boundary, instead of orphaning it.
        while not job_progress._shutdown:
            if os.getppid() != parent_pid:
                job_progress.request_shutdown()
                return
            threading.Event().wait(1)

    threading.Thread(target=watch_parent, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: job_progress.request_shutdown())
    signal.signal(signal.SIGINT, lambda *_: job_progress.request_shutdown())
    with vault_lock(f"worker-{args.lane}", blocking=False):
        job_queue.recover_lane(args.lane)
        while not job_progress._shutdown:
            close_old_connections()
            guard = vault_lock("imports") if args.lane == "ingest" else nullcontext()
            with guard:
                job = job_queue.claim_next_job(args.lane)
                if job is not None:
                    run_attempt(job)
            if job is None:
                threading.Event().wait(0.5)
        connections.close_all()


if __name__ == "__main__":
    main()
