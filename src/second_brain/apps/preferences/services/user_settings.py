"""Cached, validated config.yaml preferences with atomic writes."""

import re
from copy import deepcopy
from pathlib import Path
from threading import RLock

import yaml
from pydantic import ValidationError

from second_brain.django_config.errors import ServiceError
from second_brain.django_config.paths import get_vault_paths
from second_brain.django_config.vault_storage import atomic_write, vault_lock
from second_brain.pipeline_config import (
    ConfigError,
    PipelineConfig,
    Preferences,
)

_PYDANTIC_VALUE_ERROR_PREFIX = "Value error, "
_PYDANTIC_INPUT_PREFIX = "Input should"

_cache_lock = RLock()
_cache: dict[Path, tuple[tuple[int, int, int] | None, Preferences]] = {}


def _version(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
        return stat.st_ino, stat.st_mtime_ns, stat.st_size
    except FileNotFoundError:
        return None


def get_preferences() -> Preferences:
    """Return a detached validated snapshot, reparsing only when the file changes.

    Returns
    -------
    Preferences
        Defaults if config.yaml is absent. A copy protects the cached source map.

    Raises
    ------
    ConfigError
        Invalid YAML or unsupported settings, including a vault override.

    Notes
    -----
    A cheap stat detects writes from other processes and manual edits. Each process
    has its own cache. An already running job keeps its original snapshot.
    """
    path = get_vault_paths().data_dir / "config.yaml"
    with _cache_lock:
        version = _version(path)
        cached = _cache.get(path)
        if cached is not None and cached[0] == version:
            return cached[1].model_copy(deep=True)
        raw = _read_yaml(path) if version is not None else {}
        try:
            preferences = Preferences.model_validate(raw)
        except ValidationError as exc:
            raise ConfigError(_describe_invalid_setting("config.yaml", exc)) from exc
        _validate_sources(preferences)
        _cache[path] = (version, preferences)
        return preferences.model_copy(deep=True)


def update_preferences(changes: dict) -> Preferences:
    """Merge supplied fields, validate the whole result, and persist atomically.

    Parameters
    ----------
    changes: dict
        Nested dictionaries merge, except sources replaces the entire watched-folder
        map. Null sections, runtime paths, credentials, and unknown fields are rejected.

    Returns
    -------
    Preferences
        Saved settings, visible to subsequent requests and new jobs.

    Raises
    ------
    ServiceError
        Invalid changes. The previous file remains intact.
    """
    with vault_lock("preferences"), _cache_lock:
        current = get_preferences().model_dump(mode="json")
        for key, value in changes.items():
            if value is None:
                raise ServiceError(f"{key} cannot be null.")
            if key != "sources" and isinstance(value, dict) and isinstance(current.get(key), dict):
                current[key].update(deepcopy(value))
            else:
                current[key] = deepcopy(value)
        try:
            validated = Preferences.model_validate(current)
        except ValidationError as exc:
            locations = ", ".join(".".join(map(str, e["loc"])) for e in exc.errors())
            raise ServiceError(f"Invalid settings: {locations}.") from exc
        _validate_sources(validated)
        path = get_vault_paths().data_dir / "config.yaml"
        atomic_write(path, yaml.safe_dump(validated.model_dump(mode="json"), sort_keys=False))
        _cache[path] = (_version(path), validated)
        return validated.model_copy(deep=True)


def get_pipeline_config(preferences: dict | None = None) -> PipelineConfig:
    """Build a pipeline context from startup paths and a preference snapshot.

    Parameters
    ----------
    preferences: dict | None
        Captured job preferences, or None to capture the current settings.

    Returns
    -------
    PipelineConfig
        Internal context. data_dir always comes from Django settings, never YAML.
        HTTP settings responses use Preferences, which has no data_dir field.
    """
    snapshot = (
        get_preferences() if preferences is None else Preferences.model_validate(preferences)
    )
    return PipelineConfig(**snapshot.model_dump(), data_dir=get_vault_paths().data_dir)


def _validate_sources(preferences: Preferences) -> None:
    """Enforce named external watched folders for both API and manual file edits."""
    seen = set()
    vault = get_vault_paths().data_dir
    for name, source in preferences.sources.items():
        if not re.fullmatch(r"[\w-]{1,100}", name) or name in {"documents", "chatgpt"}:
            raise ServiceError(
                "Watched-folder names must be unique, simple names outside built-in lanes."
            )
        if (
            source.path in seen
            or source.path.is_relative_to(vault)
            or vault.is_relative_to(source.path)
        ):
            raise ServiceError(
                "Watched folders must be distinct and cannot contain or live inside the vault."
            )
        if not set(source.file_types) <= {"pdf", "md", "txt", "tex", "json"}:
            raise ServiceError("A watched folder contains an unsupported file type.")
        seen.add(source.path)


def _describe_invalid_setting(config_name: str, error: ValidationError) -> str:
    """
    Phrase the first validation problem as one sentence naming the setting.

    Parameters
    ----------
    config_name: str
        File name shown to the user, such as ``config.yaml``.
    error: ValidationError
        Pydantic's report for the loaded YAML.

    Returns
    -------
    str
        For example ``Invalid setting in config.yaml: triage.profile must be
        one of balanced, technical, not 'x'.``
    """
    problem = error.errors()[0]
    setting = ".".join(str(part) for part in problem["loc"])
    message = problem["msg"]
    if message.startswith(_PYDANTIC_VALUE_ERROR_PREFIX):
        detail = message.removeprefix(_PYDANTIC_VALUE_ERROR_PREFIX)
    elif message.startswith(_PYDANTIC_INPUT_PREFIX):
        # Pydantic appends its own restatement after a comma.
        detail = "should" + message.removeprefix(_PYDANTIC_INPUT_PREFIX).split(",")[0]
    else:
        detail = f"is invalid ({message.lower()})"
    # Checks that span several settings name the one at fault themselves.
    problem_text = detail if detail.startswith(f"{setting}.") else f"{setting} {detail}"
    return f"Invalid setting in {config_name}: {problem_text}."


def _read_yaml(config_path: Path) -> dict:
    try:
        with open(config_path) as config_file:
            raw = yaml.safe_load(config_file) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" near line {mark.line + 1}" if mark is not None else ""
        raise ConfigError(
            f"{config_path.name} isn't valid YAML{where}. Fix the file and try again."
        ) from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path.name} must contain settings written as 'name: value'.")
    return raw
