"""Tests for triage prompt profiles and config/prompt name sync."""

from __future__ import annotations

from second_brain.config import _TRIAGE_PROFILES
from second_brain.triage.prompts import (
    DEFAULT_PROFILE,
    TRIAGE_PROFILE_NAMES,
    TRIAGE_PROMPTS,
    get_prompt,
)


def test_config_and_prompt_profile_names_in_sync() -> None:
    """The validated config profile names must match the prompt registry."""
    assert set(_TRIAGE_PROFILES) == set(TRIAGE_PROFILE_NAMES)


def test_all_profiles_present() -> None:
    expected = {"balanced", "technical", "skip_heavy", "project_heavy", "lenient"}
    assert expected == set(TRIAGE_PROMPTS)


def test_profiles_are_distinct() -> None:
    prompts = list(TRIAGE_PROMPTS.values())
    assert len(set(prompts)) == len(prompts)


def test_every_prompt_has_schema_and_examples() -> None:
    for name, prompt in TRIAGE_PROMPTS.items():
        assert '"decision"' in prompt, f"{name} missing schema"
        assert "Examples" in prompt, f"{name} missing examples"
        assert "Document:" in prompt, f"{name} missing few-shot document examples"


def test_get_prompt_falls_back_to_default() -> None:
    assert get_prompt("nonexistent-profile") == TRIAGE_PROMPTS[DEFAULT_PROFILE]


def test_technical_profile_biases_toward_keeping() -> None:
    assert "GENEROUS" in TRIAGE_PROMPTS["technical"]


def test_skip_heavy_profile_biases_toward_skipping() -> None:
    assert "aggressive" in TRIAGE_PROMPTS["skip_heavy"].lower()
