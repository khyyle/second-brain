"""Filesystem watcher using watchdog + batch processing for scheduled runs."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from second_brain.config import Config, SourceConfig
from second_brain.ingestion.manifest import Manifest

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".md", ".txt", ".tex", ".json"}
# File writes often arrive as multiple rapid events (create, modify, flush);
# debouncing ensures we process each file only once after it stabilizes
DEBOUNCE_SECONDS = 2.0


class IngestionEventHandler(FileSystemEventHandler):
    """Watches for new/modified files and queues them for debounced
    processing.

    Collects filesystem events and holds them for ``DEBOUNCE_SECONDS``
    before handing them off to the processing callback, ensuring each
    file is processed only once after writes stabilize.

    Parameters
    ----------
    source_config: SourceConfig
        Configuration for the source directory being watched.
    process_callback: Callable[[Path, str], None]
        Function invoked for each file that needs processing. Receives
        the file path and the source name.
    manifest: Manifest
        Manifest used to check whether a file still needs processing.
    """

    def __init__(
        self,
        source_config: SourceConfig,
        process_callback: Callable[[Path, str], None],
        manifest: Manifest,
    ) -> None:
        self._source_config = source_config
        self._process = process_callback
        self._manifest = manifest
        self._pending: dict[str, float] = {}
        self._source_name = ""

    def set_source_name(self, name: str) -> None:
        """Set the source label used for manifest tracking.

        Parameters
        ----------
        name: str
            Human-readable source name (e.g., ``"goodnotes"``).
        """
        self._source_name = name

    def _should_handle(self, path: Path) -> bool:
        """Check whether a path matches the configured file types.

        Parameters
        ----------
        path: Path
            Filesystem path from the event.

        Returns
        -------
        bool
            ``True`` if the file extension is both globally supported
            and enabled for this source.
        """
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return False
        allowed = {f".{ft}" for ft in self._source_config.file_types}
        return path.suffix.lower() in allowed

    def on_created(self, event: FileSystemEvent) -> None:
        """Record a newly created file for debounced processing.

        Parameters
        ----------
        event: FileSystemEvent
            Watchdog filesystem event.
        """
        if event.is_directory:
            return
        path = Path(event.src_path)
        if self._should_handle(path):
            self._pending[str(path)] = time.monotonic()

    def on_modified(self, event: FileSystemEvent) -> None:
        """Record a modified file for debounced processing.

        Parameters
        ----------
        event: FileSystemEvent
            Watchdog filesystem event.
        """
        if event.is_directory:
            return
        path = Path(event.src_path)
        if self._should_handle(path):
            self._pending[str(path)] = time.monotonic()

    def flush_pending(self) -> int:
        """Process files that have been stable for ``DEBOUNCE_SECONDS``.

        Returns
        -------
        int
            Number of files successfully processed in this flush.
        """
        now = time.monotonic()
        ready = [p for p, t in self._pending.items() if now - t >= DEBOUNCE_SECONDS]

        processed = 0
        for path_str in ready:
            del self._pending[path_str]
            path = Path(path_str)
            # Re-check needs_processing in case another handler already processed it
            if path.exists() and self._manifest.needs_processing(path):
                try:
                    self._process(path, self._source_name)
                    processed += 1
                except Exception:
                    logger.exception("Error processing %s", path)
        return processed


def watch_sources(
    config: Config,
    manifest: Manifest,
    process_callback: Callable[[Path, str], None],
    run_once: bool = False,
) -> None:
    """Start watching all configured source directories.

    In continuous mode, sets up watchdog observers with debounced event
    handling. In single-scan mode, performs one batch scan and exits.

    Parameters
    ----------
    config: Config
        Application configuration containing source definitions.
    manifest: Manifest
        Manifest for change-detection and status tracking.
    process_callback: Callable[[Path, str], None]
        Function to call for each file that needs processing.
    run_once: bool
        If ``True``, run a single batch scan instead of watching
        continuously.
    """
    if run_once:
        _batch_scan(config, manifest, process_callback)
        return

    observer = Observer()
    handlers: list[IngestionEventHandler] = []

    for name, source in config.sources.items():
        if not source.enabled:
            continue
        if not source.path.exists():
            logger.warning("Source path does not exist: %s (%s)", source.path, name)
            continue

        handler = IngestionEventHandler(source, process_callback, manifest)
        handler.set_source_name(name)
        handlers.append(handler)
        observer.schedule(handler, str(source.path), recursive=True)
        logger.info("Watching: %s → %s", name, source.path)

    observer.start()
    logger.info("Watcher started. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
            for h in handlers:
                h.flush_pending()
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def _is_drop_lane(path: Path, config: Config) -> bool:
    """Whether a source directory lives under the vault's ``drops/`` queue.

    Distinguishes the built-in drop folders, where the app copies files a
    user adds interactively, from user-registered watched folders elsewhere
    on disk.

    Parameters
    ----------
    path: Path
        A source directory to test.
    config: Config
        Application configuration, used for the ``drops/`` location.

    Returns
    -------
    bool
        ``True`` if ``path`` is inside ``config.drops_dir``.
    """
    try:
        path.resolve().relative_to(config.drops_dir.resolve())
        return True
    except ValueError:
        return False


def _batch_scan(
    config: Config,
    manifest: Manifest,
    process_callback: Callable[[Path, str], None],
    drops_only: bool = False,
) -> int:
    """Walk source directories and process new or changed files.

    Parameters
    ----------
    config: Config
        Application configuration containing source definitions.
    manifest: Manifest
        Manifest for change-detection.
    process_callback: Callable[[Path, str], None]
        Function to call for each file that needs processing.
    drops_only: bool, default=False
        When ``True``, scan only the drop-queue lanes and skip registered
        watched folders (used for interactive drops).

    Returns
    -------
    int
        Total number of files successfully processed.
    """
    from second_brain.status import clear_status, now_iso, write_status

    # Gather the work list up front so the heartbeat can report an
    # accurate "i of n" as files are processed.
    work: list[tuple[Path, str]] = []
    for name, source in config.sources.items():
        if not source.enabled or not source.path.exists():
            if source.enabled:
                logger.warning("Source path does not exist: %s (%s)", source.path, name)
            continue
        if drops_only and not _is_drop_lane(source.path, config):
            continue
        allowed = {f".{ft}" for ft in source.file_types}
        for file_path in source.path.rglob("*"):
            if file_path.is_dir() or file_path.suffix.lower() not in allowed:
                continue
            if manifest.needs_processing(file_path, config.raw_dir):
                work.append((file_path, name))

    total = len(work)
    processed = 0
    started = now_iso()
    if not work:
        clear_status(config.data_dir)
        return 0

    # A single file (e.g. a Claude-vision PDF) can take minutes; refresh the
    # heartbeat on a timer while it parses so progress doesn't go stale.
    progress = {"current": 0}
    stop = threading.Event()

    def _keepalive() -> None:
        while not stop.wait(5.0):
            write_status(
                config.data_dir,
                phase="ingest",
                current=progress["current"],
                total=total,
                started_at=started,
            )

    heartbeat = threading.Thread(target=_keepalive, daemon=True)
    heartbeat.start()
    try:
        for file_path, name in work:
            progress["current"] = processed
            write_status(
                config.data_dir,
                phase="ingest",
                current=processed,
                total=total,
                started_at=started,
            )
            try:
                process_callback(file_path, name)
                processed += 1
            except Exception:
                logger.exception("Error processing %s", file_path)
        progress["current"] = processed
    finally:
        stop.set()
        heartbeat.join(timeout=2.0)
        clear_status(config.data_dir)

    logger.info("Batch scan complete: %d files processed", processed)
    return processed


async def batch_scan_async(
    config: Config,
    manifest: Manifest,
    process_callback: Callable[[Path, str], None],
) -> int:
    """Async wrapper around :func:`_batch_scan` for pipeline integration.

    Parameters
    ----------
    config: Config
        Application configuration containing source definitions.
    manifest: Manifest
        Manifest for change-detection.
    process_callback: Callable[[Path, str], None]
        Function to call for each file that needs processing.

    Returns
    -------
    int
        Total number of files successfully processed.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _batch_scan, config, manifest, process_callback)
