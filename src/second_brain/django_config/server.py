"""Prepare the selected vault, then serve the API in one Uvicorn process."""

import argparse
import os
import signal
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import django
import uvicorn
from django.conf import settings
from django.core.management import CommandError, call_command
from django.db import connections

from second_brain.django_config.identity import private_token


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reload",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Reload changed Python files (default: enabled when Django DEBUG is true).",
    )
    args = parser.parse_args()
    # Uvicorn re-raises captured signals after serving. A Python exit handler lets
    # our worker context clean up; the OS default SIGTERM would skip its finally.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "second_brain.django_config.settings.runtime")
    django.setup()
    call_command("check")

    # Run migrations before starting workers or accepting requests. A reloaded
    # ASGI child does not repeat this startup sequence.
    settings.VAULT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Using vault: {settings.VAULT_DIR}", flush=True)
    from second_brain.django_config.vault_storage import vault_lock

    with vault_lock("backend", blocking=False):
        private_token(settings.VAULT_DIR, "api-token")
        _check_database(Path(settings.DATABASES["default"]["NAME"]))
        try:
            call_command("migrate", interactive=False)
        finally:
            connections.close_all()

        from second_brain.apps.jobs.services.worker_supervisor import supervised_workers
        from second_brain.apps.preferences.services.user_settings import (
            get_pipeline_config,
            get_preferences,
        )

        get_preferences()  # Fail startup on invalid settings before accepting work.
        get_pipeline_config().ensure_directories()
        reload = settings.DEBUG if args.reload is None else args.reload
        with supervised_workers():
            uvicorn.run(
                "second_brain.django_config.asgi:application",
                host="127.0.0.1",
                port=settings.BACKEND_PORT,
                workers=1,
                reload=reload,
                reload_dirs=[str(Path(__file__).resolve().parents[1])] if reload else None,
                proxy_headers=False,  # The desktop client connects directly, without a proxy.
                timeout_graceful_shutdown=5,  # Cancel open SSE streams when quitting/reloading.
                lifespan="off",  # Django's ASGI application handles HTTP, not lifespan events.
            )


def _check_database(database: Path) -> None:
    """Back up a supported legacy database before the first Django adoption."""
    if not database.exists():
        return
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as connection:
        legacy = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'manifest'"
        ).fetchone()
        if not legacy:
            return
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (1, 2, 3):
            raise CommandError(
                f"Unrecognized legacy pipeline database version {version}; refusing adoption."
            )
        migration_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'django_migrations'"
        ).fetchone()
        if (
            migration_table
            and connection.execute(
                "SELECT 1 FROM django_migrations WHERE app = 'library' AND name = '0001_initial'"
            ).fetchone()
        ):
            return
        backups = database.parent / ".backend" / "backups"
        backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = backups / f"manifest-before-django-{stamp}.db"
        # SQLite's backup API includes committed WAL data; copying the main file alone may not.
        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        with closing(sqlite3.connect(backup)) as destination:
            connection.backup(destination)
        print(f"Saved database backup: {backup}", flush=True)


if __name__ == "__main__":
    main()
