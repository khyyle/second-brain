import json
import os
import socket
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest
from llama_cpp.server.settings import ConfigFileSettings, ModelSettings

from second_brain.apps.inference import server


def test_inherited_socket_controls_binding_and_private_config_overrides_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    config = ConfigFileSettings(
        host="0.0.0.0",
        port=0,
        api_key="test-token",
        interrupt_requests=False,
        models=[ModelSettings(model="/model.gguf", model_alias="test-model")],
    )
    path.write_text(config.model_dump_json())
    for key, value in (("HOST", "0.0.0.0"), ("PORT", "8000"), ("CONFIG_FILE", "/other.json")):
        monkeypatch.setenv(key, value)
    app_factory = Mock(return_value=Mock())
    monkeypatch.setattr(server, "create_app", app_factory)
    runner = Mock()
    monkeypatch.setattr(server.uvicorn, "Server", lambda config: runner)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        address = listener.getsockname()

        def run(*, sockets: list[socket.socket]) -> None:
            assert sockets[0].getsockname() == address
            assert "CONFIG_FILE" not in os.environ

        runner.run.side_effect = run
        server.serve(path, os.dup(listener.fileno()), os.getppid())

    supplied = app_factory.call_args.kwargs
    assert supplied["server_settings"].api_key == "test-token"
    assert supplied["server_settings"].interrupt_requests is False
    assert supplied["model_settings"] == config.models
    runner.run.assert_called_once()


@pytest.mark.parametrize(
    "listen_host,token,error",
    [
        ("0.0.0.0", "token", "Inference socket must be bound to 127.0.0.1."),
        ("127.0.0.1", None, "Inference authentication token is missing."),
        ("127.0.0.1", "", "Inference authentication token is missing."),
    ],
)
def test_unsafe_server_is_rejected_before_loading_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    listen_host: str,
    token: str | None,
    error: str,
) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"host": "127.0.0.1", "api_key": token, "models": []}))
    app_factory = Mock()
    monkeypatch.setattr(server, "create_app", app_factory)
    with socket.socket() as listener:
        listener.bind((listen_host, 0))
        with pytest.raises(ValueError) as caught:
            server.serve(path, os.dup(listener.fileno()), os.getppid())
    assert str(caught.value) == error
    app_factory.assert_not_called()


def test_parent_loss_exits_even_if_native_model_code_is_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server.os, "getppid", lambda: 1)
    exit_process = Mock(side_effect=SystemExit(1))
    monkeypatch.setattr(server.os, "_exit", exit_process)
    stop = Mock()
    stop.wait.return_value = False

    with pytest.raises(SystemExit):
        server._watch_parent(123, stop)

    exit_process.assert_called_once_with(1)


def test_completed_server_does_not_exit_from_parent_watcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exit_process = Mock()
    monkeypatch.setattr(server.os, "_exit", exit_process)
    stop = threading.Event()
    stop.set()

    server._watch_parent(123, stop)

    exit_process.assert_not_called()


def test_server_rejects_model_switching(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        ConfigFileSettings(
            host="127.0.0.1",
            api_key="test-token",
            models=[ModelSettings(model="/first.gguf"), ModelSettings(model="/second.gguf")],
        ).model_dump_json()
    )
    app_factory = Mock()
    monkeypatch.setattr(server, "create_app", app_factory)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        with pytest.raises(ValueError, match="exactly one model"):
            server.serve(path, os.dup(listener.fileno()), os.getppid())
    app_factory.assert_not_called()
