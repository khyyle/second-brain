"""Shared Django configuration; user preferences live outside Django settings."""

from pathlib import Path
from typing import Any

DEBUG = False
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]"]

INSTALLED_APPS = [
    "second_brain.apps.library.apps.LibraryConfig",
    "second_brain.apps.ingestion.apps.IngestionConfig",
    "second_brain.apps.compilation.apps.CompilationConfig",
    "second_brain.apps.wiki.apps.WikiConfig",
    "second_brain.apps.jobs.apps.JobsConfig",
    "second_brain.apps.preferences.apps.PreferencesConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]
APPEND_SLASH = False
ROOT_URLCONF = "second_brain.django_config.urls"
ASGI_APPLICATION = "second_brain.django_config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
    },
]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True
API_DOCS_ENABLED = False


def sqlite_database(path: Path | str) -> dict[str, Any]:
    """Configure short write transactions for a local SQLite database."""
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": path,
        "CONN_MAX_AGE": 0,
        "OPTIONS": {
            "timeout": 5,
            "transaction_mode": "IMMEDIATE",
            "init_command": "PRAGMA journal_mode=WAL;",
        },
    }
