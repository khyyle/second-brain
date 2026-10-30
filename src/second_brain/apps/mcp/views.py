"""HTTP endpoints for connecting desktop assistants to the local MCP server."""

from typing import Literal

from django.http import HttpRequest
from ninja import Router

from second_brain.apps.mcp.schemas import IntegrationStatus
from second_brain.apps.mcp.services import integrations
from second_brain.django_config.schemas import ErrorResponse

router = Router(tags=["MCP"])
MCPTarget = Literal["claude-desktop", "chatgpt-desktop", "cursor"]


@router.get(
    "/integrations/mcp/{target}",
    response={200: IntegrationStatus, 400: ErrorResponse, 503: ErrorResponse},
)
def integration_status(request: HttpRequest, target: MCPTarget) -> dict:
    """Check whether the selected assistant has the MCP integration installed.

    Parameters
    ----------
    request: HttpRequest
        Incoming authenticated HTTP request.
    target: MCPTarget
        Supported MCP client identifier.

    Returns
    -------
    dict
        Client installation and configuration status for this vault.
    """
    return integrations.status(target)


@router.put(
    "/integrations/mcp/{target}",
    response={200: IntegrationStatus, 400: ErrorResponse, 503: ErrorResponse},
)
def install_integration(request: HttpRequest, target: MCPTarget) -> dict:
    """Configure the selected assistant while preserving other integrations.

    Parameters
    ----------
    request: HttpRequest
        Incoming authenticated HTTP request.
    target: MCPTarget
        Supported MCP client whose configuration should receive this vault entry.

    Returns
    -------
    dict
        Client status after saving the MCP server entry.
    """
    return integrations.install(target)
