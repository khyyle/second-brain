"""Opt-in macOS integration: real startup, workers, Swift HTTP/SSE, and shutdown."""

import fcntl
import os
import socket
import subprocess
import sys
import time
from http.client import HTTPConnection
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def live_gui_backend(pytestconfig, backend_env, tmp_path):
    """Own a disposable backend for this test; never attach to the user's server."""
    if not pytestconfig.getoption("--run-gui-http"):
        pytest.skip("Run make gui-smoke (or pytest --run-gui-http) on macOS.")
    if sys.platform != "darwin":
        pytest.skip("The native GUI requires macOS and Xcode.")
    developer = os.environ.get("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    if not Path(developer).is_dir():
        pytest.skip("Install Xcode or set DEVELOPER_DIR to its developer directory.")
    vault = Path(backend_env["SECOND_BRAIN_VAULT"])
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {
        **backend_env,
        "SECOND_BRAIN_PORT": str(port),
        "SECOND_BRAIN_DEBUG": "false",
        "SECOND_BRAIN_LIVE_TEST_VAULT": str(vault),
        "SECOND_BRAIN_LIVE_TEST_PORT": str(port),
        "DEVELOPER_DIR": developer,
    }
    log_path = tmp_path / "backend.log"
    with log_path.open("w") as log:
        backend = subprocess.Popen(
            [sys.executable, str(ROOT / "start.py"), "--no-reload"],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 30
            while True:
                client = HTTPConnection("127.0.0.1", port, timeout=1)
                try:
                    token = (vault / ".backend" / "api-token").read_text().strip()
                    client.request(
                        "GET", "/v1/jobs?limit=1", headers={"Authorization": f"Bearer {token}"}
                    )
                    response = client.getresponse()
                    response.read()
                    if response.status == 200:
                        break
                except OSError:
                    pass
                finally:
                    client.close()
                assert backend.poll() is None, "Backend exited before becoming ready"
                assert time.monotonic() < deadline, "Backend did not become ready"
                time.sleep(0.2)
            yield env
        finally:
            if backend.poll() is None:
                backend.terminate()
                try:
                    backend.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    backend.kill()
                    backend.wait()
            # Captured by pytest and printed on failure, alongside Swift's output.
            print(log_path.read_text())
            for lane in ("ingest", "build"):
                lock = vault / ".backend" / "locks" / f"worker-{lane}.lock"
                if lock.exists():
                    with lock.open("r") as handle:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(handle, fcntl.LOCK_UN)


def test_swift_client_http_and_server_events(live_gui_backend):
    result = subprocess.run(
        [
            "swift",
            "test",
            "--package-path",
            str(ROOT / "gui/SecondBrainBar"),
            "--filter",
            "LiveBackendTests",
        ],
        cwd=ROOT,
        env=live_gui_backend,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
