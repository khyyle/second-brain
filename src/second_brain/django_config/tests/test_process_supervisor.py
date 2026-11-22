"""Backend-owned child process startup, recovery, and shutdown."""

import os
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from second_brain.django_config import process_supervisor


@pytest.fixture
def supervisor(monkeypatch):
    """Run monitor passes synchronously with fake child processes and no clock waits."""
    passes = []
    children = []

    class StopEvent:
        stopped = False

        def wait(self, timeout):
            if self.stopped or not passes:
                return True
            passes.pop(0)()
            return False

        def set(self):
            self.stopped = True

    class MonitorThread:
        ident = None

        def __init__(self, *, target, daemon):
            self.target = target

        def start(self):
            self.ident = 1
            self.target()

        def join(self, timeout):
            pass

    def spawn(*args, **kwargs):
        child = Mock()
        child.poll.return_value = None
        children.append(child)
        return child

    popen = Mock(side_effect=spawn)
    monkeypatch.setattr(process_supervisor.subprocess, "Popen", popen)
    monkeypatch.setattr(
        process_supervisor, "threading", SimpleNamespace(Event=StopEvent, Thread=MonitorThread)
    )
    return SimpleNamespace(passes=passes, children=children, popen=popen, spawn=spawn)


def test_partial_startup_stops_already_started_worker(supervisor):
    child = supervisor.spawn()
    supervisor.popen.side_effect = [child, OSError("cannot spawn build worker")]

    with pytest.raises(OSError, match="cannot spawn build worker"):
        with process_supervisor.supervised_processes():
            pytest.fail("Startup must fail before serving requests")

    child.terminate.assert_called_once()
    child.wait.assert_called_once()


def test_failed_restart_is_retried_and_other_lane_survives(supervisor, caplog):
    crashed, healthy, replacement = [supervisor.spawn() for _ in range(3)]
    crashed.poll.return_value = 1
    crashed.returncode = 1
    supervisor.popen.side_effect = [
        crashed,
        healthy,
        supervisor.spawn(),
        supervisor.spawn(),
        OSError("spawn failed"),
        replacement,
    ]
    supervisor.passes.extend([lambda: None, lambda: None])

    with process_supervisor.supervised_processes():
        assert supervisor.popen.call_count == 6
        healthy.terminate.assert_not_called()
        assert "Unable to restart ingest worker" in caplog.text

    healthy.terminate.assert_called_once()
    replacement.terminate.assert_called_once()


def test_shutdown_kills_worker_that_does_not_stop(supervisor):
    with process_supervisor.supervised_processes():
        child = supervisor.children[0]
        child.wait.side_effect = [subprocess.TimeoutExpired("worker", 10), 0]

    child.terminate.assert_called_once()
    child.kill.assert_called_once()
    assert child.wait.call_count == 2


def test_supervisor_owns_both_model_workers_and_job_lanes(supervisor):
    with process_supervisor.supervised_processes():
        launches = supervisor.popen.call_args_list
        assert [launch.args[0][-2:] for launch in launches] == [
            ["second_brain.apps.jobs.worker", "ingest"],
            ["second_brain.apps.jobs.worker", "build"],
            ["second_brain.apps.inference.worker", "generation"],
            ["second_brain.apps.inference.worker", "embedding"],
        ]
        assert all(
            launch.kwargs["env"]["SECOND_BRAIN_WORKER_PARENT_PID"] == str(os.getpid())
            for launch in launches
        )
    for child in supervisor.children:
        child.terminate.assert_called_once()
        child.wait.assert_called_once()


def test_failed_model_restart_waits_and_leaves_other_model_running(supervisor, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(process_supervisor.time, "monotonic", lambda: clock[0])
    ingest, build, generation, embedding, replacement = [supervisor.spawn() for _ in range(5)]
    generation.poll.return_value = 1
    generation.returncode = 1
    supervisor.popen.side_effect = [
        ingest,
        build,
        generation,
        embedding,
        OSError("spawn failed"),
        replacement,
    ]

    def advance(seconds, expected_launches):
        assert supervisor.popen.call_count == expected_launches
        embedding.terminate.assert_not_called()
        clock[0] = seconds

    supervisor.passes.extend(
        [
            lambda: advance(1, 4),
            lambda: advance(30, 4),
            lambda: advance(31, 5),
            lambda: advance(60, 5),
        ]
    )
    with process_supervisor.supervised_processes():
        assert supervisor.popen.call_count == 6
        assert supervisor.popen.call_args.args[0][-1] == "generation"
        embedding.terminate.assert_not_called()
    embedding.terminate.assert_called_once()
    replacement.terminate.assert_called_once()
