"""Holding folder under raw/ for skipped sources.

Skipped chats stay here so they can be restored until a fully successful
build finalizes the curation and clears the folder.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

SKIPPED_DIRNAME = ".skipped"


def purge_skipped(raw_dir: Path) -> None:
    """Permanently remove the skipped-source holding folder.

    Skip moves a source into ``raw/.skipped/`` so it can be un-skipped; a
    fully successful build finalizes those decisions, so the folder is
    cleared to reclaim disk. The skip verdicts stay in the manifest, so a later
    re-import of the same export is still recognized as skipped.

    Parameters
    ----------
    raw_dir: Path
        The raw output directory containing the holding folder.
    """
    skipped_dir = raw_dir / SKIPPED_DIRNAME
    if not skipped_dir.exists():
        return
    try:
        shutil.rmtree(skipped_dir)
    except OSError as e:
        logger.warning("Could not purge skipped folder: %s", e)


def move_skips_to_holding(raw_dir: Path, decisions: dict[str, str]) -> int:
    """Move skip-decision sources from ``raw/`` into the holding folder.

    Every path whose decision is ``skip`` and whose file still sits at
    ``raw_dir / rel`` is moved to ``raw_dir / .skipped / rel``, creating
    parent folders and replacing any file already at the destination.
    Missing sources are ignored so a prior move or delete is a no-op.

    Parameters
    ----------
    raw_dir: Path
        The raw output directory.
    decisions: dict[str, str]
        Map of relative raw path to triage decision.

    Returns
    -------
    int
        Number of files moved into the holding folder.
    """
    moved = 0
    for rel, decision in decisions.items():
        if decision != "skip":
            continue
        source = raw_dir / rel
        if not source.is_file():
            continue
        destination = raw_dir / SKIPPED_DIRNAME / rel
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                destination.unlink()
            shutil.move(str(source), str(destination))
            moved += 1
        except OSError as exc:
            logger.warning("Could not move skipped source %s: %s", rel, exc)
    return moved
