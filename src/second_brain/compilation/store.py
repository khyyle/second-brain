"""Build records: which raw sources are compiled into the wiki, and which were set aside."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from second_brain.db import connect, migrate


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
        with connect(self._db_path) as conn:
            conn.executemany(
                "INSERT INTO compiled (raw_path, compiled_at) VALUES (?, ?) "
                "ON CONFLICT(raw_path) DO UPDATE SET compiled_at = excluded.compiled_at",
                [(rel, now) for rel in raw_paths],
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

    def get_compiled_raw_paths(self) -> set[str]:
        """Return the set of raw file paths already compiled into the wiki.

        Returns
        -------
        set[str]
            Relative raw paths that the compilation agent has consumed.
        """
        with connect(self._db_path) as conn:
            rows = conn.execute("SELECT raw_path FROM compiled").fetchall()
        return {r["raw_path"] for r in rows}

    def defer_sources(self, raw_paths: list[str], reason: str, *, retryable: bool) -> None:
        """Record sources as set aside. Re-deferring updates the reason.

        Parameters
        ----------
        raw_paths: list[str]
            Paths relative to the raw directory.
        reason: str
            Human-readable cause.
        retryable: bool
            Whether building the same source again could succeed.
        """
        now = datetime.now(UTC).isoformat()
        with connect(self._db_path) as conn:
            conn.executemany(
                "INSERT INTO deferred_sources (raw_path, reason, deferred_at, retryable) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(raw_path) DO UPDATE SET "
                "reason = excluded.reason, deferred_at = excluded.deferred_at, "
                "retryable = excluded.retryable",
                [(path, reason, now, int(retryable)) for path in raw_paths],
            )

    def get_deferred_sources(self) -> dict[str, str]:
        """Return every deferred source as a raw_path -> reason mapping.

        Returns
        -------
        dict[str, str]
            Deferred raw paths and why each was parked.
        """
        with connect(self._db_path) as conn:
            rows = conn.execute("SELECT raw_path, reason FROM deferred_sources").fetchall()
        return {r["raw_path"]: r["reason"] for r in rows}

    def get_unretryable_sources(self) -> set[str]:
        """Return the deferred sources that building again cannot fix.

        Returns
        -------
        set[str]
            Raw paths that must be split before they can be built.
        """
        with connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT raw_path FROM deferred_sources WHERE retryable = 0"
            ).fetchall()
        return {r["raw_path"] for r in rows}

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
