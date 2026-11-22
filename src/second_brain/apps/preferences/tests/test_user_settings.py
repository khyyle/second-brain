"""Preferences load only from the vault selected by Django settings."""

import pytest
from pydantic import ValidationError

from second_brain.apps.preferences.services.user_settings import (
    get_pipeline_config,
    get_preferences,
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


def test_unknown_setting_is_rejected_in_vault_and_job_snapshot(config):
    (config.data_dir / "config.yaml").write_text("triage:\n  min_words: 300\n")
    with pytest.raises(ConfigError, match=r"triage\.min_words"):
        get_preferences()
    with pytest.raises(ValidationError, match=r"triage\.min_words"):
        get_pipeline_config({"triage": {"min_words": 300}})
