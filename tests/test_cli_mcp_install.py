"""Tests for installing the MCP server into supported desktop clients."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from click import ClickException

from second_brain import cli


def test_resolve_mcp_client_requires_installed_application(tmp_path: Path) -> None:
    home = tmp_path / "home"
    applications = tmp_path / "Applications"

    with pytest.raises(ClickException, match="Claude Desktop is not installed"):
        cli._resolve_mcp_client(
            "claude-desktop",
            home=home,
            application_directories=(applications,),
        )

    assert not (home / "Library/Application Support/Claude").exists()


@pytest.mark.parametrize(
    ("target", "application_name", "relative_config", "config_format"),
    [
        (
            "claude-desktop",
            "Claude.app",
            "Library/Application Support/Claude/claude_desktop_config.json",
            "json",
        ),
        ("cursor", "Cursor.app", ".cursor/mcp.json", "json"),
        ("chatgpt-desktop", "ChatGPT.app", ".codex/config.toml", "toml"),
    ],
)
def test_resolve_mcp_client_uses_native_config(
    tmp_path: Path,
    target: str,
    application_name: str,
    relative_config: str,
    config_format: str,
) -> None:
    home = tmp_path / "home"
    applications = tmp_path / "Applications"
    application = applications / application_name
    application.mkdir(parents=True)

    client = cli._resolve_mcp_client(
        target,
        home=home,
        application_directories=(applications,),
    )

    assert client.application_path == application
    assert client.config_file == home / relative_config
    assert client.config_format == config_format


def test_resolve_mcp_server_command_requires_executable(tmp_path: Path) -> None:
    missing_python = tmp_path / "python"

    with pytest.raises(ClickException, match="Python executable"):
        cli._resolve_mcp_server_command(missing_python)


def test_write_json_mcp_config_preserves_other_settings(tmp_path: Path) -> None:
    config_file = tmp_path / "Claude" / "config.json"
    config_file.parent.mkdir()
    config_file.write_text(
        json.dumps(
            {
                "theme": "dark",
                "mcpServers": {
                    "other": {
                        "command": "/usr/bin/other",
                        "args": [],
                    }
                },
            }
        )
    )

    cli._write_json_mcp_config(
        config_file,
        command="/venv/bin/python",
        arguments=["-m", "second_brain.mcp_server.server"],
    )

    document = json.loads(config_file.read_text())
    assert document["theme"] == "dark"
    assert document["mcpServers"]["other"]["command"] == "/usr/bin/other"
    assert document["mcpServers"]["second-brain"] == {
        "command": "/venv/bin/python",
        "args": ["-m", "second_brain.mcp_server.server"],
    }


def test_write_json_mcp_config_does_not_replace_malformed_file(tmp_path: Path) -> None:
    config_file = tmp_path / "mcp.json"
    malformed = '{"mcpServers":'
    config_file.write_text(malformed)

    with pytest.raises(ClickException, match="Cannot parse existing"):
        cli._write_json_mcp_config(
            config_file,
            command="/venv/bin/python",
            arguments=["-m", "second_brain.mcp_server.server"],
        )

    assert config_file.read_text() == malformed


def test_write_codex_mcp_config_preserves_other_settings_and_comments(tmp_path: Path) -> None:
    config_file = tmp_path / ".codex" / "config.toml"
    config_file.parent.mkdir()
    config_file.write_text(
        'model = "gpt-5.6-sol" # keep this comment\n\n'
        "[mcp_servers.other]\n"
        'command = "/usr/bin/other"\n'
    )

    cli._write_codex_mcp_config(
        config_file,
        command="/venv/bin/python",
        arguments=["-m", "second_brain.mcp_server.server"],
    )

    rendered = config_file.read_text()
    document = tomllib.loads(rendered)
    assert "# keep this comment" in rendered
    assert document["model"] == "gpt-5.6-sol"
    assert document["mcp_servers"]["other"]["command"] == "/usr/bin/other"
    assert document["mcp_servers"]["second-brain"] == {
        "command": "/venv/bin/python",
        "args": ["-m", "second_brain.mcp_server.server"],
    }


def test_write_codex_mcp_config_does_not_replace_malformed_file(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    malformed = 'model = "unterminated'
    config_file.write_text(malformed)

    with pytest.raises(ClickException, match="Cannot parse existing"):
        cli._write_codex_mcp_config(
            config_file,
            command="/venv/bin/python",
            arguments=["-m", "second_brain.mcp_server.server"],
        )

    assert config_file.read_text() == malformed
