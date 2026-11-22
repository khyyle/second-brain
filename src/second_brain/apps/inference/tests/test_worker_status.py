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
    status = WorkerStatus(
        purpose="generation",
        model="gemma4:12b",
        pid=os.getpid(),
        server_pid=os.getpid(),
        port=8001,
        phase="ready",
    )
    write_worker_status(tmp_path, status)

    assert read_worker_status(tmp_path, "generation") == status
    path = tmp_path / "inference-generation.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert "token" not in path.read_text()


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        json.dumps(
            {"purpose": "generation", "model": "gemma4:12b", "pid": -1, "phase": "starting"}
        ),
        json.dumps({"purpose": "generation", "model": "gemma4:12b", "pid": 123, "phase": "ready"}),
        json.dumps(
            {
                "purpose": "generation",
                "model": "gemma4:12b",
                "pid": 123,
                "phase": "loading",
                "port": 99999,
            }
        ),
        json.dumps(
            {
                "purpose": "generation",
                "model": "gemma4:12b",
                "pid": 123,
                "socket_path": "/old.sock",
            }
        ),
    ],
)
def test_invalid_or_obsolete_status_is_unavailable(tmp_path: Path, body: str) -> None:
    (tmp_path / "inference-generation.json").write_text(body)
    assert read_worker_status(tmp_path, "generation") is None


@pytest.mark.parametrize("dead_pid", [123, 456])
def test_abandoned_status_is_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dead_pid: int
) -> None:
    write_worker_status(
        tmp_path,
        WorkerStatus(
            purpose="generation",
            model="gemma4:12b",
            pid=123,
            server_pid=456,
            port=8001,
            phase="ready",
        ),
    )

    def check_process(pid: int, signum: int) -> None:
        if pid == dead_pid:
            raise ProcessLookupError

    monkeypatch.setattr(worker_status.os, "kill", check_process)
    assert read_worker_status(tmp_path, "generation") is None


def test_failure_remains_visible_after_worker_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status = WorkerStatus(
        purpose="generation",
        model="gemma4:12b",
        pid=123,
        phase="error",
        error="Model download failed.",
    )
    write_worker_status(tmp_path, status)

    def no_live_process(pid: int, signum: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(worker_status.os, "kill", no_live_process)
    assert read_worker_status(tmp_path, "generation") == status


def test_new_start_replaces_previous_error(tmp_path: Path) -> None:
    write_worker_status(
        tmp_path,
        WorkerStatus(
            purpose="generation", model="gemma4:12b", pid=123, phase="error", error="Old failure."
        ),
    )
    write_worker_status(
        tmp_path,
        WorkerStatus(purpose="generation", model="gemma4:12b", pid=os.getpid(), phase="starting"),
    )

    status = read_worker_status(tmp_path, "generation")
    assert status.phase == "starting"
    assert status.error is None


def test_missing_status_is_unavailable(tmp_path: Path) -> None:
    assert read_worker_status(tmp_path, "generation") is None


def test_model_statuses_do_not_overwrite_each_other(tmp_path: Path) -> None:
    generation = WorkerStatus(
        purpose="generation", model="gemma4:12b", pid=os.getpid(), phase="downloading"
    )
    embedding = WorkerStatus(
        purpose="embedding",
        model="nomic-embed-text",
        pid=os.getpid(),
        server_pid=os.getpid(),
        port=51234,
        phase="ready",
    )
    write_worker_status(tmp_path, generation)
    write_worker_status(tmp_path, embedding)
    generation = generation.model_copy(update={"phase": "error", "error": "Download failed."})
    write_worker_status(tmp_path, generation)

    assert read_worker_status(tmp_path, "generation") == generation
    assert read_worker_status(tmp_path, "embedding") == embedding


def test_status_for_wrong_purpose_is_not_used(tmp_path: Path) -> None:
    status = WorkerStatus(
        purpose="embedding", model="nomic-embed-text", pid=os.getpid(), phase="starting"
    )
    (tmp_path / "inference-generation.json").write_text(status.model_dump_json())
    assert read_worker_status(tmp_path, "generation") is None
