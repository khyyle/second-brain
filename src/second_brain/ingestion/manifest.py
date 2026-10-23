"""SQLite manifest for tracking file ingestion state and deduplication."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from second_brain.db import connect, migrate
from second_brain.hashing import hash_file
from second_brain.ingestion.triage.skipped import SKIPPED_DIRNAME

logger = logging.getLogger(__name__)


def raw_output_exists(raw_dir: Path, raw_output: str | None) -> bool:
    """Check for a parsed output, including sources held in the skipped folder.

    Parameters
    ----------
    raw_dir: Path
        Root of the parsed source tree.
    raw_output: str | None
        Output path recorded in the manifest.

    Returns
    -------
    bool
        Whether the parsed source is still available.
    """
    if not raw_output:
        return False
    return (raw_dir / raw_output).exists() or (raw_dir / SKIPPED_DIRNAME / raw_output).exists()


@dataclass(frozen=True)
class ManifestEntry:
    """Read-only snapshot of a manifest row for external consumption."""

    file_path: str
    sha256: str
    source_type: str
    status: str  # pending | processing | complete | failed
    parse_lane: str | None
    raw_output_path: str | None
    ingested_at: str
    updated_at: str
    content_hash: str | None = None
    origin_path: str | None = None
    origin_sha256: str | None = None


@dataclass(frozen=True)
class CachedPage:
    """A previously OCR'd page, keyed by the SHA-256 of its rendered PNG."""

    page_hash: str
    parse_lane: str
    raw_markdown: str
    parsed_at: str


