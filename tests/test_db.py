"""Tests for the pipeline database schema and its migrations."""

from __future__ import annotations

from pathlib import Path

from second_brain.db import connect, migrate


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
        conn.execute("INSERT INTO compiled VALUES ('documents/a.md', '2026-01-01T00:00:00')")
    version = _version(db_path)

    migrate(db_path)

    assert _version(db_path) == version
    with connect(db_path) as conn:
        rows = conn.execute("SELECT raw_path FROM compiled").fetchall()
    assert [row["raw_path"] for row in rows] == ["documents/a.md"]
