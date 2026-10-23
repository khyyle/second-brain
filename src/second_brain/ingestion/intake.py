"""Copy watched inputs into the same managed queue as interactive drops."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from second_brain.config import Config
from second_brain.hashing import hash_file
from second_brain.ingestion.manifest import Manifest, ManifestEntry, raw_output_exists

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".md", ".txt", ".tex", ".json"}


def source_name(file_path: Path, config: Config) -> str:
    """Find the source lane for a queued copy or watched original.

    Parameters
    ----------
    file_path: Path
        Absolute input path.
    config: Config
        Vault location and registered source folders.

    Returns
    -------
    str
        Source name, falling back to the documents lane for explicit imports.
    """
    if file_path.is_relative_to(config.drops_dir):
        parts = file_path.relative_to(config.drops_dir).parts
        if len(parts) > 1:
            return parts[0]
    for name, source in config.sources.items():
        if file_path.is_relative_to(source.path):
            return name
    return "documents"


def stage_watched_file(
    origin: Path, name: str, config: Config, manifest: Manifest, *, force: bool = False
) -> Path | None:
    """Copy a new or changed source version into drops/.

    Parameters
    ----------
    origin: Path
        Absolute original path outside the drop queue.
    name: str
        Source lane and parsing profile.
    config: Config
        Vault paths.
    manifest: Manifest
        Ingestion records linking copies to their originals.
    force: bool
        Copy again for an explicit import or retry, even when unchanged.

    Returns
    -------
    Path | None
        Managed copy to parse, or None when this version was already captured.
    """
    previous = manifest.get_origin_copy(origin)
    if previous is not None and not force and previous.origin_sha256 == hash_file(origin):
        if previous.status == "failed" or Path(previous.file_path).exists():
            return None
        if previous.status in ("complete", "duplicate") and raw_output_exists(
            config.raw_dir, previous.raw_output_path
        ):
            return None
    legacy = manifest.get_entry(origin)
    if (
        previous is None
        and legacy is not None
        and legacy.status in ("complete", "duplicate")
        and not force
        and not manifest.needs_processing(origin, config.raw_dir)
    ):
        return None

    destination = (
        Path(previous.file_path)
        if previous is not None
        else _copy_destination(origin, name, config, manifest, legacy)
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".copy-", dir=destination.parent) as temporary:
        copied = Path(temporary) / origin.name
        shutil.copy2(origin, copied)
        copied.replace(destination)
    manifest.record_pending_copy(destination, origin, name)
    return destination


def _copy_destination(
    origin: Path, name: str, config: Config, manifest: Manifest, legacy: ManifestEntry | None
) -> Path:
    destination = config.drops_dir / name
    if legacy is not None and legacy.raw_output_path and origin.suffix.lower() != ".json":
        output = Path(legacy.raw_output_path)
        candidate = destination / f"{output.stem}{origin.suffix}"
        if (
            output.parent == Path(name)
            and not candidate.exists()
            and manifest.get_entry(candidate) is None
        ):
            return candidate

    occupied = manifest.source_stems(name)
    for directory in (destination, config.raw_dir / name, config.raw_dir / ".skipped" / name):
        if directory.exists():
            occupied.update(path.stem.casefold() for path in directory.iterdir())
    candidate = destination / origin.name
    suffix = 2
    while candidate.stem.casefold() in occupied:
        candidate = destination / f"{origin.stem}-{suffix}{origin.suffix}"
        suffix += 1
    return candidate


def stage_watched_folders(config: Config, manifest: Manifest) -> None:
    """Collect watched files before parsing any member of the queue.

    Parameters
    ----------
    config: Config
        Source folders, their enabled state, and accepted extensions.
    manifest: Manifest
        Captured source versions.
    """
    for name, source in config.sources.items():
        if not source.enabled:
            continue
        if not source.path.exists():
            logger.warning("Source path does not exist: %s (%s)", source.path, name)
            continue
        if source.path.is_relative_to(config.drops_dir):
            continue
        allowed = {f".{extension}" for extension in source.file_types} & SUPPORTED_EXTENSIONS
        for origin in sorted(source.path.rglob("*")):
            if not origin.is_file() or origin.suffix.lower() not in allowed:
                continue
            if origin.is_relative_to(config.drops_dir):
                continue
            if any(part.startswith(".") for part in origin.relative_to(source.path).parts):
                continue
            stage_watched_file(origin, name, config, manifest)
