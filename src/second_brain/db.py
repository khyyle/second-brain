"""Schema, migrations, and connections for the pipeline database, manifest.db.

Every table is declared here, and each package reads and writes only its own tables.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

Migration = Callable[[sqlite3.Connection], None]


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """
    Open a connection that commits on clean exit.

    A fresh connection per call keeps long-running watchers from holding
    stale state; SQLite's file locking handles concurrent processes.

    Parameters
    ----------
    db_path: Path
        Path to the SQLite database file.

    Yields
    ------
    sqlite3.Connection
        A connection whose rows are addressable by column name.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def migrate(db_path: Path) -> None:
    """
    Create the database if needed and apply every migration it hasn't had.

    Each migration runs once, recorded in SQLite's ``user_version``. Pending
    migrations run in one transaction, so a crash part way leaves the
    database as it was.

    Parameters
    ----------
    db_path: Path
        Path to the SQLite database file.
    """
    with connect(db_path) as conn:
        if _schema_version(conn) >= len(_MIGRATIONS):
            return

    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        # NOTE: Taking the write lock before reading the version again means two
        # processes opening an old database at once can't both migrate it.
        conn.execute("BEGIN IMMEDIATE")
        version = _schema_version(conn)
        if version >= len(_MIGRATIONS):
            return
        for migration in _MIGRATIONS[version:]:
            migration(conn)
        conn.execute(f"PRAGMA user_version = {len(_MIGRATIONS)}")
        conn.execute("COMMIT")
    finally:
        conn.close()


def _schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE manifest (
            file_path    TEXT PRIMARY KEY,
            sha256       TEXT NOT NULL,
            source_type  TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'pending',
            parse_lane   TEXT,
            raw_output   TEXT,
            ingested_at  TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            content_hash TEXT,
            error        TEXT
        )
    """)
    conn.execute("CREATE INDEX idx_manifest_status ON manifest(status)")
    conn.execute("CREATE INDEX idx_manifest_hash ON manifest(sha256)")

    # Per-page OCR cache lets us skip re-OCRing pages whose rendered bytes
    # are unchanged across full-file re-exports (the Goodnotes case: edit
    # one page, the whole PDF binary changes).
    conn.execute("""
        CREATE TABLE page_cache (
            page_hash    TEXT PRIMARY KEY,
            parse_lane   TEXT NOT NULL,
            raw_markdown TEXT NOT NULL,
            parsed_at    TEXT NOT NULL
        )
    """)

    # Triage decisions keyed by the raw source path, so review/skip
    # verdicts persist and aren't re-evaluated on every compile run.
    conn.execute("""
        CREATE TABLE triage (
            raw_path    TEXT PRIMARY KEY,
            decision    TEXT NOT NULL,
            confidence  REAL NOT NULL,
            reason      TEXT,
            triaged_at  TEXT NOT NULL
        )
    """)

    # A raw file being produced (ingested) is not the same as it being
    # compiled into the wiki, so compilation is tracked separately.
    conn.execute("""
        CREATE TABLE compiled (
            raw_path     TEXT PRIMARY KEY,
            compiled_at  TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE dismissed_duplicates (
            stem_a       TEXT NOT NULL,
            stem_b       TEXT NOT NULL,
            dismissed_at TEXT NOT NULL,
            PRIMARY KEY (stem_a, stem_b)
        )
    """)

    # Sources whose compile run ended without completing.
    conn.execute("""
        CREATE TABLE deferred_sources (
            raw_path     TEXT PRIMARY KEY,
            reason       TEXT NOT NULL,
            deferred_at  TEXT NOT NULL,
            retryable    INTEGER NOT NULL DEFAULT 1
        )
    """)


# Append only: a migration's position is the version recorded in existing
# databases, so reordering or removing one skips or repeats work on them.
_MIGRATIONS: tuple[Migration, ...] = (_create_tables,)
