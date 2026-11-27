"""Provider and model choices for Settings."""

from typing import TypedDict

from pydantic import BaseModel, Field, ValidationError

from second_brain.apps.preferences.services.agent_provider import provider_auth
from second_brain.apps.preferences.services.agent_provider._errors import settings_errors
from second_brain.cloud_llm.pi import pi_helper
from second_brain.django_config.errors import ConflictError, DependencyError


class ModelChoice(TypedDict):
    id: str
    name: str
    context_window: int  # Tokens the model accepts in one request.


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

    choices: list[ModelChoice] = []
    for item in items:
        match item:
            case {"id": str(model_id), "name": str(name), "contextWindow": int(window)} if (
                model_id and name and window > 0
            ):
                choices.append({"id": model_id, "name": name, "context_window": window})
            case _:
                raise DependencyError("Invalid model choice from the provider helper.")
    return choices


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
    for choice in await list_models(provider_id):
        if choice["id"] == model_id:
            return choice
    if not await provider_auth.is_connected(provider_id):
        raise ConflictError("Connect a provider in Settings.")
    raise ConflictError("Choose a model on the Build tab.")
