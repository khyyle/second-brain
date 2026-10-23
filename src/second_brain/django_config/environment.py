"""Load development configuration without depending on the working directory."""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import dotenv_values

_STARTUP_VARIABLES = (
    "SECOND_BRAIN_VAULT",
    "SECOND_BRAIN_DEBUG",
    "SECOND_BRAIN_PORT",
    "SECOND_BRAIN_SECRET_KEY",
)


def load_environment() -> None:
    """Load only startup options from .env; provider credentials belong in Keychain."""
    env_file = os.environ.get("SECOND_BRAIN_ENV_FILE")
    if env_file is not None:
        # An empty value disables dotenv, e.g. for a launcher supplying everything.
        if not env_file:
            return
        path = Path(env_file).expanduser()
        if not path.is_file():
            raise ImproperlyConfigured("SECOND_BRAIN_ENV_FILE must name an existing file.")
    else:
        # Only source checkouts have this layout. Installed wheels don't search
        # the current directory or its parents for an unrelated .env file.
        project_root = Path(__file__).resolve().parents[3]
        if not (project_root / "pyproject.toml").is_file():
            return
        path = project_root / ".env"

    values = dotenv_values(path)
    for name in _STARTUP_VARIABLES:
        value = values.get(name)
        if value is not None:
            os.environ.setdefault(name, value)


def env_bool(name: str, *, default: bool) -> bool:
    """Parse boolean text explicitly: bool('false') would incorrectly be True."""
    value = os.environ.get(name)
    if value is None:
        return default
    if value.strip().lower() in {"true", "1", "yes", "on"}:
        return True
    if value.strip().lower() in {"false", "0", "no", "off"}:
        return False
    raise ImproperlyConfigured(f"{name} must be true or false.")
