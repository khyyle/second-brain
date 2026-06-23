"""Tests for compilation provider profile resolution."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from second_brain.config import CompilationConfig
from second_brain.llm_providers import SUPPORTED_MODELS, resolve_profile


def test_anthropic_profile_defaults() -> None:
    profile = resolve_profile("anthropic", "claude-sonnet-4-6")
    assert profile.name == "anthropic"
    assert profile.model == "claude-sonnet-4-6"
    assert profile.base_url is None
    assert profile.prompt_caching is True
    assert profile.api_key_env == "ANTHROPIC_API_KEY"
    assert profile.input_price_per_mtok == 3.0


def test_deepseek_profile_uses_anthropic_compatible_endpoint() -> None:
    profile = resolve_profile("deepseek", None)
    assert profile.base_url == "https://api.deepseek.com/anthropic"
    assert profile.api_key_env == "DEEPSEEK_API_KEY"
    # DeepSeek ignores cache_control, so we don't send it.
    assert profile.prompt_caching is False
    # Default model when none is configured (Flash: cheapest, fits the task).
    assert profile.model == "deepseek-v4-flash"


def test_deepseek_model_override_to_pro() -> None:
    profile = resolve_profile("deepseek", "deepseek-v4-pro")
    assert profile.model == "deepseek-v4-pro"
    assert profile.input_price_per_mtok == 0.435
    assert profile.output_price_per_mtok == 0.87


def test_mismatched_model_raises() -> None:
    # A leftover Claude model name must fail fast, not silently swap models.
    with pytest.raises(ValueError, match="not supported for provider 'deepseek'"):
        resolve_profile("deepseek", "claude-sonnet-4-6")


def test_supported_models_default_is_first_entry() -> None:
    for provider, models in SUPPORTED_MODELS.items():
        assert resolve_profile(provider, None).model == models[0]


def test_config_rejects_mismatched_provider_model() -> None:
    # Switching provider without updating the (Claude) default model fails fast.
    with pytest.raises(ValidationError):
        CompilationConfig(provider="deepseek")


def test_config_rejects_unknown_provider() -> None:
    with pytest.raises(ValidationError):
        CompilationConfig(provider="gpt", model="gpt-5")


def test_config_accepts_valid_deepseek_pair() -> None:
    cfg = CompilationConfig(provider="deepseek", model="deepseek-v4-flash")
    assert cfg.provider == "deepseek"
    assert cfg.model == "deepseek-v4-flash"


def test_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="Unknown compilation provider"):
        resolve_profile("gpt", "gpt-5")


def test_estimate_cost_uses_model_pricing() -> None:
    profile = resolve_profile("deepseek", "deepseek-v4-pro")
    # 1M input @ 0.435 + 1M output @ 0.87
    assert profile.estimate_cost(1_000_000, 1_000_000) == pytest.approx(1.305)


def test_client_kwargs_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    kwargs = resolve_profile("deepseek", None).client_kwargs()
    assert kwargs == {
        "api_key": "sk-test",
        "base_url": "https://api.deepseek.com/anthropic",
    }
