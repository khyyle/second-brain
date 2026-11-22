"""Detect stable source changes and queue ingestion."""

from second_brain.apps.ingestion.services.source_staging import SUPPORTED_EXTENSIONS
from second_brain.apps.jobs.services.automatic_jobs import schedule_for_file_change
from second_brain.pipeline_config import PipelineConfig

FileVersions = tuple[tuple[str, int, int], ...]


def source_fingerprint(config: PipelineConfig) -> tuple[FileVersions, FileVersions]:
    """Read supported source paths, modification times, and sizes.

    Parameters
    ----------
    config: PipelineConfig
        Drops directory, watched folders, and file-type filters.

    Returns
    -------
    tuple[FileVersions, FileVersions]
        Sorted versions of watched originals and disposable drops, respectively.
        Hidden paths, symlinks, and unsupported files are excluded.
    """
    roots = [(config.drops_dir, None)] + [
        (source.path, set(source.file_types))
        for source in config.sources.values()
        if source.enabled
    ]
    originals = []
    drops = []
    for root, types in roots:
        entries = drops if root == config.drops_dir else originals
        if not root.is_dir() or root.is_symlink():
            continue
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if any(part.startswith(".") for part in relative.parts) or path.is_symlink():
                continue
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            if types is not None and path.suffix.lower().lstrip(".") not in types:
                continue
            try:
                stat = path.stat()
                if path.is_file():
                    entries.append((str(path), stat.st_mtime_ns, stat.st_size))
            except FileNotFoundError:
                continue
    return tuple(sorted(originals)), tuple(sorted(drops))


class SourceWatcher:
    """Track observed and queued source versions across scans in one thread."""

    def __init__(self) -> None:
        self._previous: tuple[str, FileVersions, FileVersions] | None = None
        self._scheduled: tuple[str, FileVersions, FileVersions] | None = None

    def scan(self, config: PipelineConfig) -> None:
        """Queue stable input changes when no ingest job is active.

        Parameters
        ----------
        config: PipelineConfig
            Current source locations and preferences. Preference changes also
            count as a new version to ingest.

        Returns
        -------
        None
            Records the observed version. A version waiting on an active ingest
            job remains eligible on a later scan.
        """
        preferences = config.model_dump_json(exclude={"raw_input_dir"})
        originals, drops = source_fingerprint(config)
        fingerprint = (preferences, originals, drops)
        if fingerprint == self._previous and fingerprint != self._scheduled:
            outcome = "covered"
            if originals or drops:
                outcome = schedule_for_file_change(
                    "ingest", observed_inputs=(preferences, originals), drop_versions=drops
                )
            if outcome != "busy":
                self._scheduled = fingerprint
        self._previous = fingerprint
