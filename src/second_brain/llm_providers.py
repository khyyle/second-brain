"""Compilation LLM provider profiles.

The compilation agent uses the Anthropic Messages API. DeepSeek is compatible
with the Anthropic API via an alternate base_url so swapping providers is a matter
of transport config like base URL, API key, model, and api call kwargs.

Notes
-----
DeepSeek's Anthropic-compatible endpoint ignores ``cache_control`` but
applies automatic server-side prefix caching, billed at a much lower
cache-hit rate. We therefore do not send ``cache_control`` to DeepSeek,
and the cost estimate below (cache-miss pricing) is an upper bound for it.
Explicit caching is used for Anthropic models.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Selectable models per provider.
SUPPORTED_MODELS: dict[str, tuple[str, ...]] = {
    "anthropic": ("claude-sonnet-4-6", "claude-opus-4-8", "claude-haiku-4-5"),
    "deepseek": ("deepseek-v4-flash", "deepseek-v4-pro"),
}

# USD per 1M tokens (cache-miss input, output)
_MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "deepseek-v4-flash": (0.14, 0.28),
    "deepseek-v4-pro": (0.435, 0.87),
}


@dataclass(frozen=True)
class _ProviderSpec:
    api_key_env: str
    base_url: str | None
    prompt_caching: bool
    cache_read_multiplier: float
    cache_write_multiplier: float


_PROVIDERS: dict[str, _ProviderSpec] = {
    "anthropic": _ProviderSpec(
        api_key_env="ANTHROPIC_API_KEY",
        base_url=None,
        prompt_caching=True,
        cache_read_multiplier=0.1,
        cache_write_multiplier=2.0,
    ),
    # DeepSeek auto-caches, so multipliers are kept inert
    "deepseek": _ProviderSpec(
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com/anthropic",
        prompt_caching=False,
        cache_read_multiplier=0,
        cache_write_multiplier=0,
    ),
}


@dataclass(frozen=True)
class ProviderProfile:
    """Resolved transport + pricing for a compilation provider.

    Parameters
    ----------
    name: str
        Provider key (``"anthropic"`` or ``"deepseek"``).
    model: str
        Model id passed to the Anthropic Messages API.
    api_key_env: str
        Environment variable holding the API key.
    base_url: str | None
        Override base URL, or ``None`` for Anthropic's default.
    prompt_caching: bool
        Whether to send Anthropic ``cache_control`` markers (DeepSeek
        ignores them, so it's disabled there).
    input_price_per_mtok: float
        Cache-miss input price in USD per 1M tokens.
    output_price_per_mtok: float
        Output price in USD per 1M tokens.
    cache_read_multiplier: float
        Price of a cache-read token as a multiple of the input price.
    cache_write_multiplier: float
        Price of a cache-write token as a multiple of the input price.
    """

    name: str
    model: str
    api_key_env: str
    base_url: str | None
    prompt_caching: bool
    input_price_per_mtok: float
    output_price_per_mtok: float
    cache_read_multiplier: float
    cache_write_multiplier: float

    def estimate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        *,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        """Return a USD cost estimate from per-class token counts.

        ``input_tokens`` is the uncached input (the provider reports cached
        tokens separately), billed at full rate. Cache reads and writes are
        billed at their per-provider multiples of the input price. With no
        cache tokens this reduces to plain input+output pricing.
        """
        base = self.input_price_per_mtok
        return (
            input_tokens / 1_000_000 * base
            + cache_read_tokens / 1_000_000 * base * self.cache_read_multiplier
            + cache_write_tokens / 1_000_000 * base * self.cache_write_multiplier
            + output_tokens / 1_000_000 * self.output_price_per_mtok
        )

    def client_kwargs(self) -> dict:
        """Build kwargs for ``anthropic.Anthropic`` from the environment."""
        kwargs: dict = {}
        api_key = os.environ.get(self.api_key_env)
        if api_key:
            kwargs["api_key"] = api_key
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return kwargs


def resolve_profile(provider: str, model: str | None) -> ProviderProfile:
    """
    Resolve a provider name and optional model into a concrete profile.

    The configured ``model`` is honored only when it matches the selected
    provider (by name prefix); otherwise the provider's default model is
    used, so switching provider without updating ``model`` still works.

    Parameters
    ----------
    provider: str
        Provider key (i.e. ``"anthropic"``). Must be in _PROVIDERS
    model: str | None
        Configured model id, or ``None`` to use the provider default.

    Returns
    -------
    ProviderProfile
        Fully resolved transport and pricing.

    Raises
    ------
    ValueError
        If *provider* is unknown, or *model* is set but not supported by
        that provider. Fails fast rather than silently substituting a
        different model.
    """
    spec = _PROVIDERS.get(provider)
    if spec is None:
        raise ValueError(f"Unknown compilation provider {provider!r}; known: {sorted(_PROVIDERS)}")

    supported = SUPPORTED_MODELS[provider]
    chosen_model = model or supported[0]
    if chosen_model not in supported:
        raise ValueError(
            f"Model {chosen_model!r} is not supported for provider {provider!r}; "
            f"choose one of {supported}"
        )

    input_price, output_price = _MODEL_PRICES[chosen_model]
    return ProviderProfile(
        name=provider,
        model=chosen_model,
        api_key_env=spec.api_key_env,
        base_url=spec.base_url,
        prompt_caching=spec.prompt_caching,
        input_price_per_mtok=input_price,
        output_price_per_mtok=output_price,
        cache_read_multiplier=spec.cache_read_multiplier,
        cache_write_multiplier=spec.cache_write_multiplier,
    )
