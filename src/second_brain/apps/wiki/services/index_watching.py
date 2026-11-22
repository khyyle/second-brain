"""Detect stable wiki changes and queue search-index refreshes."""

from pathlib import Path

from second_brain.apps.jobs.services.automatic_jobs import (
    ModelReadyTrigger,
    schedule_for_file_change,
)
from second_brain.apps.wiki.services.search_index import SearchIndex
from second_brain.pipeline_config import PipelineConfig

# (path, last_modified_time, size_in_bytes)
WikiFingerprint = tuple[tuple[str, int, int], ...]


def wiki_fingerprint(wiki_dir: Path) -> WikiFingerprint:
    """Return sorted paths, modification times in nanoseconds, and sizes of wiki Markdown."""
    files = []
    for path in wiki_dir.rglob("*.md"):
        if path.is_symlink() or ".git" in path.parts:
            continue
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        files.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(files))


class WikiIndexWatcher:
    """Track observed and queued wiki versions across scans in one thread."""

    def __init__(self) -> None:
        self._previous: WikiFingerprint | None = None
        self._scheduled: WikiFingerprint | None = None
        self._embedding = ModelReadyTrigger("embedding")

    def scan(self, config: PipelineConfig) -> None:
        """Queue stable wiki edits or a missing index; resume pending vectors on model readiness.

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
        version = wiki_fingerprint(config.wiki_dir)
        index_exists = config.search_db_path.is_file()
        if (version or index_exists) and version == self._previous and version != self._scheduled:
            # Job history survives removal of search.db. Rebuild it even while offline,
            # but keep the wiki version so creating the DB isn't another change.
            outcome = schedule_for_file_change(
                "index", observed_inputs=version, missing_index=not index_exists
            )
            if outcome != "busy":
                self._scheduled = version
        self._previous = version
        if self._embedding.schedule_pending(
            lambda: index_exists and SearchIndex(config.search_db_path).has_pending_embeddings(),
            wiki_version=version,
        ):
            self._scheduled = version
