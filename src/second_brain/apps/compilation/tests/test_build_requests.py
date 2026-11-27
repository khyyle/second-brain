"""Request-time prerequisites versus failures after an asynchronous build is accepted."""

import pytest

from second_brain.apps.jobs.models import Job
from second_brain.apps.jobs.services.job_queue import claim_next_job
from second_brain.apps.jobs.worker import run_attempt
from second_brain.apps.preferences.services.user_settings import update_preferences
from second_brain.cloud_llm.pi import pi_helper

CONNECT = "Connect a provider in Settings."
CHOOSE = "Choose a model on the Build tab."
HELPER_FAILED = "The provider helper could not complete the operation."


@pytest.fixture
def staged_document(config):
    path = config.raw_dir / "documents" / "notes.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Notes\n\nAn eligible source.")
    return path


@pytest.fixture
def login(monkeypatch, config):
    """What the provider helper finds in Keychain. Tests change it to move the login."""
    update_preferences({"compilation": {"provider": "anthropic", "model": "claude-sonnet-4-6"}})
    state = {"connected": True, "models": ["claude-sonnet-4-6"]}

    async def helper(command: str, provider: str) -> object:
        if "error" in state:
            raise state["error"]
        if command == "connection-status":
            return {"connected": state["connected"]}
        assert command == "list-models"
        models = state["models"] if state["connected"] else []
        facts = {"contextWindow": 200_000, "price": None}
        return [{"id": model, "name": model, **facts} for model in models]

    monkeypatch.setattr(pi_helper, "run_json", helper)
    return state


def build(api_client, body=None):
    return api_client.post("/v1/builds", data=body or {}, content_type="application/json")


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"connected": False}, CONNECT),
        ({"models": ["another-model"]}, CHOOSE),
    ],
)
def test_a_missing_login_or_model_rejects_the_request_without_a_job_or_copies(
    api_client, config, staged_document, login, change, reason
):
    login.update(change)
    response = build(api_client)
    assert response.status_code == 409
    assert response.json() == {"detail": reason}
    assert not Job.objects.exists()
    assert not list((config.data_dir / ".backend" / "jobs").iterdir())
    assert staged_document.exists()


def test_a_build_needs_a_provider_and_then_a_model_chosen(api_client, staged_document, login):
    update_preferences({"compilation": {"provider": "deepseek"}})
    response = build(api_client)
    assert (response.status_code, response.json()) == (409, {"detail": CHOOSE})
    # A new vault starts with neither. Nothing is asked of the helper for a missing provider.
    update_preferences({"compilation": {"provider": ""}})
    login["error"] = AssertionError("There is no provider to look up")
    response = build(api_client)
    assert (response.status_code, response.json()) == (409, {"detail": CONNECT})


def test_a_failing_helper_is_a_dependency_error_before_acceptance(
    api_client, staged_document, login
):
    login["error"] = pi_helper.PiHelperError(HELPER_FAILED)
    response = build(api_client)
    assert (response.status_code, response.json()) == (503, {"detail": HELPER_FAILED})
    assert not Job.objects.exists()


@pytest.mark.parametrize(
    "route,body",
    [
        ("/builds", {"dry_run": True}),
        ("/grouping-plan/preview", {}),
    ],
)
def test_local_planning_needs_no_login(api_client, staged_document, login, route, body):
    login["error"] = AssertionError("Planning must not read the provider login")
    assert (
        api_client.post(f"/v1{route}", data=body, content_type="application/json").status_code
        == 202
    )


def test_no_sources_does_not_require_a_login(api_client, login):
    login["connected"] = False
    assert build(api_client).status_code == 202


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"connected": False}, CONNECT),
        ({"models": ["another-model"]}, CHOOSE),
        ({"error": pi_helper.PiHelperError(HELPER_FAILED)}, HELPER_FAILED),
    ],
)
def test_accepted_job_can_fail_later_and_reports_the_same_reason_in_logs_and_api(
    api_client, staged_document, login, caplog, change, reason
):
    # A saved login allows acceptance without contacting the provider. Removing it
    # or its model before the worker starts must still fail the accepted job.
    response = build(api_client)
    assert response.status_code == 202
    job_id = response.json()["id"]

    login.update(change)
    run_attempt(claim_next_job("build"))
    detail = api_client.get(f"/v1/jobs/{job_id}")
    assert detail.status_code == 200  # Successfully reading a failed job is a successful request.
    assert detail.json()["job"]["status"] == "failed"
    assert detail.json()["job"]["error"] == reason
    assert f"Job {job_id} (build) failed: {reason}" in caplog.text
    snapshot = api_client.get("/v1/desktop-state").json()
    assert snapshot["jobs"][0]["job"]["error"] == reason
