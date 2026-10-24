"""Own worker processes and detect changed watched inputs while the backend is running."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from django.db import close_old_connections, connections

from second_brain.apps.ingestion.services.source_staging import SUPPORTED_EXTENSIONS
from second_brain.apps.jobs.models import Job
from second_brain.apps.jobs.services.job_queue import enqueue_job
from second_brain.apps.preferences.services.user_settings import get_pipeline_config
from second_brain.pipeline_config import PipelineConfig

logger = logging.getLogger(__name__)


def input_fingerprint(config: PipelineConfig) -> tuple[tuple[str, int, int], ...]:
    """Return supported input paths, sizes, and modification times without reading bytes.

    Parameters
    ----------
    config: PipelineConfig
        Vault locations, watched folders, and supported source settings.

    Returns
    -------
    tuple[tuple[str, int, int], ...]
        Sorted (path, modification time in nanoseconds, byte size) entries for supported
        files in drops and enabled watched folders. File contents are not read.
    """
    roots = [(config.drops_dir, None)] + [
        (source.path, set(source.file_types))
        for source in config.sources.values()
        if source.enabled
    ]
    entries = []
    for root, types in roots:
        if not root.is_dir() or root.is_symlink():
            continue
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if any(part.startswith(".") for part in relative.parts) or path.is_symlink():
                continue
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            if types is not None and path.suffix.lower().lstrip(".") not in types:
                continue
            try:
                stat = path.stat()
                if path.is_file():
                    entries.append((str(path), stat.st_mtime_ns, stat.st_size))
            except FileNotFoundError:
                continue
    return tuple(sorted(entries))


@contextmanager
def supervised_workers() -> Iterator[None]:
    """Start and monitor one subprocess per lane; stop both when the launcher exits.

    A lightweight scan observes file metadata every two seconds and requires a stable
    fingerprint before queueing an ingest scan. No builds are scheduled automatically.
    Updated preferences are noticed by the same loop. Worker crashes cause a restart;
    the new worker marks its old running attempt interrupted before claiming more work.

    Yields
    ------
    None
        Keeps lane workers and the folder watcher running until context exit, then stops
        them.
    """
    stop = threading.Event()
    processes = {}
    thread = None

    def start(lane: str) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [sys.executable, "-m", "second_brain.apps.jobs.worker", lane],
            env={**os.environ, "SECOND_BRAIN_WORKER_PARENT_PID": str(os.getpid())},
        )

    def supervise() -> None:
        previous = None
        scheduled = None
        previous_wiki = None
        scheduled_wiki = None
        try:
            while not stop.wait(2):
                close_old_connections()
                for lane, process in list(processes.items()):
                    if process.poll() is not None:
                        logger.warning(
                            "Restarting %s worker after exit %s", lane, process.returncode
                        )
                        try:
                            processes[lane] = start(lane)
                        except OSError:
                            # Keep the exited process so the next pass retries its lane.
                            logger.exception("Unable to restart %s worker", lane)
                try:
                    config = get_pipeline_config()
                    fingerprint = (
                        config.model_dump_json(exclude={"raw_input_dir"}),
                        input_fingerprint(config),
                    )
                    if fingerprint == previous and fingerprint != scheduled:
                        if not Job.objects.filter(
                            lane="ingest", status__in=["queued", "running"]
                        ).exists():
                            if fingerprint[1]:
                                enqueue_job("ingest", {"drops_only": False})
                            scheduled = fingerprint
                    previous = fingerprint
                    wiki_version = tuple(
                        sorted(
                            (str(path), path.stat().st_mtime_ns, path.stat().st_size)
                            for path in config.wiki_dir.rglob("*.md")
                            if not path.is_symlink() and ".git" not in path.parts
                        )
                    )
                    if (
                        (wiki_version or config.search_db_path.exists())
                        and wiki_version == previous_wiki
                        and wiki_version != scheduled_wiki
                    ):
                        if not Job.objects.filter(
                            kind="index", status__in=["queued", "running"]
                        ).exists():
                            enqueue_job("index", {})
                            scheduled_wiki = wiki_version
                    previous_wiki = wiki_version
                except Exception:
                    logger.exception("Unable to inspect watched inputs")
        finally:
            connections.close_all()

    try:
        for lane in ("ingest", "build"):
            processes[lane] = start(lane)
        thread = threading.Thread(target=supervise, daemon=True)
        thread.start()
        yield
    finally:
        stop.set()
        if thread is not None and thread.ident is not None:
            thread.join(timeout=10)
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + 10
        for process in processes.values():
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        # A killed attempt is classified on the next launch, under its lane lock.
