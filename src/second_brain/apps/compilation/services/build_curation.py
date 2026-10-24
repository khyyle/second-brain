"""Finalize skipped sources and grouping only while captured curation is unchanged."""

import logging

from second_brain.apps.compilation.services.clustering.preview import (
    CLUSTERS_FILENAME,
    OVERRIDES_FILENAME,
    clear_preview,
)
from second_brain.apps.ingestion.services.triage.skipped import purge_skipped
from second_brain.django_config.errors import ConflictError
from second_brain.django_config.paths import get_vault_paths
from second_brain.django_config.vault_storage import vault_lock

logger = logging.getLogger(__name__)


def capture_curation_version() -> list[list[str | int]]:
    """Record raw-file and grouping versions under the raw-sources/grouping locks.

    Includes skipped files. Inode and ctime detect replacements and skip/restore
    moves even when the bytes or modification time remain unchanged.
    """
    paths = get_vault_paths()
    files = list(paths.raw_dir.rglob("*.md")) + [
        paths.data_dir / CLUSTERS_FILENAME,
        paths.data_dir / OVERRIDES_FILENAME,
    ]
    versions = []
    for path in sorted(files):
        try:
            stat = path.lstat()
        except FileNotFoundError:
            continue
        versions.append(
            [
                str(path.relative_to(paths.data_dir)),
                stat.st_ino,
                stat.st_ctime_ns,
                stat.st_mtime_ns,
                stat.st_size,
            ]
        )
    return versions


def finalize_build_curation(captured_version: list | None) -> None:
    """Clear completed curation if no import or user edit has changed it since submission.

    Call only after a complete, successful build. An active ingestion pass or a
    changed source/plan defers cleanup until a later build. Cleanup failure does
    not change the outcome of already committed wiki work.
    """
    if captured_version is None:
        return
    paths = get_vault_paths()
    try:
        with (
            vault_lock("imports", blocking=False),
            vault_lock("raw-sources", blocking=False),
            vault_lock("grouping", blocking=False),
        ):
            if captured_version != capture_curation_version():
                return
            purge_skipped(paths.raw_dir)
            clear_preview(paths.data_dir)
    except ConflictError:
        return
    except OSError:
        logger.exception("Unable to finalize completed build curation")
