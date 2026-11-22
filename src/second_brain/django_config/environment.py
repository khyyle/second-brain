"""Load the repository .env and parse startup flags."""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv


def load_environment() -> None:
    """Read checkout startup values without overriding the process environment.

    Returns
    -------
    None
    """
    project_root = Path(__file__).resolve().parents[3]
    if (project_root / "pyproject.toml").is_file():
        load_dotenv(project_root / ".env", override=False)


def env_bool(name: str, *, default: bool) -> bool:
    """Parse a boolean startup variable, rejecting invalid values.

    Parameters
    ----------
    name: str
        Environment variable to parse. True/1/yes/on and false/0/no/off are accepted case-
        insensitively, with surrounding whitespace ignored.
    default: bool
        Value used only when the variable is absent.

    Returns
    -------
    bool
        Parsed flag; invalid text raises ImproperlyConfigured.
    """
    value = os.environ.get(name)
    if value is None:
        return default
    if value.strip().lower() in {"true", "1", "yes", "on"}:
        return True
    if value.strip().lower() in {"false", "0", "no", "off"}:
        return False
    raise ImproperlyConfigured(f"{name} must be true or false.")
