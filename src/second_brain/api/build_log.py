"""Append-only log of wiki build actions.

Each compile run appends one line per page created or updated, giving a
readable "created X / updated Y" history without parsing git.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

BUILD_LOG_FILENAME = ".build-log.jsonl"
# Cap the file so it can't grow unbounded; keep the most recent entries.
MAX_ENTRIES = 500


def append_build_actions(data_dir: Path, actions: list[dict]) -> None:
    """Append build actions to the log, trimming to the most recent entries.

    Parameters
    ----------
    data_dir: Path
        Vault root containing the log file.
    actions: list[dict]
        Each dict has ``action`` ("created" | "updated") and ``path``.
        A timestamp is added here.
    """
    if not actions:
        return
    path = data_dir / BUILD_LOG_FILENAME
    now = datetime.now(UTC).isoformat()
    lines = [json.dumps({**a, "at": now}) for a in actions]

    try:
        existing: list[str] = []
        if path.exists():
            existing = path.read_text(encoding="utf-8").splitlines()
        combined = existing + lines
        if len(combined) > MAX_ENTRIES:
            combined = combined[-MAX_ENTRIES:]
        path.write_text("\n".join(combined) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.debug("Could not write build log: %s", exc)
