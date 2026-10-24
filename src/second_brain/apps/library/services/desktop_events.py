"""Stream desktop snapshots when vault data changes, including worker-process writes.

Watchdog uses OS filesystem notifications. It observes the database/WAL and the
screen's file inputs across processes. Notifications are hints: coalesce bursts, reread
authoritative state, and always send a fresh snapshot on connection/reconnection.
"""

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
    """Wake the async stream for relevant writes; reads and lock files do not count."""

    def __init__(self, vault: Path, notify: Callable[[], None]) -> None:
        self.vault = vault
        self.notify = notify

    def relevant(self, path: str) -> bool:
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
        if event.event_type not in {"created", "modified", "deleted", "moved"}:
            return
        if self.relevant(event.src_path) or self.relevant(getattr(event, "dest_path", "")):
            self.notify()


def read_snapshot() -> tuple[str, str]:
    """Serialize in a sync ORM context and release DB connections between events."""
    try:
        snapshot = DesktopState.model_validate(get_desktop_state()).model_dump(mode="json")
        payload = json.dumps(snapshot, separators=(",", ":"))
        # Clock-only differences should not repaint the screen or send another event.
        snapshot["state"].pop("generated_at", None)
        for entry in snapshot["jobs"]:
            entry["job"].pop("heartbeat_at", None)
            entry["progress"].pop("updated_at", None)
        signature = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        return payload, signature
    finally:
        connections.close_all()


async def stream_desktop_state(vault: Path) -> AsyncIterator[str]:
    """Send SSE snapshots, keepalives, and recoverable errors until disconnected.

    The queue is a single dirty flag, so slow clients cannot accumulate events.
    Every keepalive also reconciles state: filesystem notifications can be merged
    or lost, or arrive before a SQLite transaction commits. This low-frequency
    safety check sends no snapshot unless visible data changed. Each snapshot
    replaces the whole desktop read model, including after reconnection.
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
