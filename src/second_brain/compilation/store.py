"""Build records: which raw sources are compiled into the wiki, and which were set aside."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from second_brain.db import connect, migrate


@dataclass(frozen=True)
class DeferredSource:
    """A source a build set aside, and the version of it that was set aside."""

    reason: str
    retryable: bool
    content_hash: str | None


class BuildStore:
    """
    Reads and writes the ``compiled`` and ``deferred_sources`` tables.

    Parameters
    ----------
    db_path: Path
        Path to the pipeline database. Created if it does not exist.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        migrate(db_path)

    def mark_compiled(self, source_hashes: dict[str, str]) -> None:
        """Record that the given raw files were compiled into the wiki.

        Parameters
        ----------
        source_hashes: dict[str, str]
            Raw file path (relative to the raw directory) mapped to the
            content hash of the version the build read.
        """
        if not source_hashes:
            return
        now = datetime.now(UTC).isoformat()
        with connect(self._db_path) as conn:
            conn.executemany(
                "INSERT INTO compiled (raw_path, compiled_at, content_hash) VALUES (?, ?, ?) "
                "ON CONFLICT(raw_path) DO UPDATE SET "
                "compiled_at = excluded.compiled_at, content_hash = excluded.content_hash",
                [(rel, now, content_hash) for rel, content_hash in source_hashes.items()],
            )

    def unmark_compiled(self, raw_paths: list[str]) -> int:
        """
        Clear the compiled record for the given raw files.

        These sources become eligible for the next normal build again, while
        their ingestion and triage records stay intact.

        Parameters
        ----------
        raw_paths: list[str]
            Raw file paths (relative to the raw directory) to unmark.

        Returns
        -------
        int
            Number of compiled records actually cleared.
        """
        if not raw_paths:
            return 0
        cleared = 0
        with connect(self._db_path) as conn:
            for rel in raw_paths:
                cursor = conn.execute("DELETE FROM compiled WHERE raw_path = ?", (rel,))
                cleared += cursor.rowcount
        return cleared

    def get_compiled(self) -> dict[str, str | None]:
        """Return every compiled raw file and the content hash it was built from.

        Returns
        -------
        dict[str, str | None]
            Relative raw path mapped to its content hash, or ``None`` for a
            record made before hashes were kept.
        """
        with connect(self._db_path) as conn:
            rows = conn.execute("SELECT raw_path, content_hash FROM compiled").fetchall()
        return {r["raw_path"]: r["content_hash"] for r in rows}

    def defer_sources(
        self, source_hashes: dict[str, str], reason: str, *, retryable: bool
    ) -> None:
        """Record sources as set aside. Re-deferring updates the reason.

        Parameters
        ----------
        source_hashes: dict[str, str]
            Raw file path (relative to the raw directory) mapped to the
            content hash of the version being set aside.
        reason: str
            Human-readable cause.
        retryable: bool
            Whether building the same source again could succeed.
        """
        now = datetime.now(UTC).isoformat()
        with connect(self._db_path) as conn:
            conn.executemany(
                "INSERT INTO deferred_sources "
                "(raw_path, reason, deferred_at, retryable, content_hash) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(raw_path) DO UPDATE SET "
                "reason = excluded.reason, deferred_at = excluded.deferred_at, "
                "retryable = excluded.retryable, content_hash = excluded.content_hash",
                [
                    (rel, reason, now, int(retryable), content_hash)
                    for rel, content_hash in source_hashes.items()
                ],
            )

    def get_deferred(self) -> dict[str, DeferredSource]:
        """Return every set-aside source.

        Returns
        -------
        dict[str, DeferredSource]
            Relative raw path mapped to why it was set aside, whether a retry
            could succeed, and which version was set aside.
        """
        with connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT raw_path, reason, retryable, content_hash FROM deferred_sources"
            ).fetchall()
        return {
            r["raw_path"]: DeferredSource(
                reason=r["reason"],
                retryable=bool(r["retryable"]),
                content_hash=r["content_hash"],
            )
            for r in rows
        }

    def clear_deferred(self, raw_paths: list[str]) -> int:
        """Release deferred sources back into the compile queue.

        Parameters
        ----------
        raw_paths: list[str]
            Paths relative to the raw directory.

        Returns
        -------
        int
            Number of rows removed.
        """
        if not raw_paths:
            return 0
        with connect(self._db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM deferred_sources WHERE raw_path IN "
                f"({','.join('?' * len(raw_paths))})",
                raw_paths,
            )
        return cursor.rowcount


def forget_build(conn: sqlite3.Connection, raw_path: str) -> None:
    """
    Delete a source's compiled and set-aside records on the caller's connection.

    Parameters
    ----------
    conn: sqlite3.Connection
        Open connection whose transaction the caller commits.
    raw_path: str
        Path relative to the raw directory.
    """
    conn.execute("DELETE FROM compiled WHERE raw_path = ?", (raw_path,))
    conn.execute("DELETE FROM deferred_sources WHERE raw_path = ?", (raw_path,))
