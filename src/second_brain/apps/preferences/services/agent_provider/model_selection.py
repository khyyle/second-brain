"""Model choices for the connected provider in Settings."""

from typing import TypedDict

from second_brain.apps.preferences.services.agent_provider._errors import settings_errors
from second_brain.cloud_llm.pi import pi_helper
from second_brain.django_config.errors import DependencyError


class ModelChoice(TypedDict):
    id: str
    name: str


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
        if isinstance(item, dict):
            model_id, name = item.get("id"), item.get("name")
            if isinstance(model_id, str) and model_id and isinstance(name, str) and name:
                choices.append({"id": model_id, "name": name})
                continue
        raise DependencyError("Invalid model choice from the provider helper.")
    return choices
