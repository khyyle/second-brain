import json
import os
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import Mock, call

import httpx
import pytest

from second_brain.apps.inference import worker
from second_brain.apps.inference.catalog import ModelPurpose
from second_brain.apps.inference.errors import InferenceUnavailableError
from second_brain.apps.inference.services import model_files
from second_brain.apps.inference.services.worker_status import WorkerStatus, read_worker_status


@pytest.fixture
def server_config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.json"
    path.write_text(
        worker.ConfigFileSettings(
            host="127.0.0.1",
            port=0,
            api_key="test-inference-key",
            models=[worker.ModelSettings(model="/test.gguf", model_alias="test-model")],
        ).model_dump_json()
    )
    return path


@pytest.fixture
def starting_status() -> WorkerStatus:
    return WorkerStatus(
        purpose="generation", model="test-model", pid=os.getpid(), phase="starting"
    )


@pytest.fixture
def prepared_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings) -> Mock:
    settings.VAULT_DIR = tmp_path
    monkeypatch.setenv("SECOND_BRAIN_WORKER_PARENT_PID", str(os.getppid()))
    files = Mock()
    files.ensure.side_effect = lambda model, progress: tmp_path / model.filename
    monkeypatch.setattr(model_files, "ModelFiles", lambda: files)
    return files


@pytest.mark.parametrize(
    "purpose,model", [("generation", "gemma4:12b"), ("embedding", "nomic-embed-text")]
)
@pytest.mark.parametrize("exit_code", [0, 2, -signal.SIGABRT])
def test_server_exit_is_propagated_and_configuration_is_removed(
    prepared_models: Mock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
    purpose: ModelPurpose,
    model: str,
) -> None:
    process = Mock(pid=os.getpid())
    process.poll.return_value = exit_code
    process.wait.return_value = exit_code
    configurations = []

    def start(command: list[str], *, pass_fds: tuple[int, ...]) -> Mock:
        assert command[:3] == [sys.executable, "-m", "second_brain.apps.inference.server"]
        assert pass_fds == (int(command[4]),)
        config_path = Path(command[3])
        configurations.append(config_path)
        config = json.loads(config_path.read_text())
        assert config["host"] == "127.0.0.1"
        assert config["interrupt_requests"] is False
        assert [entry["model_alias"] for entry in config["models"]] == [model]
        settings = config["models"][0]
        assert Path(settings["model"]).parent == tmp_path
        assert settings["embedding"] == (purpose == "embedding")
        assert settings["cache"] is (purpose == "generation")
        if purpose == "embedding":
            assert settings["n_batch"] == settings["n_ctx"]
        return process

    monkeypatch.setattr(worker.subprocess, "Popen", start)
    previous_handler = signal.getsignal(signal.SIGTERM)
    if exit_code:
        with pytest.raises(SystemExit) as error:
            worker.main(purpose)
        assert error.value.code == (exit_code if exit_code > 0 else 1)
    else:
        worker.main(purpose)

    assert signal.getsignal(signal.SIGTERM) == previous_handler
    assert configurations and not configurations[0].exists()
    process.terminate.assert_not_called()
    process.kill.assert_not_called()
    status = read_worker_status(tmp_path / ".backend", purpose)
    if exit_code:
        assert status.phase == "error"
        assert "stopped unexpectedly" in status.error
    else:
        assert status is None


def test_shutdown_during_preparation_does_not_start_server(
    prepared_models: Mock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted_download(*args: object) -> None:
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

    prepared_models.ensure.side_effect = interrupted_download
    start = Mock()
    monkeypatch.setattr(worker.subprocess, "Popen", start)
    previous_handlers = {
        signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)
    }

    with pytest.raises(SystemExit) as error:
        worker.main("generation")
    assert error.value.code == 0
    start.assert_not_called()
    assert not (tmp_path / ".backend" / "inference-generation.json").exists()
    assert not list((tmp_path / ".backend").glob("inference-generation-*"))
    for signum, handler in previous_handlers.items():
        assert signal.getsignal(signum) == handler