class Manifest:
    """Tracks which source files have been ingested and their processing
    state.

    Uses a SQLite database to persist file hashes, statuses, and output
    locations across runs. Designed for single-process access with
    per-call connections to avoid stale state in long-running watchers.

    Parameters
    ----------
    db_path: Path
        Path to the SQLite database file. Created if it does not exist.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        migrate(db_path)

    def _conn(self) -> AbstractContextManager[sqlite3.Connection]:
        return connect(self._db_path)

    def needs_processing(self, file_path: Path, raw_dir: Path | None = None) -> bool:
        """Check whether a file is new, changed, or missing its parsed output.

        An unchanged failure waits for an explicit retry rather than repeating
        the same attempt on every scan.

        Parameters
        ----------
        file_path: Path
            File to check.
        raw_dir: Path | None
            Raw output root. When provided, a previously-completed file
            whose raw output no longer exists on disk is treated as needing
            re-processing, so deleting raw outputs (e.g. pruning in Finder)
            self-heals on the next re-upload.

        Returns
        -------
        bool
            ``True`` if the file should be (re-)processed.
        """
        current_hash = hash_file(file_path)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT sha256, status, raw_output FROM manifest WHERE file_path = ?",
                (str(file_path),),
            ).fetchone()
        if row is None:
            # New path. Skip if identical bytes were already ingested under
            # a different name (e.g. a re-dropped duplicate), so the same
            # document is never compiled twice.
            return not self._content_already_complete(current_hash, raw_dir)
        if row["sha256"] != current_hash:
            return True
        if row["status"] == "failed":
            return False
        if row["status"] == "pending":
            return not self._content_already_complete(current_hash, raw_dir)
        if row["status"] not in ("complete", "duplicate"):
            return True
        # Complete + unchanged: re-process only if its raw output is gone.
        if raw_dir is not None and not raw_output_exists(raw_dir, row["raw_output"]):
            return True
        return False

    def _content_already_complete(self, sha256: str, raw_dir: Path | None = None) -> bool:
        """Return ``True`` if a completed entry with this content hash still
        has its raw output on disk.

        Parameters
        ----------
        sha256: str
            Content hash to look for.
        raw_dir: Path | None
            Raw output root for existence checks. When None, any completed
            row counts (existence is not verified).

        Returns
        -------
        bool
            Whether identical, still-present content has already been ingested.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT raw_output FROM manifest WHERE sha256 = ? AND status = 'complete'",
                (sha256,),
            ).fetchall()
        if not rows:
            return False
        if raw_dir is None:
            return True
        return any(raw_output_exists(raw_dir, r["raw_output"]) for r in rows)

    def mark_processing(self, file_path: Path, source_type: str) -> None:
        """Record that a file is being processed (upsert on file_path).

        Uses INSERT ... ON CONFLICT to atomically handle both first-time
        ingestion and re-processing of changed or failed files.

        Parameters
        ----------
        file_path: Path
            File being processed.
        source_type: str
            Label for the source (e.g., ``"goodnotes"``).
        """
        now = datetime.now(UTC).isoformat()
        sha = hash_file(file_path)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO manifest
                   (file_path, sha256, source_type, status, ingested_at, updated_at)
                   VALUES (?, ?, ?, 'processing', ?, ?)
                   ON CONFLICT(file_path) DO UPDATE SET
                     sha256 = excluded.sha256,
                     status = 'processing',
                     error = NULL,
                     updated_at = excluded.updated_at""",
                (str(file_path), sha, source_type, now, now),
            )

    def mark_complete(
        self,
        file_path: Path,
        parse_lane: str | None = None,
        raw_output: str | None = None,
        content_hash: str | None = None,
    ) -> None:
        """Mark a file as successfully ingested and record its output.

        Parameters
        ----------
        file_path: Path
            File that was ingested.
        parse_lane: str | None
            Parser lane used (e.g., ``"docling"``).
        raw_output: str | None
            Path to the raw markdown output file.
        content_hash: str | None
            SHA-256 of the assembled post-OCR markdown. When this matches
            the previously stored value, callers can skip downstream
            compilation work even though the source file hash has changed.
        """
        now = datetime.now(UTC).isoformat()
        with self._conn() as conn:
            conn.execute(
                """UPDATE manifest
                   SET status = 'complete',
                       parse_lane = ?,
                       raw_output = ?,
                       content_hash = COALESCE(?, content_hash),
                       updated_at = ?
                   WHERE file_path = ?""",
                (parse_lane, raw_output, content_hash, now, str(file_path)),
            )

    def mark_duplicate(self, file_path: Path, source_type: str) -> None:
        """Record a file whose content was already ingested under another path.

        Existing terminal records are preserved. A pending managed copy can
        become a duplicate while retaining its original-file association.

        Parameters
        ----------
        file_path: Path
            The duplicate file.
        source_type: str
            Label for the source lane it arrived through.
        """
        with self._conn() as conn:
            exists = conn.execute(
                "SELECT status FROM manifest WHERE file_path = ?", (str(file_path),)
            ).fetchone()
        if exists and exists["status"] != "pending":
            return
        sha = hash_file(file_path)
        now = datetime.now(UTC).isoformat()
        with self._conn() as conn:
            original = conn.execute(
                "SELECT raw_output FROM manifest WHERE sha256 = ? AND status = 'complete'",
                (sha,),
            ).fetchone()
            conn.execute(
                """INSERT INTO manifest
                   (file_path, sha256, source_type, status, raw_output,
                    ingested_at, updated_at)
                   VALUES (?, ?, ?, 'duplicate', ?, ?, ?)
                   ON CONFLICT(file_path) DO UPDATE SET
                     status = 'duplicate', raw_output = excluded.raw_output,
                     updated_at = excluded.updated_at""",
                (
                    str(file_path),
                    sha,
                    source_type,
                    original["raw_output"] if original else None,
                    now,
                    now,
                ),
            )

    def mark_failed(self, file_path: Path, error: str | None = None) -> None:
        """Record a failure that waits for a retry or changed file content.

        Parameters
        ----------
        file_path: Path
            File that failed processing.
        error: str | None
            One-sentence reason shown to the user, or None if unknown.
        """
        now = datetime.now(UTC).isoformat()
        with self._conn() as conn:
            conn.execute(
                """UPDATE manifest
                   SET status = 'failed', error = ?, updated_at = ?
                   WHERE file_path = ?""",
                (error, now, str(file_path)),
            )
        if error:
            logger.error("Ingestion failed for %s: %s", file_path, error)

    def get_ingest_failures(self) -> dict[str, str]:
        """Return each failed file that has a recorded reason.

        Returns
        -------
        dict[str, str]
            Absolute file path mapped to why its last ingest failed.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT file_path, error FROM manifest "
                "WHERE status = 'failed' AND error IS NOT NULL"
            ).fetchall()
        return {r["file_path"]: r["error"] for r in rows}

    def get_entry(self, file_path: Path) -> ManifestEntry | None:
        """Look up a single file's manifest record.

        Parameters
        ----------
        file_path: Path
            File to look up.

        Returns
        -------
        ManifestEntry | None
            The entry if tracked, otherwise ``None``.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM manifest WHERE file_path = ?", (str(file_path),)
            ).fetchone()
        if row is None:
            return None
        return _row_to_entry(row)

    def get_origin_copy(self, origin_path: Path) -> ManifestEntry | None:
        """Find the managed copy of a watched original.

        Parameters
        ----------
        origin_path: Path
            Absolute path of the watched original.

        Returns
        -------
        ManifestEntry | None
            The copy's current ingestion record, if one exists.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM manifest WHERE origin_path = ?", (str(origin_path),)
            ).fetchone()
        return _row_to_entry(row) if row is not None else None

    def record_pending_copy(self, file_path: Path, origin_path: Path, source_type: str) -> None:
        """Record a copied source before it enters parsing.

        The original's captured hash remains independent of later edits to the
        managed copy, so repairing that copy does not trigger a stale recopy.

        Parameters
        ----------
        file_path: Path
            The managed input under drops/.
        origin_path: Path
            Original file from which this version was copied.
        source_type: str
            Source folder's name and parsing settings.
        """
        previous = self.get_entry(file_path) or self.get_entry(origin_path)
        sha256 = hash_file(file_path)
        now = datetime.now(UTC).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO manifest
                   (file_path, sha256, source_type, status, ingested_at, updated_at,
                    origin_path, origin_sha256, parse_lane, raw_output, content_hash)
                   VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(file_path) DO UPDATE SET
                     sha256 = excluded.sha256, source_type = excluded.source_type,
                     status = 'pending', error = NULL, updated_at = excluded.updated_at,
                     origin_path = excluded.origin_path, origin_sha256 = excluded.origin_sha256""",
                (
                    str(file_path),
                    sha256,
                    source_type,
                    now,
                    now,
                    str(origin_path),
                    sha256,
                    previous.parse_lane if previous else None,
                    previous.raw_output_path if previous else None,
                    previous.content_hash if previous else None,
                ),
            )
            conn.execute("DELETE FROM manifest WHERE file_path = ?", (str(origin_path),))

    def source_stems(self, source_type: str) -> set[str]:
        """Return source names reserved by manifest records.

        Parameters
        ----------
        source_type: str
            Source lane whose input names must not be reused.

        Returns
        -------
        set[str]
            Case-insensitive stems, including copies whose parsed output is gone.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT file_path FROM manifest WHERE source_type = ?", (source_type,)
            ).fetchall()
        return {Path(row["file_path"]).stem.casefold() for row in rows}

    def get_content_hash(self, file_path: Path) -> str | None:
        """Return the post-OCR content hash recorded for a file, if any.

        Parameters
        ----------
        file_path: Path
            File to look up.

        Returns
        -------
        str | None
            The previously stored assembled-markdown SHA-256, or
            ``None`` when the file has never been processed or was
            stored before content-hash tracking existed.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT content_hash FROM manifest WHERE file_path = ?",
                (str(file_path),),
            ).fetchone()
        if row is None:
            return None
        return row["content_hash"]

    def remove_entries(self, file_paths: list[Path]) -> int:
        """Delete manifest rows for the given source paths.

        Used when source files are removed (e.g. de-duplication cleanup)
        so the manifest does not retain rows for files that no longer
        exist.

        Parameters
        ----------
        file_paths: list[Path]
            Source paths whose rows should be deleted.

        Returns
        -------
        int
            Number of rows deleted.
        """
        if not file_paths:
            return 0
        deleted = 0
        with self._conn() as conn:
            for p in file_paths:
                cur = conn.execute("DELETE FROM manifest WHERE file_path = ?", (str(p),))
                deleted += cur.rowcount
        return deleted

    def count_by_status(self) -> dict[str, int]:
        """Aggregate manifest entries by status.

        Returns
        -------
        dict[str, int]
            Mapping of status label to count.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM manifest GROUP BY status"
            ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    def get_cached_page(self, page_hash: str) -> CachedPage | None:
        """Look up a previously OCR'd page by its rendered-PNG hash.

        Parameters
        ----------
        page_hash: str
            SHA-256 of the page's rendered PNG bytes at ``RENDER_DPI``.

        Returns
        -------
        CachedPage | None
            The cached parse output for this page, or ``None`` on miss.
        """
        with self._conn() as conn:
            row = conn.execute(
                """SELECT page_hash, parse_lane, raw_markdown, parsed_at
                   FROM page_cache WHERE page_hash = ?""",
                (page_hash,),
            ).fetchone()
        if row is None:
            return None
        return CachedPage(
            page_hash=row["page_hash"],
            parse_lane=row["parse_lane"],
            raw_markdown=row["raw_markdown"],
            parsed_at=row["parsed_at"],
        )

    def put_cached_page(
        self,
        page_hash: str,
        parse_lane: str,
        raw_markdown: str,
    ) -> None:
        """Store a freshly OCR'd page so future occurrences hit the cache.

        Parameters
        ----------
        page_hash: str
            SHA-256 of the page's rendered PNG bytes.
        parse_lane: str
            Parser used for this page (e.g. ``"chandra"``).
        raw_markdown: str
            Markdown extracted by the parser for this single page.
        """
        now = datetime.now(UTC).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO page_cache
                   (page_hash, parse_lane, raw_markdown, parsed_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(page_hash) DO UPDATE SET
                     parse_lane = excluded.parse_lane,
                     raw_markdown = excluded.raw_markdown,
                     parsed_at = excluded.parsed_at""",
                (page_hash, parse_lane, raw_markdown, now),
            )

    def page_cache_size(self) -> int:
        """Return the number of cached pages, for diagnostics."""
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM page_cache").fetchone()
        return int(row["cnt"]) if row else 0


def _row_to_entry(row: sqlite3.Row) -> ManifestEntry:
    """Convert a SQLite manifest row to a :class:`ManifestEntry`."""
    return ManifestEntry(
        file_path=row["file_path"],
        sha256=row["sha256"],
        source_type=row["source_type"],
        status=row["status"],
        parse_lane=row["parse_lane"],
        raw_output_path=row["raw_output"],
        ingested_at=row["ingested_at"],
        updated_at=row["updated_at"],
        content_hash=row["content_hash"],
        origin_path=row["origin_path"],
        origin_sha256=row["origin_sha256"],
    )


def forget_ingested(conn: sqlite3.Connection, raw_output: str) -> None:
    """
    Delete the manifest rows that produced a raw source, on the caller's connection.

    Parameters
    ----------
    conn: sqlite3.Connection
        Open connection whose transaction the caller commits.
    raw_output: str
        Raw output path relative to the raw directory.
    """
    conn.execute("DELETE FROM manifest WHERE raw_output = ?", (raw_output,))
