"""Watched folder definitions live in the preferences sources map."""

import re
from pathlib import Path

from second_brain.apps.preferences.services.user_settings import (
    get_preferences,
    update_preferences,
)
from second_brain.django_config.errors import ServiceError
from second_brain.django_config.paths import get_vault_paths


def list_folders() -> list[dict]:
    """Return named external source folders, with no implicit built-in drop lanes.

    Returns
    -------
    list[dict]
        Saved source-folder definitions with their names, paths, enabled state, and file
        types.
    """
    return [
        {"name": name, **source.model_dump(mode="json")}
        for name, source in get_preferences().sources.items()
    ]


def replace_folders(folders: list[dict]) -> list[dict]:
    """Validate and replace all watched folders without starting a build.

    Parameters
    ----------
    folders: list[dict]
        Named external folders. Names and resolved paths must be unique; built-in
        chatgpt/documents names and paths inside the vault are reserved.

    Returns
    -------
    list[dict]
        Persisted definitions.

    Raises
    ------
    ServiceError
        Duplicate, reserved, relative, or invalid folder definitions.
    """
    sources = {}
    paths = set()
    vault = get_vault_paths().data_dir
    for folder in folders:
        entry = dict(folder)
        name = entry.pop("name")
        path = Path(entry["path"]).expanduser()
        if not path.is_absolute():
            raise ServiceError("Watched folders require absolute paths.")
        path = path.resolve()
        if (
            name in sources
            or name in {"chatgpt", "documents"}
            or not re.fullmatch(r"[\w-]+", name)
        ):
            raise ServiceError(
                "Watched-folder names must be unique and cannot use built-in lanes."
            )
        if path in paths or path.is_relative_to(vault):
            raise ServiceError("Watched folders must be unique and outside the vault.")
        entry["path"] = str(path)
        sources[name] = entry
        paths.add(path)
    update_preferences({"sources": sources})
    return list_folders()
