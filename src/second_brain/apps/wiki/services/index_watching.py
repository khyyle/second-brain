"""Detect stable wiki changes and queue search-index refreshes."""

from second_brain.apps.jobs.models import Job
from second_brain.apps.jobs.services.job_queue import enqueue_job
from second_brain.pipeline_config import PipelineConfig

WikiFingerprint = tuple[tuple[str, int, int], ...]


class WikiIndexWatcher:
    """Track observed and queued wiki versions across scans in one thread."""

    def __init__(self) -> None:
        self._previous: WikiFingerprint | None = None
        self._scheduled: WikiFingerprint | None = None

    def scan(self, config: PipelineConfig) -> None:
        """Queue an index refresh after two matching scans of changed wiki files.

        Parameters
        ----------
        config: PipelineConfig
            Wiki directory and search database location.

        Returns
        -------
        None
            Records the observed version. Changes waiting on an active index job
            remain eligible on a later scan, including deletion of the last page.
        """
        version = tuple(
            sorted(
                (str(path), path.stat().st_mtime_ns, path.stat().st_size)
                for path in config.wiki_dir.rglob("*.md")
                if not path.is_symlink() and ".git" not in path.parts
            )
        )
        if (
            (version or config.search_db_path.exists())
            and version == self._previous
            and version != self._scheduled
        ):
            if not Job.objects.filter(kind="index", status__in=["queued", "running"]).exists():
                enqueue_job("index", {})
                self._scheduled = version
        self._previous = version
