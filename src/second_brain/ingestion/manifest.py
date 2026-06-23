"""SQLite manifest for tracking file ingestion state and deduplication."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# 64 KB read buffer to match typical OS page size for efficient disk I/O
BUF_SIZE = 65536

# Triage-table decision for a source too large for the current model's context
# window. Distinct from the triage verdicts (worthwhile/review/skip) and
# re-evaluated each build, so it self-heals once a larger-window model is chosen.
DEFERRED_DECISION = "deferred"


def _raw_output_exists(raw_dir: Path, raw_output: str | None) -> bool:
    """Whether a manifest row's raw output file is still present on disk."""
    return bool(raw_output) and (raw_dir / raw_output).exists()


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
        self._init_db()

    def _init_db(self) -> None:
        """Create the manifest schema and apply any column-additive migrations."""
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS manifest (
                    file_path    TEXT PRIMARY KEY,
                    sha256       TEXT NOT NULL,
                    source_type  TEXT NOT NULL,
                    status       TEXT NOT NULL DEFAULT 'pending',
                    parse_lane   TEXT,
                    raw_output   TEXT,
                    ingested_at  TEXT NOT NULL,
                    updated_at   TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_manifest_status
                ON manifest(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_manifest_hash
                ON manifest(sha256)
            """)
            # Per-page OCR cache lets us skip re-OCRing pages whose rendered
            # bytes are unchanged across full-file re-exports (the Goodnotes
            # case: edit one page, the whole PDF binary changes).
            self._migrate_drop_page_cache_confidence(conn)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS page_cache (
                    page_hash    TEXT PRIMARY KEY,
                    parse_lane   TEXT NOT NULL,
                    raw_markdown TEXT NOT NULL,
                    parsed_at    TEXT NOT NULL
                )
            """)
            # Triage decisions keyed by the raw source path, so review/skip
            # verdicts persist and aren't re-evaluated on every compile run.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS triage (
                    raw_path    TEXT PRIMARY KEY,
                    decision    TEXT NOT NULL,
                    confidence  REAL NOT NULL,
                    reason      TEXT,
                    triaged_at  TEXT NOT NULL
                )
            """)
            # Which raw files the compilation agent has consumed into the
            # wiki. Tracked separately from ingestion as a raw file being
            # produced (ingested) is not the same as it being compiled.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS compiled (
                    raw_path     TEXT PRIMARY KEY,
                    compiled_at  TEXT NOT NULL
                )
            """)
            self._migrate_add_content_hash(conn)

    @staticmethod
    def _migrate_add_content_hash(conn: sqlite3.Connection) -> None:
        """Add `manifest.content_hash` to pre-existing databases.

        SQLite has no `ADD COLUMN IF NOT EXISTS`, so we inspect the
        current schema and add the column only when absent.
        """
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(manifest)")}
        if "content_hash" not in cols:
            conn.execute("ALTER TABLE manifest ADD COLUMN content_hash TEXT")

    @staticmethod
    def _migrate_drop_page_cache_confidence(conn: sqlite3.Connection) -> None:
        """Drop the legacy `page_cache.confidence` column.

        The page cache is disposable (re-derivable by re-OCRing), so the
        simplest migration off the old schema is to drop the table and let
        it rebuild without the column.
        """
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(page_cache)")}
        if "confidence" in cols:
            conn.execute("DROP TABLE page_cache")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection that auto-commits on clean exit.

        Opens a fresh connection each call rather than holding one
        open — SQLite handles file-level locking, and this avoids stale
        state across long-running watcher sessions.

        Yields
        ------
        sqlite3.Connection
            A connection with ``Row`` row factory configured.
        """
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def compute_hash(file_path: Path) -> str:
        """Compute SHA-256 of a file's contents for change detection.

        Parameters
        ----------
        file_path: Path
            File to hash.

        Returns
        -------
        str
            Hex-encoded SHA-256 digest.
        """
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(BUF_SIZE):
                h.update(chunk)
        return h.hexdigest()

    def is_unchanged(self, file_path: Path) -> bool:
        """Check whether a file already exists in the manifest with a
        matching hash and completed status.

        A file that previously failed with the same hash still needs
        reprocessing, so both hash and status must match.

        Parameters
        ----------
        file_path: Path
            File to check.

        Returns
        -------
        bool
            ``True`` if the file is already ingested with the same
            hash.
        """
        current_hash = self.compute_hash(file_path)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT sha256, status FROM manifest WHERE file_path = ?",
                (str(file_path),),
            ).fetchone()
        if row is None:
            return False
        return row["sha256"] == current_hash and row["status"] == "complete"

    def needs_processing(self, file_path: Path, raw_dir: Path | None = None) -> bool:
        """Check whether a file is new, changed, or previously failed.

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
        current_hash = self.compute_hash(file_path)
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
        if row["status"] != "complete":
            return True
        # Complete + unchanged: re-process only if its raw output is gone.
        if raw_dir is not None and not _raw_output_exists(raw_dir, row["raw_output"]):
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
        return any(_raw_output_exists(raw_dir, r["raw_output"]) for r in rows)

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
        sha = self.compute_hash(file_path)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO manifest
                   (file_path, sha256, source_type, status, ingested_at, updated_at)
                   VALUES (?, ?, ?, 'processing', ?, ?)
                   ON CONFLICT(file_path) DO UPDATE SET
                     sha256 = excluded.sha256,
                     status = 'processing',
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
            # (Re)ingesting a source invalidates any prior compilation of
            # its raw output, so an overwritten/updated file is recompiled.
            if raw_output is not None:
                conn.execute("DELETE FROM compiled WHERE raw_path = ?", (raw_output,))

    def mark_failed(self, file_path: Path, error: str | None = None) -> None:
        """Mark a file as failed so it will be retried on the next run.

        Parameters
        ----------
        file_path: Path
            File that failed processing.
        error: str | None
            Optional error message to log.
        """
        now = datetime.now(UTC).isoformat()
        with self._conn() as conn:
            conn.execute(
                """UPDATE manifest
                   SET status = 'failed', updated_at = ?
                   WHERE file_path = ?""",
                (now, str(file_path)),
            )
        if error:
            logger.error("Ingestion failed for %s: %s", file_path, error)

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

    def forget_source(self, raw_output: str) -> None:
        """Fully un-ingest a source: drop its manifest, compiled, and triage
        rows so it leaves both the staged and built sets.

        This clears only the database records. Deleting the raw markdown
        file on disk is the caller's responsibility.

        Parameters
        ----------
        raw_output: str
            Raw output path relative to the raw directory, e.g.
            ``"documents/notes.md"``.
        """
        with self._conn() as conn:
            conn.execute("DELETE FROM manifest WHERE raw_output = ?", (raw_output,))
            conn.execute("DELETE FROM compiled WHERE raw_path = ?", (raw_output,))
            conn.execute("DELETE FROM triage WHERE raw_path = ?", (raw_output,))

    def get_all(
        self,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[ManifestEntry]:
        """Return all manifest entries, optionally filtered by status.

        Parameters
        ----------
        status: str | None
            If provided, only entries with this status are returned.
        limit: int | None
            If provided, cap the number of returned entries (most
            recently updated first).

        Returns
        -------
        list[ManifestEntry]
            Entries ordered by most recently updated first.
        """
        sql = "SELECT * FROM manifest"
        params: tuple = ()
        if status:
            sql += " WHERE status = ?"
            params = (status,)
        sql += " ORDER BY updated_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params = (*params, limit)

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_entry(r) for r in rows]

    def get_completed_raw_paths(self) -> set[str]:
        """Return raw output paths for all completed ingestions.

        Returns
        -------
        set[str]
            Paths to raw markdown files that have been successfully
            ingested.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT raw_output FROM manifest "
                "WHERE status = 'complete' AND raw_output IS NOT NULL"
            ).fetchall()
        return {r["raw_output"] for r in rows}

    def mark_compiled(self, raw_paths: list[str]) -> None:
        """Record that the given raw files were compiled into the wiki.

        Parameters
        ----------
        raw_paths: list[str]
            Raw file paths (relative to the raw directory) consumed by
            the compilation agent in a successful run.
        """
        if not raw_paths:
            return
        now = datetime.now(UTC).isoformat()
        with self._conn() as conn:
            for rel in raw_paths:
                conn.execute(
                    "INSERT INTO compiled (raw_path, compiled_at) VALUES (?, ?) "
                    "ON CONFLICT(raw_path) DO UPDATE SET compiled_at = excluded.compiled_at",
                    (rel, now),
                )

    def get_compiled_raw_paths(self) -> set[str]:
        """Return the set of raw file paths already compiled into the wiki.

        Returns
        -------
        set[str]
            Relative raw paths that the compilation agent has consumed.
        """
        with self._conn() as conn:
            rows = conn.execute("SELECT raw_path FROM compiled").fetchall()
        return {r["raw_path"] for r in rows}

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

    def record_triage(
        self,
        raw_path: str,
        decision: str,
        confidence: float,
        reason: str = "",
    ) -> None:
        """Persist a triage verdict for a raw source path (upsert).

        Parameters
        ----------
        raw_path: str
            Raw source path (relative to the raw directory).
        decision: str
            One of ``"worthwhile"``, ``"review"``, ``"skip"``.
        confidence: float
            Triage model confidence for the decision.
        reason: str
            Short human-readable rationale for logs/digests.
        """
        now = datetime.now(UTC).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO triage (raw_path, decision, confidence, reason, triaged_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(raw_path) DO UPDATE SET
                     decision = excluded.decision,
                     confidence = excluded.confidence,
                     reason = excluded.reason,
                     triaged_at = excluded.triaged_at""",
                (raw_path, decision, confidence, reason, now),
            )

    def get_triage_decisions(self) -> dict[str, str]:
        """Return the recorded triage decision for every triaged raw path.

        Returns
        -------
        dict[str, str]
            Mapping of raw source path to decision label.
        """
        with self._conn() as conn:
            rows = conn.execute("SELECT raw_path, decision FROM triage").fetchall()
        return {r["raw_path"]: r["decision"] for r in rows}

    def count_triage_decision(self, decision: str) -> int:
        """Count triage rows carrying a given decision (e.g. deferred)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM triage WHERE decision = ?",
                (decision,),
            ).fetchone()
        return int(row["cnt"]) if row else 0


def _row_to_entry(row: sqlite3.Row) -> ManifestEntry:
    """Convert a SQLite manifest row to a :class:`ManifestEntry`.

    Tolerates rows missing ``content_hash`` so older databases still
    deserialize after a fresh checkout but before any new write.
    """
    content_hash: str | None
    try:
        content_hash = row["content_hash"]
    except (IndexError, KeyError):
        content_hash = None
    return ManifestEntry(
        file_path=row["file_path"],
        sha256=row["sha256"],
        source_type=row["source_type"],
        status=row["status"],
        parse_lane=row["parse_lane"],
        raw_output_path=row["raw_output"],
        ingested_at=row["ingested_at"],
        updated_at=row["updated_at"],
        content_hash=content_hash,
    )
