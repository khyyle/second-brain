"""Prepare the selected vault, then serve the API in one Uvicorn process."""

import argparse
import os
import sqlite3
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
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "second_brain.django_config.settings.runtime")
    django.setup()
    call_command("check")

    # Keep filesystem changes and migrations in the launcher, never in settings
    # imports or AppConfig.ready(). A reloaded ASGI child doesn't repeat startup.
    settings.VAULT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Using vault: {settings.VAULT_DIR}", flush=True)
    private_token(settings.VAULT_DIR, "api-token")
    _check_database(Path(settings.DATABASES["default"]["NAME"]))
    try:
        call_command("migrate", interactive=False)
    finally:
        connections.close_all()

    reload = settings.DEBUG if args.reload is None else args.reload
    uvicorn.run(
        "second_brain.django_config.asgi:application",
        host="127.0.0.1",
        port=settings.BACKEND_PORT,
        workers=1,
        reload=reload,
        reload_dirs=[str(Path(__file__).resolve().parents[1])] if reload else None,
        proxy_headers=False,  # The desktop client connects directly, without a proxy.
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
