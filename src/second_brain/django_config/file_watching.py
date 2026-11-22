"""Run source and wiki change detection for the backend lifetime."""

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from django.db import close_old_connections, connections

from second_brain.apps.ingestion.services.source_watching import SourceWatcher
from second_brain.apps.preferences.services.user_settings import get_pipeline_config
from second_brain.apps.wiki.services.index_watching import WikiIndexWatcher

logger = logging.getLogger(__name__)
SCAN_INTERVAL_SECONDS = 2
SHUTDOWN_TIMEOUT_SECONDS = 10


@contextmanager
def watch_vault_changes() -> Iterator[None]:
    """Scan sources and wiki files in a background thread until context exit.

    Each pass reads current preferences. Scan failures are logged and retried on
    the next pass, without preventing the other watcher from running. Database
    connections belong to the scanning thread and close when it finishes.

    Yields
    ------
    None
        Keeps scanning active. Context exit requests a stop and waits up to ten
        seconds for the current scan to finish.
    """
    stop = threading.Event()
    watchers = (SourceWatcher(), WikiIndexWatcher())

    def scan() -> None:
        try:
            while not stop.wait(SCAN_INTERVAL_SECONDS):
                close_old_connections()
                try:
                    config = get_pipeline_config()
                except Exception:
                    logger.exception("Unable to read preferences for file watching")
                    continue
                for watcher in watchers:
                    if stop.is_set():
                        break
                    try:
                        watcher.scan(config)
                    except Exception:
                        logger.exception("File scan failed in %s", type(watcher).__name__)
        finally:
            connections.close_all()

    # Directory scans can block on mounted volumes; process monitoring must stay responsive.
    thread = threading.Thread(target=scan, daemon=True)
    try:
        thread.start()
        yield
    finally:
        stop.set()
        if thread.ident is not None:
            thread.join(timeout=SHUTDOWN_TIMEOUT_SECONDS)
