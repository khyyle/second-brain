"""Worker outcomes preserve expected explanations and hide unexpected exception details."""

import pytest

from second_brain.apps.jobs.worker import run_attempt
from second_brain.errors import SecondBrainError


@pytest.mark.parametrize(
    "failure,reason",
    [
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