def test_shutdown_during_process_creation_still_reaps_server(
    prepared_models: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = Mock(pid=os.getpid())
    process.poll.return_value = None
    process.wait.return_value = -signal.SIGTERM

    def start(command: list[str], *, pass_fds: tuple[int, ...]) -> Mock:
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        return process

    monkeypatch.setattr(worker.subprocess, "Popen", start)

    worker.main("generation")

    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=worker.SHUTDOWN_TIMEOUT_SECONDS)
    process.kill.assert_not_called()


def test_server_that_ignores_termination_is_killed_and_reaped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_config_path: Path,
    starting_status: WorkerStatus,
) -> None:
    process = Mock(pid=os.getpid())
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired("server", 3), -signal.SIGKILL]
    monkeypatch.setattr(worker.subprocess, "Popen", Mock(return_value=process))
    stop = threading.Event()
    stop.set()

    assert (
        worker._run_server(server_config_path, stop, tmp_path, starting_status) == -signal.SIGKILL
    )

    assert process.method_calls[-4:] == [
        call.terminate(),
        call.wait(timeout=worker.SHUTDOWN_TIMEOUT_SECONDS),
        call.kill(),
        call.wait(),
    ]


def test_real_child_is_stopped_before_run_server_returns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_config_path: Path,
    starting_status: WorkerStatus,
) -> None:
    popen = subprocess.Popen
    children = []

    def start(command: list[str], *, pass_fds: tuple[int, ...]) -> subprocess.Popen[bytes]:
        child = popen([sys.executable, "-c", "import time; time.sleep(60)"], pass_fds=pass_fds)
        children.append(child)
        return child

    monkeypatch.setattr(worker.subprocess, "Popen", start)
    stop = threading.Event()
    stop.set()
    try:
        assert (
            worker._run_server(server_config_path, stop, tmp_path, starting_status)
            == -signal.SIGTERM
        )
        assert children[0].returncode == -signal.SIGTERM
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)


def test_parent_exit_requests_shutdown_through_signal_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker.os, "getppid", lambda: 1)
    send_signal = Mock()
    monkeypatch.setattr(worker.os, "kill", send_signal)
    stop = Mock()
    stop.wait.return_value = False

    worker._watch_parent(12345, stop)

    send_signal.assert_called_once_with(os.getpid(), signal.SIGTERM)


def test_stopped_parent_watcher_does_not_signal_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    send_signal = Mock()
    monkeypatch.setattr(worker.os, "kill", send_signal)
    stop = threading.Event()
    stop.set()

    worker._watch_parent(12345, stop)

    send_signal.assert_not_called()


def test_port_is_reserved_until_child_inherits_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_config_path: Path,
    starting_status: WorkerStatus,
) -> None:
    process = Mock(pid=os.getpid())
    process.poll.return_value = 0
    process.wait.return_value = 0
    ports = []

    def start(command: list[str], *, pass_fds: tuple[int, ...]) -> Mock:
        assert pass_fds == (int(command[4]),)
        with socket.socket(fileno=os.dup(pass_fds[0])) as inherited:
            address = inherited.getsockname()
            assert address[0] == "127.0.0.1"
            assert address[1] > 0
            ports.append(address[1])
            with socket.socket() as competitor:
                with pytest.raises(OSError):
                    competitor.bind(address)
        return process

    monkeypatch.setattr(worker.subprocess, "Popen", start)
    worker._run_server(server_config_path, threading.Event(), tmp_path, starting_status)

    assert read_worker_status(tmp_path, "generation").port == ports[0]
    with socket.socket() as released:
        released.bind(("127.0.0.1", ports[0]))


