"""Verify migrations finish before serving, using disposable on-disk databases."""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("migration_fails", [False, True])
def test_startup_only_serves_after_successful_migration(
    tmp_path: Path, backend_env: dict[str, str], migration_fails: bool
) -> None:
    migrations = tmp_path / "startup_migrations"
    migrations.mkdir()
    (migrations / "__init__.py").write_text("")
    sql = (
        "INVALID SQL" if migration_fails else "CREATE TABLE startup_probe (id INTEGER PRIMARY KEY)"
    )
    (migrations / "0001_initial.py").write_text(
        "from django.db import migrations\n"
        "class Migration(migrations.Migration):\n"
        "    initial = True\n"
        "    dependencies = []\n"
        f"    operations = [migrations.RunSQL({sql!r})]\n"
    )
    backend_env["SECOND_BRAIN_PORT"] = "8123"
    start = Path(__file__).resolve().parents[4] / "start.py"
    script = f"""
import json
import runpy
import sqlite3
import sys
import django
import uvicorn
from django.conf import settings
from django.db import connections

sys.path.insert(0, {str(tmp_path)!r})
django.setup()
settings.MIGRATION_MODULES = {{'library': 'startup_migrations'}}

def inspect_before_serving(app, **options):
    # This runs at the exact point the launcher hands control to Uvicorn.
    assert all(conn.connection is None for conn in connections.all())
    with sqlite3.connect(settings.DATABASES['default']['NAME']) as db:
        db.execute('SELECT * FROM startup_probe')
        count = db.execute(
            "SELECT COUNT(*) FROM django_migrations WHERE app = 'library'"
        ).fetchone()[0]
    print('SERVER_READY ' + json.dumps({{'migrations': count, 'app': app, **options}}))

uvicorn.run = inspect_before_serving
sys.argv = [{str(start)!r}, '--no-reload']
runpy.run_path({str(start)!r}, run_name='__main__')
"""
    # A second launch must leave an already-migrated database usable.
    for _ in range(1 if migration_fails else 2):
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=backend_env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if migration_fails:
            assert result.returncode != 0
            assert "SERVER_READY" not in result.stdout
            assert "OperationalError" in result.stderr
        else:
            assert result.returncode == 0, result.stderr
            output = next(
                line for line in result.stdout.splitlines() if line.startswith("SERVER_READY")
            )
            server = json.loads(output.removeprefix("SERVER_READY "))
            assert server["migrations"] == 1
            assert server["host"] == "127.0.0.1"
            assert server["port"] == 8123
            assert server["workers"] == 1
            assert server["reload"] is False


def test_startup_refuses_to_migrate_the_legacy_database(backend_env: dict[str, str]) -> None:
    vault = Path(backend_env["SECOND_BRAIN_VAULT"])
    vault.mkdir()
    database = vault / "manifest.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE manifest (file_path TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO manifest VALUES ('preserve-this-document.pdf')")
    original = database.read_bytes()
    start = Path(__file__).resolve().parents[4] / "start.py"

    result = subprocess.run(
        [sys.executable, str(start), "--no-reload"],
        env=backend_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    assert "legacy pipeline database" in result.stderr
    assert database.read_bytes() == original
