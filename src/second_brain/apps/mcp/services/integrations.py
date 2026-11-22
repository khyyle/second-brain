"""Inspect or explicitly install the local MCP client entry."""

import json
import tomllib

import click
from django.conf import settings

from second_brain.apps.mcp.services.client_configuration import (
    MCP_SERVER_NAME,
    resolve_mcp_client,
    resolve_mcp_server_command,
    write_codex_mcp_config,
    write_json_mcp_config,
)
from second_brain.django_config.errors import ServiceError
from second_brain.django_config.vault_storage import vault_lock


def _command() -> tuple[str, list[str]]:
    command, arguments = resolve_mcp_server_command()
    return command, [
        "--vault",
        str(settings.VAULT_DIR),
        "--port",
        str(settings.BACKEND_PORT),
        *arguments,
    ]


def status(target: str) -> dict:
    """Inspect the target's server entry without changing external client files.

    Parameters
    ----------
    target: str
        Supported MCP client identifier.

    Returns
    -------
    dict
        Target, installed and configured flags, and a status message. Configured is true
        only when command and arguments match this vault.
    """
    try:
        client = resolve_mcp_client(target)
    except click.ClickException as exc:
        return {"target": target, "installed": False, "configured": False, "message": str(exc)}
    configured = False
    if client.config_file.exists():
        try:
            text = client.config_file.read_text()
            document = tomllib.loads(text) if client.config_format == "toml" else json.loads(text)
            servers = document.get(
                "mcp_servers" if client.config_format == "toml" else "mcpServers", {}
            )
            entry = servers.get(MCP_SERVER_NAME, {})
            command, arguments = _command()
            configured = entry.get("command") == command and entry.get("args") == arguments
        except (ValueError, OSError, AttributeError, click.ClickException):
            pass
    return {
        "target": target,
        "installed": True,
        "configured": configured,
        "message": "Configured for this vault." if configured else "Connection setup is needed.",
    }


def install(target: str) -> dict:
    """Write this vault's MCP server entry while preserving other client entries.

    Parameters
    ----------
    target: str
        Supported MCP client whose configuration file should be updated.

    Returns
    -------
    dict
        Installation and configuration status after writing the entry.
    """
    try:
        with vault_lock("mcp-install"):
            client = resolve_mcp_client(target)
            command, arguments = _command()
            client.config_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            writer = (
                write_codex_mcp_config if client.config_format == "toml" else write_json_mcp_config
            )
            writer(client.config_file, command=command, arguments=arguments)
    except click.ClickException as exc:
        raise ServiceError(str(exc)) from exc
    return status(target)
