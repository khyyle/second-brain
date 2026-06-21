"""
Pipeline status heartbeat.

A pipeline run is a separate process, so it writes a small JSON heartbeat
that an external reader can poll for live progress (phase, items done,
elapsed, accumulated cost). Best-effort: a failed write never interrupts
the run.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

STATUS_FILENAME = ".status.json"
STOP_FILENAME = ".stop"


@dataclass(frozen=True)
class PipelineStatus:
    """A snapshot of pipeline progress written to the heartbeat file."""
    running: bool
    phase: str  # "ingest" | "triage" | "compile" | "idle"
    current: int
    total: int
    updated_at: str
    started_at: str  # run start, for elapsed display
    cost_usd: float  # accumulated API cost this run (0 for free stages)


def now_iso() -> str:
    """Current UTC time as an ISO string (run-start marker for elapsed)."""
    return datetime.now(UTC).isoformat()


def _status_path(data_dir: Path) -> Path:
    return data_dir / STATUS_FILENAME


def write_status(
    data_dir: Path,
    phase: str,
    current: int,
    total: int,
    started_at: str,
    cost_usd: float = 0.0,
) -> None:
    """
    Write a running heartbeat snapshot.

    Parameters
    ----------
    data_dir: Path
        Vault root containing the status file.
    phase: str
        Current stage label (``"ingest"``, ``"triage"``, ``"compile"``).
    current: int
        Items processed so far.
    total: int
        Total items in this run (0 when not a per-item stage).
    started_at: str
        ISO timestamp when this run began (for elapsed display).
    cost_usd: float
        Accumulated API cost so far this run.
    """
    status = PipelineStatus(
        running=True,
        phase=phase,
        current=current,
        total=total,
        updated_at=now_iso(),
        started_at=started_at,
        cost_usd=cost_usd,
    )
    _write(data_dir, status)


def touch_status(data_dir: Path) -> None:
    """Refresh ``updated_at`` on the current heartbeat without changing its
    values.

    A long blocking step (e.g. one Claude API call) writes no heartbeat, so
    a poller could treat the run as stale. A keepalive timer calls this
    between real updates. No-op if there is no active run on disk.
    """
    path = _status_path(data_dir)
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not snapshot.get("running"):
        return
    snapshot["updated_at"] = now_iso()
    try:
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(snapshot), encoding="utf-8")
        temp_path.replace(path)
    except OSError as exc:
        logger.debug("Could not touch status heartbeat: %s", exc)


def clear_status(data_dir: Path) -> None:
    """Mark the pipeline idle (run finished or never started)."""
    now = now_iso()
    status = PipelineStatus(
        running=False,
        phase="idle",
        current=0,
        total=0,
        updated_at=now,
        started_at=now,
        cost_usd=0.0,
    )
    _write(data_dir, status)


def _stop_path(data_dir: Path) -> Path:
    return data_dir / STOP_FILENAME


def request_stop(data_dir: Path) -> None:
    """Ask a running compile to halt at its next checkpoint.

    Co-operative cancellation: the compiler checks for this flag between
    sources and between agent turns, so it stops cleanly without orphaned
    child processes or half-written pages.
    """
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        _stop_path(data_dir).write_text(now_iso(), encoding="utf-8")
    except OSError as exc:
        logger.debug("Could not write stop flag: %s", exc)


def stop_requested(data_dir: Path) -> bool:
    """Whether a stop has been requested for the current run."""
    return _stop_path(data_dir).exists()


def clear_stop(data_dir: Path) -> None:
    """Remove the stop flag (called at the start and end of a run)."""
    try:
        _stop_path(data_dir).unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("Could not clear stop flag: %s", exc)


def _write(data_dir: Path, status: PipelineStatus) -> None:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        path = _status_path(data_dir)
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(asdict(status)), encoding="utf-8")
        temp_path.replace(path)
    except OSError as exc:
        logger.debug("Could not write status heartbeat: %s", exc)
