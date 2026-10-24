"""Runtime values shared by development, management commands, and app launches."""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from ..environment import env_bool, load_environment
from ..identity import signing_key
from .base import *  # noqa: F403
from .base import sqlite_database

load_environment()

DEBUG = env_bool("SECOND_BRAIN_DEBUG", default=False)
API_DOCS_ENABLED = DEBUG

_vault = os.environ.get("SECOND_BRAIN_VAULT", "~/second-brain")
if not _vault.strip():
    raise ImproperlyConfigured("SECOND_BRAIN_VAULT must not be empty.")
VAULT_DIR = Path(_vault).expanduser()
if not VAULT_DIR.is_absolute():
    raise ImproperlyConfigured("SECOND_BRAIN_VAULT must be an absolute path (or start with ~/).")
VAULT_DIR = VAULT_DIR.resolve()
DATABASES = {"default": sqlite_database(VAULT_DIR / "manifest.db")}

try:
    BACKEND_PORT = int(os.environ.get("SECOND_BRAIN_PORT", "8000"))
except ValueError as exc:
    raise ImproperlyConfigured(
        "SECOND_BRAIN_PORT must be an integer between 1 and 65535."
    ) from exc
if not 1 <= BACKEND_PORT <= 65535:
    raise ImproperlyConfigured("SECOND_BRAIN_PORT must be an integer between 1 and 65535.")

SECRET_KEY = os.environ.get("SECOND_BRAIN_SECRET_KEY") or signing_key(VAULT_DIR)
