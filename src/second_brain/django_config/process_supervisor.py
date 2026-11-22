"""Own the job and model subprocesses for the backend lifetime."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)
INFERENCE_RESTART_SECONDS = 30
MONITOR_INTERVAL_SECONDS = 2
SHUTDOWN_TIMEOUT_SECONDS = 10


@contextmanager
def supervised_processes() -> Iterator[None]:
    """Start job and inference workers; stop them when the launcher exits.

    Job workers restart after a crash. Model workers restart independently, at most
    once every thirty seconds. Context exit terminates children, then kills and
    reaps any that remain after the shared shutdown deadline.

    Yields
    ------
    None
        Keeps the child processes monitored until context exit.
    """
    stop = threading.Event()
    processes = {}
    restart_after: dict[str, float] = {}
    thread = None
    commands = {
        "ingest": ("second_brain.apps.jobs.worker", "ingest"),
        "build": ("second_brain.apps.jobs.worker", "build"),
        "generation": ("second_brain.apps.inference.worker", "generation"),
        "embedding": ("second_brain.apps.inference.worker", "embedding"),
    }

    def start(name: str) -> subprocess.Popen[bytes]:
        if name in ("generation", "embedding"):
            # Failed downloads and model loads must not cause a rapid restart loop.
            restart_after[name] = time.monotonic() + INFERENCE_RESTART_SECONDS
        return subprocess.Popen(
            [sys.executable, "-m", *commands[name]],
            env={**os.environ, "SECOND_BRAIN_WORKER_PARENT_PID": str(os.getpid())},
        )

    def supervise() -> None:
        while not stop.wait(MONITOR_INTERVAL_SECONDS):
            for name, process in list(processes.items()):
                if process.poll() is None or time.monotonic() < restart_after.get(name, 0):
                    continue
                logger.warning("Restarting %s worker after exit %s", name, process.returncode)
                try:
                    processes[name] = start(name)
                except OSError:
                    # Keep the exited process so a later pass can retry it.
                    logger.exception("Unable to restart %s worker", name)

    try:
        for name in commands:
            processes[name] = start(name)
        thread = threading.Thread(target=supervise, daemon=True)
        thread.start()
        yield
    finally:
        stop.set()
        if thread is not None and thread.ident is not None:
            thread.join(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + SHUTDOWN_TIMEOUT_SECONDS
        for process in processes.values():
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
