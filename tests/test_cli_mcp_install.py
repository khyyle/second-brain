"""Tests for installing the MCP server into supported desktop clients."""

from __future__ import annotations

import json
import stat
import tomllib
from pathlib import Path

import pytest
from click import ClickException

from second_brain.mcp_server import install


def test_resolve_mcp_client_requires_installed_application(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(install.Path, "is_dir", lambda _path: False)

    with pytest.raises(ClickException, match="Claude Desktop is not installed"):
        install.resolve_mcp_client("claude-desktop")

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
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    application_name: str,
    relative_config: str,
    config_format: str,
) -> None:
    home = tmp_path / "home"
    application = home / "Applications" / application_name
    application.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    client = install.resolve_mcp_client(target)

    assert client.config_file == home / relative_config
    assert client.config_format == config_format


def test_resolve_mcp_server_command_uses_generated_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    python_executable = tmp_path / ".venv" / "bin" / "python3"
    python_executable.parent.mkdir(parents=True)
    second_brain_executable = python_executable.with_name("second-brain")
    second_brain_executable.write_text("#!/bin/sh\n")
    second_brain_executable.chmod(0o700)
    monkeypatch.setattr(install.sys, "executable", str(python_executable))

    command, arguments = install.resolve_mcp_server_command()

    assert command == str(second_brain_executable)
    assert arguments == ["mcp", "serve"]


def test_install_mcp_client_creates_missing_config_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "home" / ".cursor" / "mcp.json"
    client = install.MCPInstallTarget(
        display_name="Cursor",
        config_file=config_file,
        config_format="json",
    )
    monkeypatch.setattr(install, "resolve_mcp_client", lambda _target: client)
    monkeypatch.setattr(
        install,
        "resolve_mcp_server_command",
        lambda: ("/venv/bin/second-brain", ["mcp", "serve"]),
    )

    installed_client = install.install_mcp_client("cursor")

    assert installed_client == client
    assert config_file.is_file()
    assert stat.S_IMODE(config_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(config_file.parent.stat().st_mode) & 0o077 == 0


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

    install.write_json_mcp_config(
        config_file,
        command="/venv/bin/second-brain",
        arguments=["mcp", "serve"],
    )

    document = json.loads(config_file.read_text())
    assert document["theme"] == "dark"
    assert document["mcpServers"]["other"]["command"] == "/usr/bin/other"
    assert document["mcpServers"]["second-brain"] == {
        "command": "/venv/bin/second-brain",
        "args": ["mcp", "serve"],
    }


def test_write_json_mcp_config_does_not_replace_malformed_file(tmp_path: Path) -> None:
    config_file = tmp_path / "mcp.json"
    malformed = '{"mcpServers":'
    config_file.write_text(malformed)

    with pytest.raises(ClickException, match="Cannot parse existing"):
        install.write_json_mcp_config(
            config_file,
            command="/venv/bin/second-brain",
            arguments=["mcp", "serve"],
        )

    assert config_file.read_text() == malformed


def test_write_json_mcp_config_creates_private_file(tmp_path: Path) -> None:
    config_file = tmp_path / ".cursor" / "mcp.json"
    config_file.parent.mkdir()

    install.write_json_mcp_config(
        config_file,
        command="/venv/bin/second-brain",
        arguments=["mcp", "serve"],
    )

    assert config_file.is_file()
    assert stat.S_IMODE(config_file.stat().st_mode) == 0o600


def test_write_json_mcp_config_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "managed.json"
    target.write_text('{"managed": true}')
    config_file = tmp_path / "mcp.json"
    config_file.symlink_to(target)

    with pytest.raises(ClickException, match="symbolic link"):
        install.write_json_mcp_config(
            config_file,
            command="/venv/bin/second-brain",
            arguments=["mcp", "serve"],
        )

    assert target.read_text() == '{"managed": true}'
    assert config_file.is_symlink()


def test_write_codex_mcp_config_preserves_other_settings_and_comments(tmp_path: Path) -> None:
    config_file = tmp_path / ".codex" / "config.toml"
    config_file.parent.mkdir()
    config_file.write_text(
        'model = "gpt-5.6-sol" # keep this comment\n\n'
        "[mcp_servers.other]\n"
        'command = "/usr/bin/other"\n'
    )

    install.write_codex_mcp_config(
        config_file,
        command="/venv/bin/second-brain",
        arguments=["mcp", "serve"],
    )

    rendered = config_file.read_text()
    document = tomllib.loads(rendered)
    assert "# keep this comment" in rendered
    assert document["model"] == "gpt-5.6-sol"
    assert document["mcp_servers"]["other"]["command"] == "/usr/bin/other"
    assert document["mcp_servers"]["second-brain"] == {
        "command": "/venv/bin/second-brain",
        "args": ["mcp", "serve"],
    }


def test_write_codex_mcp_config_does_not_replace_malformed_file(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    malformed = 'model = "unterminated'
    config_file.write_text(malformed)

    with pytest.raises(ClickException, match="Cannot parse existing"):
        install.write_codex_mcp_config(
            config_file,
            command="/venv/bin/second-brain",
            arguments=["mcp", "serve"],
        )

    assert config_file.read_text() == malformed
