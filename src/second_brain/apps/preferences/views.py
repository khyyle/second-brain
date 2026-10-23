"""Settings contracts; vault config and Keychain integration remain implementation work."""

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
from second_brain.config import Config
from second_brain.django_config.scaffold import ErrorResponse, pending

router = Router(tags=["Preferences"])
MCPTarget = Literal["claude-desktop", "chatgpt-desktop", "cursor"]


@router.get("/catalog", response={200: Catalog, 501: ErrorResponse})
def get_catalog(request: HttpRequest) -> Catalog:
    """TODO: Serialize llm.SUPPORTED_MODELS/resolve_profile and file/profile choices.

    Replace Swift's duplicated catalog; keep application definitions in Python code.
    """
    pending("Read application catalog")


@router.get("/settings", response={200: Config, 501: ErrorResponse})
def read_settings(request: HttpRequest) -> Config:
    """TODO: Add preferences/services/configuration.py to load <vault>/config.yaml.

    Use settings.VAULT_DIR as the authoritative vault, validate with Config, and adopt
    legacy config/config.yaml + sources.json deliberately. Never include provider keys.
    """
    pending("Read settings")


@router.patch("/settings", response={200: Config, 501: ErrorResponse})
def patch_settings(request: HttpRequest, payload: SettingsUpdate) -> Config:
    """TODO: Merge payload.model_dump(exclude_unset=True) with current preferences,
    validate the merged Config, then save atomically under a lock. Do not reset omitted
    nested settings. Reject null sections. The running vault cannot change via this API.
    """
    pending("Update settings")


@router.get("/watched-folders", response={200: list[WatchedFolder], 501: ErrorResponse})
def watched_folders(request: HttpRequest) -> list[WatchedFolder]:
    """TODO: Return user sources from vault config, excluding built-in drop folders.

    Replaces SourcesStore and config._merge_user_sources once adoption is implemented.
    """
    pending("Read watched folders")


@router.put("/watched-folders", response={200: list[WatchedFolder], 501: ErrorResponse})
def replace_watched_folders(
    request: HttpRequest,
    payload: list[WatchedFolder],
) -> list[WatchedFolder]:
    """TODO: Validate unique/reserved names and paths; persist with settings atomically.

    Backend watchers will consume these settings; saving must not start a build.
    """
    pending("Update watched folders")


@router.get("/credentials/{provider}", response={200: CredentialStatus, 501: ErrorResponse})
def credential_status(request: HttpRequest, provider: str) -> CredentialStatus:
    """TODO: Report whether the backend can access this provider's Keychain credential.

    GUI saves secrets using native Keychain APIs. Establish shared access before workers
    use them; return configured status only, never the secret. No provider env fallback.
    """
    pending("Read credential availability")


@router.get("/diagnostics", response={200: Diagnostics, 501: ErrorResponse})
def diagnostics(request: HttpRequest) -> Diagnostics:
    """TODO: Adapt cli.doctor / ollama.client.check_ollama with a bounded timeout."""
    pending("Read dependency diagnostics")


@router.get("/integrations/mcp/{target}", response={200: IntegrationStatus, 501: ErrorResponse})
def integration_status(request: HttpRequest, target: MCPTarget) -> IntegrationStatus:
    """TODO: Inspect mcp_server.install's target/config metadata without changing it."""
    pending("Read MCP integration status")


@router.put("/integrations/mcp/{target}", response={200: IntegrationStatus, 501: ErrorResponse})
def install_integration(request: HttpRequest, target: MCPTarget) -> IntegrationStatus:
    """TODO: Call mcp_server.install.install_mcp_client after explicit GUI Connect action.

    Preserve unrelated client configuration; this intentionally changes an external file.
    """
    pending("Configure MCP integration")
