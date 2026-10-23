"""Tests for the pipeline database schema and its migrations."""

from __future__ import annotations

from pathlib import Path

from second_brain.db import _add_build_content_hashes, _create_tables, connect, migrate
from second_brain.ingestion.manifest import Manifest


def _version(db_path: Path) -> int:
    with connect(db_path) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


def test_a_new_database_gets_every_table(tmp_path: Path) -> None:
    db_path = tmp_path / "manifest.db"

    migrate(db_path)

    with connect(db_path) as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    # The menu bar app queries these tables by name.
    assert tables == {
        "manifest",
        "page_cache",
        "triage",
        "compiled",
        "dismissed_duplicates",
        "deferred_sources",
    }
    assert _version(db_path) >= 1


def test_migrating_again_changes_nothing(tmp_path: Path) -> None:
    db_path = tmp_path / "manifest.db"
    migrate(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO compiled (raw_path, compiled_at) VALUES (?, ?)",
            ("documents/a.md", "2026-01-01T00:00:00"),
        )
    version = _version(db_path)

    migrate(db_path)

    assert _version(db_path) == version
    with connect(db_path) as conn:
        rows = conn.execute("SELECT raw_path FROM compiled").fetchall()
    assert [row["raw_path"] for row in rows] == ["documents/a.md"]


def test_existing_manifest_rows_survive_adding_watched_origins(tmp_path: Path) -> None:
    db_path = tmp_path / "manifest.db"
    with connect(db_path) as conn:
        _create_tables(conn)
        _add_build_content_hashes(conn)
        conn.execute(
            "INSERT INTO manifest "
            "(file_path, sha256, source_type, status, ingested_at, updated_at) "
            "VALUES ('/watched/notes.md', 'original-hash', 'research', 'complete', 'then', 'then')"
        )
        # Version 2 is the persisted schema before watched-origin tracking.
        conn.execute("PRAGMA user_version = 2")

    migrate(db_path)
    entry = Manifest(db_path).get_entry(Path("/watched/notes.md"))

    assert entry.sha256 == "original-hash"
    assert entry.status == "complete"
    assert entry.origin_path is None
    assert entry.origin_sha256 is None
