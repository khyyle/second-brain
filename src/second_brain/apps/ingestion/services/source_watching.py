"""Detect stable source changes and queue ingestion."""

from second_brain.apps.ingestion.services.source_staging import SUPPORTED_EXTENSIONS
from second_brain.apps.jobs.models import Job
from second_brain.apps.jobs.services.job_queue import enqueue_job
from second_brain.pipeline_config import PipelineConfig

SourceFingerprint = tuple[tuple[str, int, int], ...]


def source_fingerprint(config: PipelineConfig) -> SourceFingerprint:
    """Read supported source paths, modification times, and sizes.

    Parameters
    ----------
    config: PipelineConfig
        Drops directory, watched folders, and file-type filters.

    Returns
    -------
    SourceFingerprint
        Sorted metadata entries. Hidden paths, symlinks, and unsupported files
        are excluded. File contents are not read.
    """
    roots = [(config.drops_dir, None)] + [
        (source.path, set(source.file_types))
        for source in config.sources.values()
        if source.enabled
    ]
    entries = []
    for root, types in roots:
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
    return tuple(sorted(entries))


class SourceWatcher:
    """Track observed and queued source versions across scans in one thread."""

    def __init__(self) -> None:
        self._previous: tuple[str, SourceFingerprint] | None = None
        self._scheduled: tuple[str, SourceFingerprint] | None = None

    def scan(self, config: PipelineConfig) -> None:
        """Queue changed sources after two matching scans and an idle ingest lane.

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
        fingerprint = (
            config.model_dump_json(exclude={"raw_input_dir"}),
            source_fingerprint(config),
        )
        if fingerprint == self._previous and fingerprint != self._scheduled:
            if not Job.objects.filter(lane="ingest", status__in=["queued", "running"]).exists():
                if fingerprint[1]:
                    enqueue_job("ingest", {"drops_only": False})
                self._scheduled = fingerprint
        self._previous = fingerprint
