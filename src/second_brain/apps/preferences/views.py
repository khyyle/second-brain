"""HTTP entry points for preferences and credentials."""

from django.http import HttpRequest
from django.views.decorators.cache import never_cache
from ninja import Router, Status
from ninja.decorators import decorate_view

from second_brain.apps.preferences.schemas import (
    Catalog,
    ConnectionStatus,
    LoginAnswer,
    LoginRequest,
    LoginStarted,
    ModelChoiceResponse,
    SettingsUpdate,
    WatchedFolder,
)
from second_brain.apps.preferences.services import catalog, user_settings
from second_brain.apps.preferences.services import watched_folders as folders
from second_brain.apps.preferences.services.agent_provider import model_selection, provider_auth
from second_brain.django_config.schemas import ErrorResponse
from second_brain.pipeline_config import Preferences

router = Router(tags=["Preferences"])


@router.get("/catalog", response={200: Catalog, 400: ErrorResponse, 503: ErrorResponse})
def get_catalog(request: HttpRequest) -> dict:
    """List supported file types, triage profiles, and grouping algorithms.

    Parameters
    ----------
    request: HttpRequest
        Incoming authenticated HTTP request.

    Returns
    -------
    dict
        Accepted file types, triage profiles, and clustering algorithms.
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


# Wrap Ninja's HTTP response, including authentication, validation and helper errors.
_private_response = decorate_view(never_cache)


@router.get(
    "/providers/{provider_id}/connection",
    response={200: ConnectionStatus, 400: ErrorResponse, 503: ErrorResponse},
)
@_private_response
async def get_connection(request: HttpRequest, provider_id: str) -> ConnectionStatus:
    """Report whether this provider has a saved login, independent of available models."""
    return ConnectionStatus(connected=await provider_auth.is_connected(provider_id))


@router.delete(
    "/providers/{provider_id}/connection",
    response={204: None, 400: ErrorResponse, 503: ErrorResponse},
)
@_private_response
async def disconnect_provider(request: HttpRequest, provider_id: str) -> Status[None]:
    """Remove the local credential; this does not revoke it at the provider."""
    await provider_auth.logout(provider_id)
    return Status(204, None)


@router.post(
    "/providers/{provider_id}/logins",
    response={200: LoginStarted, 400: ErrorResponse, 503: ErrorResponse},
)
@_private_response
async def start_provider_login(
    request: HttpRequest, provider_id: str, payload: LoginRequest
) -> LoginStarted:
    """Start a login with the selected method; read next-event to follow its progress."""
    return LoginStarted(login_id=await provider_auth.start_login(provider_id, payload.method))


@router.post(
    "/logins/{login_id}/next-event",
    response={
        200: provider_auth.LoginEvent,
        400: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
        503: ErrorResponse,
    },
)
@_private_response
async def next_login_event(request: HttpRequest, login_id: str) -> provider_auth.LoginEvent:
    """Wait for one login event, including prompts and terminal confirmation."""
    return await provider_auth.read_login_event(login_id)


@router.post(
    "/logins/{login_id}/answers", response={204: None, 404: ErrorResponse, 409: ErrorResponse}
)
@_private_response
async def submit_login_answer(
    request: HttpRequest, login_id: str, payload: LoginAnswer
) -> Status[None]:
    """Answer a pending prompt once. Input goes only to that login's helper stdin."""
    await provider_auth.answer_login(
        login_id, payload.prompt_id, payload.answer.get_secret_value()
    )
    return Status(204, None)


@router.delete(
    "/logins/{login_id}",
    response={
        204: None,
        400: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
        503: ErrorResponse,
    },
)
@_private_response
async def cancel_provider_login(request: HttpRequest, login_id: str) -> Status[None]:
    """Stop a pending login without disconnecting an already saved account."""
    await provider_auth.cancel_login(login_id)
    return Status(204, None)
