"""Worker outcomes preserve expected explanations and hide unexpected exception details."""

import threading

import pytest

from second_brain.apps.inference.errors import InferenceUnavailableError
from second_brain.apps.jobs.services import job_progress
from second_brain.apps.jobs.worker import run_attempt
from second_brain.errors import SecondBrainError


@pytest.mark.parametrize(
    "failure,reason",
    [
        (
            InferenceUnavailableError("Local inference is still loading."),
            "Local inference is still loading.",
        ),
        (
            SecondBrainError("The model provider is unavailable. Try again later."),
            "The model provider is unavailable. Try again later.",
        ),
        (
            RuntimeError("sensitive internal implementation details"),
            "The operation failed unexpectedly. Review backend logs before retrying.",
        ),
    ],
)
def test_failure_is_persisted_and_logged(active_job, monkeypatch, caplog, failure, reason):
    def fail(_):
        raise failure

    monkeypatch.setattr("second_brain.apps.jobs.services.job_execution.execute_job", fail)
    run_attempt(active_job)
    active_job.refresh_from_db()
    assert active_job.status == "failed"
    assert active_job.error == reason
    assert active_job.finished_at is not None
    assert f"Job {active_job.pk} (build) failed: {reason}" in caplog.text
    assert any(record.exc_info for record in caplog.records) == (
        not isinstance(failure, SecondBrainError)
    )


def test_worker_heartbeat_continues_during_blocking_execution(active_job, monkeypatch):
    refreshed = threading.Event()
    touch_heartbeat = job_progress.touch_heartbeat

    def record_refresh():
        touch_heartbeat()
        refreshed.set()

    def execute(_):
        job_progress.update_progress(
            phase="triage",
            current=0,
            total=1,
            started_at=job_progress.now_iso(),
            current_sources=("chatgpt/slow.md",),
        )
        active_job.refresh_from_db()
        before = active_job.heartbeat_at
        progress = active_job.progress.copy()

        assert refreshed.wait(timeout=8)
        active_job.refresh_from_db()
        assert active_job.heartbeat_at > before
        assert active_job.progress == progress
        return {"completed": 1}

    monkeypatch.setattr(job_progress, "touch_heartbeat", record_refresh)
    monkeypatch.setattr("second_brain.apps.jobs.services.job_execution.execute_job", execute)
    run_attempt(active_job)

    active_job.refresh_from_db()
    assert active_job.status == "succeeded", active_job.error
