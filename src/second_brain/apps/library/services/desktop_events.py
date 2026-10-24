"""Watch vault changes and stream complete desktop snapshots over SSE."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from asgiref.sync import sync_to_async
from django.db import connections
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from second_brain.apps.library.desktop_schemas import DesktopState
from second_brain.apps.library.services.desktop_state import get_desktop_state

logger = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 15
COALESCE_SECONDS = 0.5


class VaultChangeHandler(FileSystemEventHandler):
    """Notify on writes to vault state; ignore reads and unrelated paths."""

    def __init__(self, vault: Path, notify: Callable[[], None]) -> None:
        """Observe writes that can change the desktop snapshot.

        Parameters
        ----------
        vault: Path
            Vault root used to interpret filesystem event paths.
        notify: Callable[[], None]
            Callback invoked on the watchdog observer thread when a relevant path changes. It
            must be safe to call from that thread.

        Returns
        -------
        None
        """
        self.vault = vault
        self.notify = notify

    def relevant(self, path: str) -> bool:
        """Check whether a path belongs to the snapshot inputs.

        Parameters
        ----------
        path: str
            Filesystem path reported by watchdog; the path need not still exist.

        Returns
        -------
        bool
            True for source, import, wiki, database, or settings paths inside the vault. Git
            internals and unrelated paths return False.
        """
        try:
            parts = Path(path).relative_to(self.vault).parts
        except ValueError:
            return False
        if not parts or ".git" in parts:
            return False
        if parts[0] in {"raw", "drops", "wiki"}:
            return True
        return len(parts) == 1 and parts[0] in {
            "manifest.db",
            "manifest.db-wal",
            "config.yaml",
            ".clusters.json",
            ".cluster-overrides.json",
            ".build-log.jsonl",
        }

    def on_any_event(self, event: FileSystemEvent) -> None:
        """Notify once when a create, write, delete, or move affects snapshot inputs.

        Parameters
        ----------
        event: FileSystemEvent
            Filesystem notification. Moves check both the original and destination paths.
            Read events are ignored.

        Returns
        -------
        None
        """
        if event.event_type not in {"created", "modified", "deleted", "moved"}:
            return
        if self.relevant(event.src_path) or self.relevant(getattr(event, "dest_path", "")):
            self.notify()


def read_snapshot() -> tuple[str, str]:
    """Validate and serialize the current desktop state.

    Closes this thread's database connections on success or failure.

    Returns
    -------
    payload: str
        Complete snapshot encoded as compact JSON.
    signature: str
        Sorted JSON with generation and heartbeat timestamps removed, so timestamp-only
        changes compare equal. This is a comparison value, not a hash.
    """
    try:
        snapshot = DesktopState.model_validate(get_desktop_state()).model_dump(mode="json")
        payload = json.dumps(snapshot, separators=(",", ":"))
        # Timestamp-only changes do not count as state changes.
        snapshot["state"].pop("generated_at", None)
        for entry in snapshot["jobs"]:
            entry["job"].pop("heartbeat_at", None)
            entry["progress"].pop("updated_at", None)
        signature = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        return payload, signature
    finally:
        connections.close_all()


async def stream_desktop_state(vault: Path) -> AsyncIterator[str]:
    """Stream state changes and idle connection checks as Server-Sent Events.

    Reads an initial snapshot, then rereads after filesystem changes or an idle
    interval. Repeated snapshots are suppressed unless their comparison value
    changes or a previous read failed. Read failures are logged and produce one
    `unavailable` event until a read succeeds. Closing or cancelling the generator
    stops and joins its filesystem observer.

    Parameters
    ----------
    vault: Path
        Existing vault root to watch. It must match the vault selected in Django
        settings, which supplies the database and files read for each snapshot.

    Yields
    ------
    str
        A complete SSE frame: a `snapshot` event with JSON state, an `unavailable`
        event with an error detail, or `: keepalive` followed by a blank line.
        The colon marks an SSE comment: a line with no application data, sent after
        15 seconds without a filesystem notification to keep the connection active.

    Notes
    -----
    Filesystem notifications may be merged, lost, or delivered before a database
    commit. The idle check rereads state to catch these changes. One pending-change
    flag bounds memory use; a short delay combines bursts without waiting for all
    writes to stop.
    """
    loop = asyncio.get_running_loop()
    changed = asyncio.Event()
    handler = VaultChangeHandler(vault, lambda: loop.call_soon_threadsafe(changed.set))
    observer = Observer()
    observer.schedule(handler, str(vault), recursive=True)
    observer.start()
    previous = None
    failed = False
    try:
        while True:
            changed.clear()
            try:
                payload, signature = await sync_to_async(read_snapshot)()
                if failed or signature != previous:
                    yield f"event: snapshot\ndata: {payload}\n\n"
                    previous = signature
                failed = False
            except Exception:
                logger.exception("Could not refresh desktop event stream")
                if not failed:
                    yield (
                        'event: unavailable\ndata: {"detail":'
                        '"Could not refresh the library. Check backend logs."}\n\n'
                    )
                failed = True
            try:
                await asyncio.wait_for(changed.wait(), timeout=HEARTBEAT_SECONDS)
                # Throttle bursts without waiting indefinitely for a busy worker to quiet down.
                await asyncio.sleep(COALESCE_SECONDS)
            except TimeoutError:
                yield ": keepalive\n\n"
    finally:
        observer.stop()
        await asyncio.to_thread(observer.join)
