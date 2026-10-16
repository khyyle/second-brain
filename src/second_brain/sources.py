"""Operations on one source that cross every stage's records."""

from __future__ import annotations

from second_brain.compilation.store import forget_build
from second_brain.config import Config
from second_brain.db import connect, migrate
from second_brain.ingestion.manifest import forget_ingested
from second_brain.triage.store import forget_verdict


def forget_source(config: Config, raw_path: str) -> None:
    """
    Un-ingest a source: drop its ingestion, triage, and build records together.

    This clears only the database records. Deleting the raw markdown file on
    disk is the caller's responsibility.

    Parameters
    ----------
    config: Config
        Application configuration (database path).
    raw_path: str
        Path relative to the raw directory, e.g. ``"documents/notes.md"``.
    """
    migrate(config.manifest_db_path)
    with connect(config.manifest_db_path) as conn:
        forget_ingested(conn, raw_path)
        forget_verdict(conn, raw_path)
        forget_build(conn, raw_path)
