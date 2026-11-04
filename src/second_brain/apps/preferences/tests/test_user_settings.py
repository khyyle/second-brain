"""Preferences load only from the vault selected by Django settings."""

from copy import deepcopy

import pytest
import yaml

from second_brain.apps.preferences.services.user_settings import (
    get_pipeline_config,
    get_preferences,
    update_preferences,
)
from second_brain.pipeline_config import ConfigError, Preferences


def test_missing_file_uses_defaults(config):
    assert isinstance(get_preferences(), Preferences)
    assert get_preferences().compilation.provider == "anthropic"


def test_yaml_cannot_select_the_vault(config):
    (config.data_dir / "config.yaml").write_text("data_dir: /tmp/wrong\n")
    with pytest.raises(ConfigError):
        get_preferences()


def test_runtime_context_uses_selected_vault(config):
    (config.data_dir / "config.yaml").write_text("compilation:\n  max_iterations: 15\n")
    current = get_pipeline_config()
    assert current.data_dir == config.data_dir
    assert current.compilation.max_iterations == 15


def test_invalid_yaml_names_the_line(config):
    (config.data_dir / "config.yaml").write_text("triage:\n  profile: [unclosed\n")
    with pytest.raises(ConfigError, match="valid YAML near line"):
        get_preferences()


def test_obsolete_hosts_load_from_preferences_and_job_snapshots(config):
    saved = {
        "triage": {"ollama_host": "http://localhost:11434", "profile": "technical"},
        "search": {"ollama_host": "http://localhost:11434", "semantic_enabled": False},
    }
    original = deepcopy(saved)
    path = config.data_dir / "config.yaml"
    path.write_text(yaml.safe_dump(saved))

    preferences = get_preferences()
    job_config = get_pipeline_config(saved)
    assert preferences.triage.profile == job_config.triage.profile == "technical"
    assert preferences.search.semantic_enabled is job_config.search.semantic_enabled is False
    assert saved == original

    update_preferences({"triage": {"enabled": False}})
    assert "ollama_host" not in path.read_text()
    assert get_preferences().triage.profile == "technical"


def test_obsolete_host_cleanup_still_rejects_unknown_settings(config):
    (config.data_dir / "config.yaml").write_text(
        "triage:\n  ollama_host: http://localhost:11434\n  typo: true\n"
    )
    with pytest.raises(ConfigError, match="triage.typo"):
        get_preferences()
