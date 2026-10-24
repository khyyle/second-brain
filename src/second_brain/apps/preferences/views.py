"""HTTP entry points for preferences, credentials, and assistant integrations."""

from typing import Literal

from django.http import HttpRequest
from ninja import Router

from second_brain.apps.preferences.schemas import (
    Catalog,
    CredentialStatus,
    Diagnostics,
    IntegrationStatus,
    SettingsUpdate,
    WatchedFolder,
)
from second_brain.apps.preferences.services import (
    catalog,
    mcp_integrations,
    provider_credentials,
    user_settings,
)
from second_brain.apps.preferences.services import watched_folders as folders
from second_brain.django_config.schemas import ErrorResponse
from second_brain.ollama.client import check_ollama
from second_brain.pipeline_config import Preferences

router = Router(tags=["Preferences"])
MCPTarget = Literal["claude-desktop", "chatgpt-desktop", "cursor"]


@router.get("/catalog", response={200: Catalog, 400: ErrorResponse, 503: ErrorResponse})
def get_catalog(request: HttpRequest) -> dict:
    """List supported models, prices, file types, and default preferences."""
    return catalog.get_catalog()


@router.get("/settings", response={200: Preferences, 400: ErrorResponse, 503: ErrorResponse})
def read_settings(request: HttpRequest) -> Preferences:
    """Read the validated preferences for the active vault."""
    return user_settings.get_preferences()


@router.patch("/settings", response={200: Preferences, 400: ErrorResponse, 503: ErrorResponse})
def patch_settings(request: HttpRequest, payload: SettingsUpdate) -> Preferences:
    """Merge supplied preferences and atomically save the validated result."""
    return user_settings.update_preferences(payload.model_dump(mode="json", exclude_unset=True))


@router.get(
    "/watched-folders", response={200: list[WatchedFolder], 400: ErrorResponse, 503: ErrorResponse}
)
def watched_folders(request: HttpRequest) -> list[dict]:
    """List configured input folders and their enabled file types."""
    return folders.list_folders()


@router.put(
    "/watched-folders", response={200: list[WatchedFolder], 400: ErrorResponse, 503: ErrorResponse}
)
def replace_watched_folders(
    request: HttpRequest,
    payload: list[WatchedFolder],
) -> list[dict]:
    """Validate and replace the complete watched-folder configuration."""
    return folders.replace_folders([item.model_dump(mode="json") for item in payload])


@router.get(
    "/credentials/{provider}",
    response={200: CredentialStatus, 400: ErrorResponse, 503: ErrorResponse},
)
def credential_status(request: HttpRequest, provider: str) -> dict:
    """Report whether the provider credential is available in Keychain."""
    return {"provider": provider, "configured": bool(provider_credentials.read_api_key(provider))}


@router.get("/diagnostics", response={200: Diagnostics, 400: ErrorResponse, 503: ErrorResponse})
def diagnostics(request: HttpRequest) -> dict:
    """Check Ollama availability and required local models."""
    probe = check_ollama(user_settings.get_pipeline_config())
    return {
        "healthy": probe.reachable and not probe.missing_models,
        "reachable": probe.reachable,
        "host": probe.host,
        "required_models": list(probe.required_models),
        "missing_models": list(probe.missing_models),
        "message": probe.message(),
    }


@router.get(
    "/integrations/mcp/{target}",
    response={200: IntegrationStatus, 400: ErrorResponse, 503: ErrorResponse},
)
def integration_status(request: HttpRequest, target: MCPTarget) -> dict:
    """Check whether the selected assistant has the MCP integration installed."""
    return mcp_integrations.status(target)


@router.put(
    "/integrations/mcp/{target}",
    response={200: IntegrationStatus, 400: ErrorResponse, 503: ErrorResponse},
)
def install_integration(request: HttpRequest, target: MCPTarget) -> dict:
    """Configure the selected assistant while preserving other integrations."""
    return mcp_integrations.install(target)
