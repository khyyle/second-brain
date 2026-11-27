"""HTTP entry points for preferences and credentials."""

from django.http import HttpRequest
from ninja import Router

from second_brain.apps.preferences.schemas import (
    Catalog,
    CredentialStatus,
    ModelChoiceResponse,
    SettingsUpdate,
    WatchedFolder,
)
from second_brain.apps.preferences.services import (
    catalog,
    provider_credentials,
    user_settings,
)
from second_brain.apps.preferences.services import watched_folders as folders
from second_brain.apps.preferences.services.agent_provider import model_selection
from second_brain.django_config.schemas import ErrorResponse
from second_brain.pipeline_config import Preferences

router = Router(tags=["Preferences"])


@router.get("/catalog", response={200: Catalog, 400: ErrorResponse, 503: ErrorResponse})
def get_catalog(request: HttpRequest) -> dict:
    """List supported models, prices, file types, triage profiles, and grouping algorithms.

    Parameters
    ----------
    request: HttpRequest
        Incoming authenticated HTTP request.

    Returns
    -------
    dict
        Providers and models with prices and context limits, accepted file types,
        triage profiles, and clustering algorithms.
    """
    return catalog.get_catalog()


@router.get("/providers", response={200: list[model_selection.ProviderChoice], 503: ErrorResponse})
async def list_providers(request: HttpRequest) -> list[model_selection.ProviderChoice]:
    """List Pi providers and their login methods."""
    return await model_selection.list_providers()


@router.get(
    "/providers/{provider_id}/models",
    response={200: list[ModelChoiceResponse], 400: ErrorResponse, 503: ErrorResponse},
)
async def list_provider_models(
    request: HttpRequest, provider_id: str
) -> list[model_selection.ModelChoice]:
    """List LLM options for a connected provider."""
    return await model_selection.list_models(provider_id)


@router.get("/settings", response={200: Preferences, 400: ErrorResponse, 503: ErrorResponse})
def read_settings(request: HttpRequest) -> Preferences:
    """Read the validated preferences for the active vault.

    Parameters
    ----------
    request: HttpRequest
        Incoming authenticated HTTP request.

    Returns
    -------
    Preferences
        Validated preferences loaded from the active vault.
    """
    return user_settings.get_preferences()


@router.patch("/settings", response={200: Preferences, 400: ErrorResponse, 503: ErrorResponse})
def patch_settings(request: HttpRequest, payload: SettingsUpdate) -> Preferences:
    """Merge supplied preferences and atomically save the validated result.

    Parameters
    ----------
    request: HttpRequest
        Incoming authenticated HTTP request.
    payload: SettingsUpdate
        Preference fields to update. Nested fields merge; the sources map replaces all
        watched folders.

    Returns
    -------
    Preferences
        Complete validated preferences after saving the changes.
    """
    return user_settings.update_preferences(payload.model_dump(mode="json", exclude_unset=True))


@router.get(
    "/watched-folders", response={200: list[WatchedFolder], 400: ErrorResponse, 503: ErrorResponse}
)
def watched_folders(request: HttpRequest) -> list[dict]:
    """List configured input folders and their enabled file types.

    Parameters
    ----------
    request: HttpRequest
        Incoming authenticated HTTP request.

    Returns
    -------
    list[dict]
        Named external source-folder definitions.
    """
    return folders.list_folders()


@router.put(
    "/watched-folders", response={200: list[WatchedFolder], 400: ErrorResponse, 503: ErrorResponse}
)
def replace_watched_folders(
    request: HttpRequest,
    payload: list[WatchedFolder],
) -> list[dict]:
    """Validate and replace the complete watched-folder configuration.

    Parameters
    ----------
    request: HttpRequest
        Incoming authenticated HTTP request.
    payload: list[WatchedFolder]
        Complete set of watched folders to validate and save.

    Returns
    -------
    list[dict]
        Persisted watched-folder definitions.
    """
    return folders.replace_folders([item.model_dump(mode="json") for item in payload])


@router.get(
    "/credentials/{provider}",
    response={200: CredentialStatus, 400: ErrorResponse, 503: ErrorResponse},
)
def credential_status(request: HttpRequest, provider: str) -> dict:
    """Report whether the provider credential is available in Keychain.

    Parameters
    ----------
    request: HttpRequest
        Incoming authenticated HTTP request.
    provider: str
        Provider whose local credential availability should be checked.

    Returns
    -------
    dict
        Provider name and configured flag; no credential value is returned.
    """
    return {"provider": provider, "configured": bool(provider_credentials.read_api_key(provider))}
