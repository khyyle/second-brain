"""Request-time prerequisites versus failures after an asynchronous build is accepted."""

import pytest

from second_brain.apps.jobs.models import Job
from second_brain.apps.jobs.services.job_queue import claim_next_job
from second_brain.apps.jobs.worker import run_attempt
from second_brain.apps.preferences.services import provider_credentials
from second_brain.django_config.errors import DependencyError

MISSING_KEY = (
    "No anthropic credential is available in Keychain. Add it in Settings, then build again."
)


@pytest.fixture
def staged_document(config):
    path = config.raw_dir / "documents" / "notes.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Notes\n\nAn eligible source.")
    return path


def test_missing_credential_rejects_request_without_a_job_or_copies(
    api_client,
    config,
    staged_document,
    monkeypatch,
):
    monkeypatch.setattr(provider_credentials, "read_api_key", lambda _: None)
    response = api_client.post("/v1/builds", data={}, content_type="application/json")
    assert response.status_code == 409
    assert response.json() == {"detail": MISSING_KEY}
    assert not Job.objects.exists()
    assert not list((config.data_dir / ".backend" / "jobs").iterdir())
    assert staged_document.exists()


def test_inaccessible_keychain_is_a_dependency_error_before_acceptance(
    api_client,
    staged_document,
    monkeypatch,
):
    def denied(_):
        raise DependencyError("Keychain denied access to the provider credential.")

    monkeypatch.setattr(provider_credentials, "read_api_key", denied)
    response = api_client.post("/v1/builds", data={}, content_type="application/json")
    assert response.status_code == 503
    assert response.json() == {"detail": "Keychain denied access to the provider credential."}
    assert not Job.objects.exists()


@pytest.mark.parametrize(
    "route,body",
    [
        ("/builds", {"dry_run": True}),
        ("/grouping-plan/preview", {}),
    ],
)
def test_local_planning_needs_no_paid_credential(
    api_client, staged_document, monkeypatch, route, body
):
    def forbidden(_):
        pytest.fail("Planning must not access provider credentials")

    monkeypatch.setattr(provider_credentials, "read_api_key", forbidden)
    assert (
        api_client.post(f"/v1{route}", data=body, content_type="application/json").status_code
        == 202
    )


def test_no_sources_does_not_require_a_credential(api_client, monkeypatch):
    monkeypatch.setattr(provider_credentials, "read_api_key", lambda _: None)
    assert (
        api_client.post("/v1/builds", data={}, content_type="application/json").status_code == 202
    )


@pytest.mark.parametrize(
    "keychain_error,reason",
    [
        (None, MISSING_KEY),
        (
            DependencyError("Keychain denied access to the provider credential."),
            "Keychain denied access to the provider credential.",
        ),
    ],
)
def test_accepted_job_can_fail_later_and_reports_the_same_reason_in_logs_and_api(
    api_client,
    staged_document,
    monkeypatch,
    caplog,
    keychain_error,
    reason,
):
    # Presence of a fake key allows acceptance without contacting the provider.
    # Removing the key or denying access later must still fail the accepted job.
    response = api_client.post("/v1/builds", data={}, content_type="application/json")
    assert response.status_code == 202
    job_id = response.json()["id"]

    def unavailable(_):
        if keychain_error is not None:
            raise keychain_error
        return None

    monkeypatch.setattr(provider_credentials, "read_api_key", unavailable)
    run_attempt(claim_next_job("build"))
    detail = api_client.get(f"/v1/jobs/{job_id}")
    assert detail.status_code == 200  # Successfully reading a failed job is a successful request.
    assert detail.json()["job"]["status"] == "failed"
    assert detail.json()["job"]["error"] == reason
    assert f"Job {job_id} (build) failed: {reason}" in caplog.text
    snapshot = api_client.get("/v1/desktop-state").json()
    assert snapshot["jobs"][0]["job"]["error"] == reason
