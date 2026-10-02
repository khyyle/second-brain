"""
Record of how a pipeline run ended.

The app and scheduled runs need to know how a run ended after the process
is gone. The file keeps one entry per stage, and a run replaces only its
own entry, so one stage's result does not hide another's. A failed write
never interrupts the run.
"""

from __future__ import annotations

import json
import logging
from enum import Enum
from pathlib import Path

from second_brain.status import now_iso

logger = logging.getLogger(__name__)

RUN_RECORD_FILENAME = ".last-run.json"


class StageOutcome(Enum):
    """How a single stage (ingest or compile) ended."""

    OK = "ok"
    PARTIAL = "partial"
    CAPPED = "capped"
    FAILED = "failed"
    STOPPED = "stopped"


_STAGE_EXIT_CODES: dict[StageOutcome, int] = {
    StageOutcome.OK: 0,
    StageOutcome.STOPPED: 0,
    StageOutcome.FAILED: 1,
    StageOutcome.PARTIAL: 2,
    StageOutcome.CAPPED: 2,
}


def exit_code_for(outcome: StageOutcome) -> int:
    """
    Process exit status for a stage outcome.

    Parameters
    ----------
    outcome: StageOutcome
        How the stage ended.

    Returns
    -------
    int
        ``0`` when the stage succeeded or the user stopped it, ``1`` when
        it failed, ``2`` when it was partial or stopped by the spend cap.
    """
    return _STAGE_EXIT_CODES[outcome]


def _read_record(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): value for key, value in payload.items()}


def record_run(
    data_dir: Path,
    stage: str,
    outcome: StageOutcome,
    reason: str,
    counts: dict[str, int],
    started_at: str,
) -> None:
    """
    Replace one stage's entry in the run record.

    Parameters
    ----------
    data_dir: Path
        Vault root containing the record file.
    stage: str
        Stage name (``"ingest"`` or ``"compile"``).
    outcome: StageOutcome
        How this stage ended.
    reason: str
        One plain sentence, or empty when there is nothing to explain.
    counts: dict[str, int]
        Per-stage counts (files for ingest, sources for compile).
    started_at: str
        ISO timestamp when this stage began.
    """
    path = data_dir / RUN_RECORD_FILENAME
    record = _read_record(path)
    record[stage] = {
        "outcome": outcome.value,
        "reason": reason,
        "counts": counts,
        "started_at": started_at,
        "finished_at": now_iso(),
    }
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(record), encoding="utf-8")
        temp_path.replace(path)
    except OSError as exc:
        logger.debug("Could not write run record: %s", exc)