def test_model_preparation_progress_is_published(
    tmp_path: Path, starting_status: WorkerStatus
) -> None:
    worker._publish_progress(tmp_path, starting_status, "downloading", 25, 100)

    status = read_worker_status(tmp_path, "generation")
    assert status.phase == "downloading"
    assert status.model == "test-model"
    assert (status.completed, status.total) == (25, 100)


@pytest.mark.parametrize(
    "failure,reason",
    [
        (InferenceUnavailableError("Model download failed."), "Model download failed."),
        (
            RuntimeError("Private runtime details."),
            "Local inference failed to start. Check backend logs.",
        ),
    ],
)
def test_preparation_failure_remains_available(
    prepared_models: Mock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    reason: str,
) -> None:
    prepared_models.ensure.side_effect = failure
    start = Mock()
    monkeypatch.setattr(worker.subprocess, "Popen", start)
    previous_handlers = {
        signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)
    }

    with pytest.raises(type(failure)) as error:
        worker.main("generation")

    assert error.value is failure
    status = read_worker_status(tmp_path / ".backend", "generation")
    assert status.phase == "error"
    assert status.error == reason
    start.assert_not_called()
    assert not list((tmp_path / ".backend").glob("inference-generation-*"))
    for signum, handler in previous_handlers.items():
        assert signal.getsignal(signum) == handler


def test_readiness_requires_authenticated_model_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_config_path: Path,
    starting_status: WorkerStatus,
) -> None:
    process = Mock(pid=os.getpid())
    process.poll.side_effect = [None, None, 0, 0]
    process.wait.return_value = 0
    monkeypatch.setattr(worker.subprocess, "Popen", Mock(return_value=process))
    client = Mock()
    client.get.side_effect = [
        httpx.ConnectError("Loading"),
        httpx.Response(
            200,
            json={"data": [{"id": "test-model"}]},
            request=httpx.Request("GET", "http://test/v1/models"),
        ),
    ]
    client_context = Mock()
    client_context.__enter__ = Mock(return_value=client)
    client_context.__exit__ = Mock(return_value=False)
    client_factory = Mock(return_value=client_context)
    monkeypatch.setattr(worker.httpx, "Client", client_factory)
    stop = Mock()
    stop.wait.return_value = False

    assert worker._run_server(server_config_path, stop, tmp_path, starting_status) == 0

    status = read_worker_status(tmp_path, "generation")
    assert status.phase == "ready"
    assert status.port > 0
    assert client_factory.call_args.kwargs["base_url"] == f"http://127.0.0.1:{status.port}"
    assert client_factory.call_args.kwargs["headers"] == {
        "Authorization": "Bearer test-inference-key"
    }
    assert client_factory.call_args.kwargs["trust_env"] is False


@pytest.mark.parametrize("failure", ["timeout", "authentication", "models"])
def test_failed_readiness_stops_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_config_path: Path,
    starting_status: WorkerStatus,
    failure: str,
) -> None:
    process = Mock(pid=os.getpid())
    process.poll.return_value = None
    process.wait.return_value = -signal.SIGTERM
    monkeypatch.setattr(worker.subprocess, "Popen", Mock(return_value=process))
    client = Mock()
    if failure == "timeout":
        client.get.side_effect = httpx.ConnectError("Loading")
        monkeypatch.setattr(worker, "STARTUP_TIMEOUT_SECONDS", 0)
    else:
        client.get.return_value = httpx.Response(
            401 if failure == "authentication" else 200,
            json={"data": [{"id": "unexpected-model"}]},
            request=httpx.Request("GET", "http://test/v1/models"),
        )
    client_context = Mock()
    client_context.__enter__ = Mock(return_value=client)
    client_context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(worker.httpx, "Client", Mock(return_value=client_context))
    stop = Mock()
    stop.wait.return_value = False

    with pytest.raises(InferenceUnavailableError):
        worker._run_server(server_config_path, stop, tmp_path, starting_status)

    assert read_worker_status(tmp_path, "generation").phase != "ready"
    process.terminate.assert_called_once()
    process.wait.assert_called_once()
