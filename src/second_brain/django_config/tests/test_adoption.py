"""Exercise the real migrations on legacy SQLite files, including committed WAL rows."""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("version", [1, 2, 3])
def test_launcher_adopts_legacy_data_and_preserves_a_backup(
    backend_env: dict[str, str],
    version: int,
) -> None:
    vault = Path(backend_env["SECOND_BRAIN_VAULT"])
    vault.mkdir()
    database = vault / "manifest.db"
    legacy = sqlite3.connect(database)
    try:
        legacy.execute("PRAGMA journal_mode=WAL")
        fixtures = Path(__file__).with_name("fixtures")
        for schema_version in range(1, version + 1):
            legacy.executescript((fixtures / f"legacy_schema_v{schema_version}.sql").read_text())
        legacy.execute(f"PRAGMA user_version={version}")
        timestamp = "2026-10-23T12:34:56+00:00"
        legacy.execute(
            "INSERT INTO manifest (file_path, sha256, source_type, status, raw_output, "
            "ingested_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "/drop/notes.md",
                "hash",
                "documents",
                "complete",
                "documents/notes.md",
                timestamp,
                timestamp,
            ),
        )
        legacy.execute(
            "INSERT INTO page_cache VALUES (?, ?, ?, ?)",
            ("page-hash", "docling", "# Notes", timestamp),
        )
        legacy.execute(
            "INSERT INTO triage VALUES (?, ?, ?, ?, ?)",
            ("chatgpt/chat.md", "worthwhile", 1.0, "kept", timestamp),
        )
        legacy.execute(
            "INSERT INTO compiled (raw_path, compiled_at) VALUES (?, ?)",
            ("documents/notes.md", timestamp),
        )
        legacy.execute(
            "INSERT INTO deferred_sources (raw_path, reason, deferred_at, retryable) "
            "VALUES (?, ?, ?, ?)",
            ("documents/large.md", "capacity", timestamp, 1),
        )
        legacy.execute("INSERT INTO dismissed_duplicates VALUES (?, ?, ?)", ("a", "b", timestamp))
        legacy.commit()
        before = {
            table: legacy.execute(f'SELECT * FROM "{table}"').fetchall()
            for table in (
                "manifest",
                "page_cache",
                "triage",
                "compiled",
                "deferred_sources",
                "dismissed_duplicates",
            )
        }
        script = """
import sys
import uvicorn
from second_brain.django_config.server import main

def verify(app, **options):
    from second_brain.apps.library.models import Source, TriageDecision
    from second_brain.apps.library.schemas import SourceOut
    from second_brain.apps.ingestion.models import PageCache
    from second_brain.apps.compilation.models import CompiledSource, DeferredSource
    from second_brain.apps.wiki.models import DismissedDuplicate
    from second_brain.apps.jobs.models import Job
    row = Source.objects.get(pk='/drop/notes.md')
    assert row.updated_at.isoformat() == '2026-10-23T12:34:56+00:00'
    assert SourceOut.from_orm(row).raw_output == 'documents/notes.md'
    assert TriageDecision.objects.get().decision == 'worthwhile'
    assert PageCache.objects.get().raw_markdown == '# Notes'
    assert CompiledSource.objects.get().raw_path == 'documents/notes.md'
    assert DeferredSource.objects.get().retryable is True
    assert DismissedDuplicate.objects.get(pk=('a', 'b')).stem_a == 'a'
    Job.objects.get_or_create(kind='ingest', lane='ingest')
    assert Job.objects.count() == 1
    print('ADOPTION_OK')

uvicorn.run = verify
sys.argv = ['start.py', '--no-reload']
main()
"""
        for _ in range(2):
            result = subprocess.run(
                [sys.executable, "-c", script],
                env=backend_env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            assert "ADOPTION_OK" in result.stdout
        backups = list((vault / ".backend" / "backups").glob("*.db"))
        assert len(backups) == 1
        assert backups[0].stat().st_mode & 0o777 == 0o600
        with sqlite3.connect(backups[0]) as backup:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == version
            for table, rows in before.items():
                assert backup.execute(f'SELECT * FROM "{table}"').fetchall() == rows
        for table, rows in before.items():
            migrated = legacy.execute(f'SELECT * FROM "{table}"').fetchall()
            assert [row[: len(rows[0])] for row in migrated] == rows
        assert legacy.execute("PRAGMA user_version").fetchone()[0] == 3
        token_file = vault / ".backend" / "api-token"
        assert token_file.stat().st_mode & 0o777 == 0o600
        assert len(token_file.read_text()) > 40
    finally:
        legacy.close()
