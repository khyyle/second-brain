"""Safe MCP configuration installation for supported desktop clients."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import click
import tomlkit
from tomlkit.exceptions import ParseError

MCP_SERVER_NAME = "second-brain"


@dataclass(frozen=True)
class MCPInstallTarget:
    """Configuration details for an installed desktop MCP client."""

    display_name: str
    config_file: Path
    config_format: Literal["json", "toml"]


def resolve_mcp_client(target: str) -> MCPInstallTarget:
    """
    Resolve an installed desktop client and its native MCP configuration.

    Parameters
    ----------
    target: str
        CLI identifier for the desktop client.

    Returns
    -------
    MCPInstallTarget
        Display name, configuration path, and configuration format.

    Raises
    ------
    click.ClickException
        If the target is unsupported or its application is not installed.
    """
    home_directory = Path.home()
    if target == "claude-desktop":
        display_name = "Claude Desktop"
        application_name = "Claude.app"
        config_file = (
            home_directory
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
        config_format: Literal["json", "toml"] = "json"
    elif target == "chatgpt-desktop":
        display_name = "ChatGPT Desktop"
        application_name = "ChatGPT.app"
        config_file = home_directory / ".codex" / "config.toml"
        config_format = "toml"
    elif target == "cursor":
        display_name = "Cursor"
        application_name = "Cursor.app"
        config_file = home_directory / ".cursor" / "mcp.json"
        config_format = "json"
    else:
        raise click.ClickException(f"Unsupported MCP target: {target}")

    search_directories = (
        Path("/Applications"),
        home_directory / "Applications",
    )
    expected_applications = [directory / application_name for directory in search_directories]
    if not any(application.is_dir() for application in expected_applications):
        searched = ", ".join(str(application) for application in expected_applications)
        raise click.ClickException(
            f"{display_name} is not installed. Expected to find it at: {searched}"
        )

    return MCPInstallTarget(
        display_name=display_name,
        config_file=config_file,
        config_format=config_format,
    )


def resolve_mcp_server_command() -> tuple[str, list[str]]:
    """
    Resolve the generated CLI command used to launch the local MCP server.

    Returns
    -------
    tuple[str, list[str]]
        Absolute executable path and module arguments.

    Raises
    ------
    click.ClickException
        If the generated CLI executable does not exist or cannot be executed.
    """
    interpreter = Path(sys.executable)
    try:
        command_path = interpreter.expanduser().absolute().with_name(MCP_SERVER_NAME)
    except (OSError, RuntimeError) as exc:
        raise click.ClickException(
            f"Could not locate the Second Brain executable beside {interpreter}"
        ) from exc
    if not command_path.exists():
        raise click.ClickException(f"Second Brain executable was not found: {command_path}")
    if not command_path.is_file() or not os.access(command_path, os.X_OK):
        raise click.ClickException(f"Second Brain executable is not runnable: {command_path}")
    return str(command_path), ["mcp", "serve"]


def _validate_config_path(path: Path) -> None:
    if path.is_symlink():
        raise click.ClickException(
            f"Cannot update MCP configuration because it is a symbolic link: {path}"
        )


def _atomic_write_text(path: Path, content: str) -> None:
    """Replace a configuration file without exposing a partially written file."""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
            temporary_path = Path(temporary_file.name)

        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    except OSError as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise click.ClickException(f"Could not update MCP configuration {path}: {exc}") from exc


def write_json_mcp_config(
    config_file: Path,
    *,
    command: str,
    arguments: list[str],
) -> None:
    """
    Add the server to a Claude or Cursor JSON configuration.

    Existing settings and other MCP servers are retained. Invalid files and
    symbolic links are rejected without modification.

    Parameters
    ----------
    config_file: Path
        JSON configuration to create or update.
    command: str
        Absolute executable used to start the server.
    arguments: list[str]
        Arguments passed to the server executable.
    """
    _validate_config_path(config_file)
    document: dict[str, object] = {}
    if config_file.exists():
        try:
            parsed = json.loads(config_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise click.ClickException(
                f"Cannot parse existing MCP configuration {config_file}: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise click.ClickException(
                f"Cannot parse existing MCP configuration {config_file}: root must be an object"
            )
        document = parsed

    servers = document.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise click.ClickException(
            f"Cannot update MCP configuration {config_file}: mcpServers must be an object"
        )
    servers[MCP_SERVER_NAME] = {"command": command, "args": arguments}
    _atomic_write_text(config_file, json.dumps(document, indent=2) + "\n")


def write_codex_mcp_config(
    config_file: Path,
    *,
    command: str,
    arguments: list[str],
) -> None:
    """
    Add the server to the TOML configuration shared by ChatGPT and Codex.

    Existing formatting, comments, settings, and other MCP servers are
    retained. Invalid files and symbolic links are rejected without modification.

    Parameters
    ----------
    config_file: Path
        TOML configuration to create or update.
    command: str
        Absolute executable used to start the server.
    arguments: list[str]
        Arguments passed to the server executable.
    """
    _validate_config_path(config_file)
    if config_file.exists():
        try:
            document = tomlkit.parse(config_file.read_text(encoding="utf-8"))
        except (OSError, ParseError) as exc:
            raise click.ClickException(
                f"Cannot parse existing Codex configuration {config_file}: {exc}"
            ) from exc
    else:
        document = tomlkit.document()

    if "mcp_servers" not in document:
        document["mcp_servers"] = tomlkit.table()
    servers = document["mcp_servers"]
    if not isinstance(servers, dict):
        raise click.ClickException(
            f"Cannot update Codex configuration {config_file}: mcp_servers must be a table"
        )

    server = tomlkit.table()
    server["command"] = command
    server["args"] = arguments
    servers[MCP_SERVER_NAME] = server
    _atomic_write_text(config_file, tomlkit.dumps(document))


def install_mcp_client(target: str) -> MCPInstallTarget:
    """
    Configure the local MCP server for an installed desktop client.

    Parameters
    ----------
    target: str
        CLI identifier for the desktop client.

    Returns
    -------
    MCPInstallTarget
        Details for the client that was configured.
    """
    client = resolve_mcp_client(target)
    command, arguments = resolve_mcp_server_command()
    try:
        client.config_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise click.ClickException(
            f"Could not create MCP configuration directory {client.config_file.parent}: {exc}"
        ) from exc

    if client.config_format == "toml":
        write_codex_mcp_config(
            client.config_file,
            command=command,
            arguments=arguments,
        )
    else:
        write_json_mcp_config(
            client.config_file,
            command=command,
            arguments=arguments,
        )
    return client
