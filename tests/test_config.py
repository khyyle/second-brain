"""Tests for loading and validating configuration from disk."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from second_brain.config import Config, ConfigError, load_config


def test_load_config_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    config = load_config(tmp_path / "nonexistent.yaml")
    assert isinstance(config, Config)
    assert config.compilation.provider == "anthropic"


def test_load_config_parses_yaml_file(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    data_dir = tmp_path / "custom_data"
    raw_yaml = {
        "data_dir": str(data_dir),
        "compilation": {
            "provider": "anthropic",
            "model": "claude-haiku-4-5",
            "max_iterations": 15,
        },
        "search": {
            "embedding_model": "nomic-embed-text",
            "embedding_dimensions": 768,
        },
    }
    config_file.write_text(yaml.dump(raw_yaml), encoding="utf-8")

    config = load_config(config_file)
    assert config.data_dir == data_dir
    assert config.compilation.model == "claude-haiku-4-5"
    assert config.compilation.max_iterations == 15


def test_load_config_merges_user_sources_json(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    raw_yaml = {
        "data_dir": str(data_dir),
        "sources": {
            "documents": {
                "path": "~/drops/documents",
                "enabled": True,
                "file_types": ["pdf"],
            }
        },
    }
    config_file.write_text(yaml.dump(raw_yaml), encoding="utf-8")

    sources_json = [
        {
            "name": "extra_notes",
            "path": "~/drops/notes",
            "enabled": True,
            "file_types": ["md", "txt"],
        }
    ]
    (data_dir / "sources.json").write_text(json.dumps(sources_json), encoding="utf-8")

    config = load_config(config_file)
    assert "documents" in config.sources
    assert "extra_notes" in config.sources
    assert config.sources["extra_notes"].file_types == ("md", "txt")


def test_load_config_raises_config_error_on_invalid_values(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    invalid_yaml = {
        "compilation": {
            "provider": "unsupported_llm_provider",
        }
    }
    config_file.write_text(yaml.dump(invalid_yaml), encoding="utf-8")

    with pytest.raises(ConfigError, match="Invalid configuration"):
        load_config(config_file)
