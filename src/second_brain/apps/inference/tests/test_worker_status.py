import json
import os
from pathlib import Path

import pytest

from second_brain.apps.inference.services import worker_status
from second_brain.apps.inference.services.worker_status import (
    WorkerStatus,
    read_worker_status,
    write_worker_status,
)


def test_status_round_trip_is_private_and_contains_no_token(tmp_path: Path) -> None:
    status = WorkerStatus(pid=os.getpid(), server_pid=os.getpid(), port=8001, phase="ready")
    write_worker_status(tmp_path, status)

    assert read_worker_status(tmp_path) == status
    path = tmp_path / "inference.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert "token" not in path.read_text()


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        json.dumps({"pid": -1, "phase": "starting"}),
        json.dumps({"pid": 123, "phase": "ready"}),
        json.dumps({"pid": 123, "phase": "loading", "port": 99999}),
        json.dumps({"pid": 123, "socket_path": "/old.sock"}),
    ],
)
def test_invalid_or_obsolete_status_is_unavailable(tmp_path: Path, body: str) -> None:
    (tmp_path / "inference.json").write_text(body)
    assert read_worker_status(tmp_path) is None


@pytest.mark.parametrize("dead_pid", [123, 456])
def test_abandoned_status_is_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dead_pid: int
) -> None:
    write_worker_status(tmp_path, WorkerStatus(pid=123, server_pid=456, port=8001, phase="ready"))

    def check_process(pid: int, signum: int) -> None:
        if pid == dead_pid:
            raise ProcessLookupError

    monkeypatch.setattr(worker_status.os, "kill", check_process)
    assert read_worker_status(tmp_path) is None


def test_failure_remains_visible_after_worker_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status = WorkerStatus(pid=123, phase="error", error="Model download failed.")
    write_worker_status(tmp_path, status)

    def no_live_process(pid: int, signum: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(worker_status.os, "kill", no_live_process)
    assert read_worker_status(tmp_path) == status


def test_new_start_replaces_previous_error(tmp_path: Path) -> None:
    write_worker_status(tmp_path, WorkerStatus(pid=123, phase="error", error="Old failure."))
    write_worker_status(tmp_path, WorkerStatus(pid=os.getpid(), phase="starting"))

    status = read_worker_status(tmp_path)
    assert status.phase == "starting"
    assert status.error is None


def test_missing_status_is_unavailable(tmp_path: Path) -> None:
    assert read_worker_status(tmp_path) is None
