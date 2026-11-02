"""Run the model worker while its backend launcher is alive."""

import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from pathlib import Path
from types import FrameType
from typing import Literal

import django
import httpx
from llama_cpp.server.settings import ConfigFileSettings, ModelSettings

from second_brain.apps.inference.catalog import get_model
from second_brain.apps.inference.errors import InferenceUnavailableError
from second_brain.apps.inference.services.worker_status import WorkerStatus, write_worker_status
from second_brain.django_config.identity import private_token
from second_brain.django_config.paths import get_vault_paths
from second_brain.django_config.vault_storage import vault_lock

PARENT_CHECK_SECONDS = 1
SHUTDOWN_TIMEOUT_SECONDS = 3
STARTUP_TIMEOUT_SECONDS = 120


def main() -> None:
    """Prepare model files and serve local inference until shutdown or parent exit.

    Reads the vault from Django settings and the launcher's process ID from
    SECOND_BRAIN_WORKER_PARENT_PID. Holds the vault's inference lock until the
    server exits, then removes its temporary configuration.

    Returns
    -------
    None

    Raises
    ------
    SystemExit
        Shutdown was requested, or the server exited unsuccessfully.
    """
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "second_brain.django_config.settings.runtime")
    django.setup()

    # Hugging Face reads cache settings at import time, after Django loads .env.
    from second_brain.apps.inference.services.model_files import ModelFiles

    files = ModelFiles()
    parent_pid = int(os.environ.get("SECOND_BRAIN_WORKER_PARENT_PID", os.getppid()))
    stop = threading.Event()
    preparing = True

    def request_shutdown(signum: int, frame: FrameType | None) -> None:
        stop.set()
        if preparing:
            # Interrupt downloads; no child process needs to be reaped yet.
            raise SystemExit(0)

    previous_handlers = {
        signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)
    }
    watcher = threading.Thread(target=_watch_parent, args=(parent_pid, stop), daemon=True)
    try:
        for signum in previous_handlers:
            signal.signal(signum, request_shutdown)
        watcher.start()
        directory = get_vault_paths().backend_dir
        with vault_lock("inference", blocking=False):
            failed = False
            try:
                write_worker_status(directory, WorkerStatus(pid=os.getpid(), phase="starting"))
                with tempfile.TemporaryDirectory(prefix="inference-", dir=directory) as temporary:
                    triage_model = get_model("gemma4:12b")
                    triage_model_path = files.ensure(
                        triage_model, partial(_publish_progress, directory, triage_model.name)
                    )
                    embedding_model = get_model("nomic-embed-text")
                    embedding_model_path = files.ensure(
                        embedding_model,
                        partial(_publish_progress, directory, embedding_model.name),
                    )

                    triage_settings = ModelSettings(
                        model=str(triage_model_path),
                        model_alias=triage_model.name,
                        n_ctx=triage_model.context_tokens,
                        n_gpu_layers=-1,
                        cache=True,
                    )
                    embedding_settings = ModelSettings(
                        model=str(embedding_model_path),
                        model_alias=embedding_model.name,
                        n_ctx=embedding_model.context_tokens,
                        n_batch=embedding_model.context_tokens,
                        n_gpu_layers=-1,
                        embedding=True,
                    )
                    server_config = ConfigFileSettings(
                        host="127.0.0.1",
                        port=0,
                        api_key=private_token(get_vault_paths().data_dir, "inference-token"),
                        interrupt_requests=False,
                        models=[triage_settings, embedding_settings],
                    )
                    config_path = Path(temporary) / "server_config.json"
                    config_path.write_text(server_config.model_dump_json(indent=2))

                    # Defer signals during process creation until its handle can be cleaned up.
                    preparing = False
                    if stop.is_set():
                        return
                    return_code = _run_server(config_path, stop, directory)
                    if return_code and not stop.is_set():
                        failed = True
                        write_worker_status(
                            directory,
                            WorkerStatus(
                                pid=os.getpid(),
                                phase="error",
                                error="Local inference stopped unexpectedly. Check backend logs.",
                            ),
                        )
                        raise SystemExit(return_code if return_code > 0 else 1)
            except Exception as exc:
                failed = True
                reason = (
                    exc.reason
                    if isinstance(exc, InferenceUnavailableError)
                    else "Local inference failed to start. Check backend logs."
                )
                write_worker_status(
                    directory, WorkerStatus(pid=os.getpid(), phase="error", error=reason)
                )
                raise
            finally:
                if not failed:
                    # Keep failures readable after exit, until the next launch replaces them.
                    (directory / "inference.json").unlink(missing_ok=True)
    finally:
        stop.set()
        if watcher.ident is not None:
            watcher.join()
        # Signal handlers belong to the process, so restore them when this runner returns.
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def _watch_parent(parent_pid: int, stop: threading.Event) -> None:
    """Request normal shutdown if the original parent disappears."""
    while not stop.wait(PARENT_CHECK_SECONDS):
        if os.getppid() != parent_pid:
            os.kill(os.getpid(), signal.SIGTERM)
            return


def _publish_progress(
    directory: Path,
    model: str,
    phase: Literal["downloading", "verifying"],
    completed: int,
    total: int,
) -> None:
    """Publish byte counts for the model currently being prepared."""
    write_worker_status(
        directory,
        WorkerStatus(pid=os.getpid(), phase=phase, model=model, completed=completed, total=total),
    )


def _run_server(config_path: Path, stop: threading.Event, directory: Path) -> int:
    """Publish HTTP readiness and stop the server before returning.

    Parameters
    ----------
    config_path: Path
        JSON configuration containing loopback server settings, token, and model paths.
    stop: threading.Event
        Requests shutdown when set.
    directory: Path
        Vault backend directory where startup status is published.

    Returns
    -------
    int
        Server exit code, including a negative signal number on forced termination.

    Raises
    ------
    InferenceUnavailableError
        The server did not become ready or returned an unexpected model list.
    """
    config = ConfigFileSettings.model_validate_json(config_path.read_text())
    if config.host != "127.0.0.1" or not config.api_key:
        raise ValueError("Inference requires a loopback address and authentication token.")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        # Pass the open socket so another process cannot claim the port before startup.
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "second_brain.apps.inference.server",
                str(config_path),
                str(listener.fileno()),
            ],
            pass_fds=(listener.fileno(),),
        )
    try:
        status = WorkerStatus(pid=os.getpid(), server_pid=process.pid, port=port, phase="loading")
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        write_worker_status(directory, status)
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=1,
            trust_env=False,
        ) as client:
            while process.poll() is None and not stop.wait(PARENT_CHECK_SECONDS):
                if status.phase == "ready":
                    continue
                try:
                    response = client.get("/v1/models")
                except httpx.RequestError:
                    pass
                else:
                    try:
                        response.raise_for_status()
                        available = {model["id"] for model in response.json()["data"]}
                        expected = {model.model_alias or model.model for model in config.models}
                        if available != expected:
                            raise ValueError("Unexpected model aliases")
                    except (httpx.HTTPStatusError, ValueError, KeyError, TypeError) as exc:
                        raise InferenceUnavailableError(
                            "Local inference did not confirm its configured models. "
                            "Check backend logs."
                        ) from exc
                    status = status.model_copy(update={"phase": "ready"})
                    write_worker_status(directory, status)
                if status.phase != "ready" and time.monotonic() >= deadline:
                    raise InferenceUnavailableError(
                        "Local inference took too long to start. Check backend logs and retry."
                    )
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            return_code = process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            return_code = process.wait()
    return return_code


if __name__ == "__main__":
    main()
