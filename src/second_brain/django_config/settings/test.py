"""Test profile independent of the installed service and development vault."""

from .base import *  # noqa: F403
from .base import sqlite_database

SECRET_KEY = "test-only-second-brain-key"
ALLOWED_HOSTS = ["testserver", "127.0.0.1", "localhost", "[::1]"]
API_DOCS_ENABLED = True
DATABASES = {"default": sqlite_database(":memory:")}
