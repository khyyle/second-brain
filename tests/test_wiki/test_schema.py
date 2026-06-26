"""Tests for the topic schema vocabulary and register-on-use."""

from __future__ import annotations

from pathlib import Path

import yaml

from second_brain.wiki.schema import DEFAULT_SCHEMA, register_domains, write_default_schema


def _domains(wiki: Path) -> dict:
    raw = yaml.safe_load((wiki / "_meta" / "topic_schema.yaml").read_text())
    return raw.get("domains") or {}


def test_default_schema_ships_no_domains() -> None:
    assert DEFAULT_SCHEMA["domains"] == {}
    assert "agent_permissions" not in DEFAULT_SCHEMA


def test_register_adds_new_domains(tmp_path: Path) -> None:
    write_default_schema(tmp_path)

    added = register_domains(tmp_path, {"finance", "biology"})

    assert sorted(added) == ["biology", "finance"]
    assert set(_domains(tmp_path)) == {"finance", "biology"}


def test_register_is_idempotent_and_skips_existing(tmp_path: Path) -> None:
    write_default_schema(tmp_path)
    register_domains(tmp_path, {"finance"})

    # Re-registering an existing domain plus a new one only adds the new one.
    added = register_domains(tmp_path, {"finance", "history"})

    assert added == ["history"]
    assert set(_domains(tmp_path)) == {"finance", "history"}


def test_register_preserves_other_schema_sections(tmp_path: Path) -> None:
    write_default_schema(tmp_path)
    register_domains(tmp_path, {"finance"})

    raw = yaml.safe_load((tmp_path / "_meta" / "topic_schema.yaml").read_text())
    assert "content_types" in raw
    assert "page_rules" in raw
