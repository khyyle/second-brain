"""Provider and model choices for Settings."""

import asyncio
import time

from asgiref.sync import async_to_sync
from pydantic import BaseModel, Field, ValidationError

from second_brain.apps.preferences.services.agent_provider import provider_auth
from second_brain.apps.preferences.services.agent_provider._errors import settings_errors
from second_brain.cloud_llm.pi import pi_helper
from second_brain.django_config.errors import ConflictError, DependencyError, ServiceError

# Estimates are recomputed for every desktop snapshot, far more often than a login changes.
_ESTIMATE_MODEL_SECONDS = 30
# A snapshot waits for this lookup, and Keychain can block on a prompt. Estimates can wait.
_ESTIMATE_LOOKUP_SECONDS = 5


class PriceTier(BaseModel):
    """Rates that replace a model's base rates for a request above an input size."""

    input_tokens_above: int = Field(ge=0, validation_alias="inputTokensAbove")
    input: float = Field(ge=0)
    output: float = Field(ge=0)


class ModelPrice(BaseModel):
    """What a model costs through the API, in US dollars per million tokens."""

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    tiers: list[PriceTier] = []

    def rates_for(self, input_tokens: float) -> tuple[float, float]:
        """Input and output rates for one request with this many input tokens.

        Some models cost more per token once a prompt passes a size. Pi applies the
        highest tier the request exceeds, and so does this.
        """
        exceeded = [tier for tier in self.tiers if input_tokens > tier.input_tokens_above]
        rates = max(exceeded, key=lambda tier: tier.input_tokens_above, default=self)
        return rates.input, rates.output


class ModelChoice(BaseModel):
    """One model a saved login offers, as Pi's catalog describes it."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    # Tokens the model accepts in one request.
    context_window: int = Field(gt=0, validation_alias="contextWindow")
    # None when Pi has no price for the model.
    price: ModelPrice | None


class ProviderChoice(BaseModel):
    id: str = Field(min_length=1)
    name: str
    login_methods: list[str] = Field(validation_alias="loginMethods")


async def list_providers() -> list[ProviderChoice]:
    """List built-in Pi providers and their interactive login methods."""
    with settings_errors():
        items = await pi_helper.run_json("list-providers")
    if not isinstance(items, list):
        raise DependencyError("Expected a list from the provider helper.")
    try:
        return [ProviderChoice.model_validate(item) for item in items]
    except ValidationError:
        raise DependencyError("Invalid provider list from the provider helper.") from None


async def list_models(provider_id: str) -> list[ModelChoice]:
    """List a provider's model choices without starting sign-in or refreshing credentials.

    Parameters
    ----------
    provider_id : str
        Provider ID from the Pi catalog, not a login ID.

    Returns
    -------
    list[ModelChoice]
        Catalog entries for the saved connection, or an empty list if no credential
        is saved. A listed model does not guarantee the account has access to it.

    Raises
    ------
    ServiceError
        The provider ID is invalid or unknown.
    DependencyError
        The helper could not start, read the saved connection, or return a valid model list.
    """
    with settings_errors():
        items = await pi_helper.run_json("list-models", provider_id)
    if not isinstance(items, list):
        raise DependencyError("Invalid model list from the provider helper.")

    try:
        return [ModelChoice.model_validate(item) for item in items]
    except ValidationError:
        raise DependencyError("Invalid model choice from the provider helper.") from None


async def chosen_model(provider_id: str, model_id: str) -> ModelChoice:
    """Find the model chosen on the Build tab among those the saved login offers.

    Parameters
    ----------
    provider_id : str
        Provider ID saved in Settings.
    model_id : str
        Model ID saved on the Build tab, or empty if none is chosen yet.

    Returns
    -------
    ModelChoice
        The chosen model as Pi's catalog describes it for this login.

    Raises
    ------
    ConflictError
        No login is saved for the provider, or the login does not offer the model.
        The reason says which screen fixes it.
    DependencyError
        The helper could not start or read the saved login.
    """
    if not provider_id:
        raise ConflictError("Connect a provider in Settings.")
    for choice in await list_models(provider_id):
        if choice.id == model_id:
            return choice
    if not await provider_auth.is_connected(provider_id):
        raise ConflictError("Connect a provider in Settings.")
    raise ConflictError("Choose a model on the Build tab.")


_estimate_model: tuple[tuple[str, str], float, ModelChoice | None] | None = None


async def _offered_model(provider_id: str, model_id: str) -> ModelChoice | None:
    async with asyncio.timeout(_ESTIMATE_LOOKUP_SECONDS):
        models = await list_models(provider_id)
    return next((model for model in models if model.id == model_id), None)


def model_for_estimates(provider_id: str, model_id: str) -> ModelChoice | None:
    """Return the chosen model for pricing estimates, remembered for a short while.

    Parameters
    ----------
    provider_id : str
        Provider ID saved in Settings.
    model_id : str
        Model ID saved on the Build tab, or empty if none is chosen yet.

    Returns
    -------
    ModelChoice | None
        The chosen model, or None when there is nothing to price: no login, no model,
        or the helper failed or was slow. An estimate must never fail or stall the
        screen that shows it.

    Notes
    -----
    A login that changes while the same provider and model stay chosen is noticed after
    at most ``_ESTIMATE_MODEL_SECONDS``. Builds do not use this; they ask the helper.
    """
    global _estimate_model
    if not provider_id or not model_id:
        return None
    choice = (provider_id, model_id)
    if _estimate_model is not None:
        remembered, expires, model = _estimate_model
        if remembered == choice and time.monotonic() < expires:
            return model
    try:
        model = async_to_sync(_offered_model)(provider_id, model_id)
    except (ServiceError, TimeoutError):
        model = None
    _estimate_model = (choice, time.monotonic() + _ESTIMATE_MODEL_SECONDS, model)
    return model
