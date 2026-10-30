"""Capture watched inputs and process pending copies in one ingestion pass."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from second_brain.apps.ingestion.services.source_manifest import Manifest
from second_brain.apps.ingestion.services.source_staging import (
    SUPPORTED_EXTENSIONS,
    source_name,
    stage_watched_folders,
)
from second_brain.pipeline_config import PipelineConfig

logger = logging.getLogger(__name__)


def _is_drop_lane(path: Path, config: PipelineConfig) -> bool:
    """Whether a source directory lives under the vault's ``drops/`` queue.

    Distinguishes the built-in drop folders, where the app copies files a
    user adds interactively, from user-registered watched folders elsewhere
    on disk.

    Parameters
    ----------
    path: Path
        A source directory to test.
    config: PipelineConfig
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


def remove_drop_copy(file_path: Path, config: PipelineConfig) -> None:
    """Delete an ingested or duplicate copy from the drops queue.

    Only removes files inside the configured drops directory. Paths outside it,
    including watched-folder originals, are left untouched.

    Parameters
    ----------
    file_path: Path
        File that was ingested or recognized as already ingested.
    config: PipelineConfig
        Application configuration, used for the `drops/` location.

    Returns
    -------
    None
    """
    try:
        file_path.relative_to(config.drops_dir)
    except ValueError:
        return
    try:
        file_path.unlink()
    except OSError as exc:
        logger.warning("Could not remove drop copy %s: %s", file_path, exc)


def batch_scan(
    config: PipelineConfig,
    manifest: Manifest,
    process_callback: Callable[[Path, str], None],
    drops_only: bool = False,
    *,
    retry_failed: bool = False,
) -> int:
    """Walk source directories and process new or changed files.

    Parameters
    ----------
    config: PipelineConfig
        Application configuration containing source definitions.
    manifest: Manifest
        Manifest for change-detection.
    process_callback: Callable[[Path, str], None]
        Function to call for each file that needs processing.
    drops_only: bool, default=False
        When ``True``, scan only the drop-queue lanes and skip registered
        watched folders (used for interactive drops).
    retry_failed: bool
        Include unchanged failed copies in the scan.

    Returns
    -------
    int
        Total number of files successfully processed.
    """
    from second_brain.apps.jobs.services.job_progress import (
        finish_progress,
        now_iso,
        update_progress,
    )

    if not drops_only:
        stage_watched_folders(config, manifest, retry_failed=retry_failed)

    work: list[tuple[Path, str]] = []
    for file_path in sorted(config.drops_dir.rglob("*")):
        if (
            file_path.is_symlink()
            or not file_path.is_file()
            or file_path.suffix.lower() not in SUPPORTED_EXTENSIONS
        ):
            continue
        if any(part.startswith(".") for part in file_path.relative_to(config.drops_dir).parts):
            continue
        name = source_name(file_path, config)
        source = config.sources.get(name)
        if source is not None:
            if not source.enabled and _is_drop_lane(source.path, config):
                continue
            if file_path.suffix.lower().lstrip(".") not in source.file_types:
                continue
        if _prepare_queued_file(file_path, name, config, manifest, retry_failed=retry_failed):
            work.append((file_path, name))

    # Exports must be expanded before slow document parsers delay their chats.
    work.sort(key=lambda entry: (entry[0].suffix.lower() != ".json", str(entry[0])))
    total = len(work)
    processed = 0
    started = now_iso()
    if not work:
        finish_progress()
        return 0

    try:
        for file_path, name in work:
            update_progress(
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
    finally:
        finish_progress()

    logger.info("Batch scan complete: %d files processed", processed)
    return processed


def _prepare_queued_file(
    path: Path,
    name: str,
    config: PipelineConfig,
    manifest: Manifest,
    *,
    retry_failed: bool = False,
) -> bool:
    """Select queued files for processing and remove redundant copies."""
    entry = manifest.get_entry(path)
    if retry_failed and entry is not None and entry.status == "failed":
        return True
    if manifest.needs_processing(path, config.raw_dir):
        return True
    if entry is None or entry.status in ("pending", "complete", "duplicate"):
        manifest.mark_duplicate(path, name)
        remove_drop_copy(path, config)
    return False
