"""Queued builds finalize only the curation present when they were submitted."""

import json
from contextlib import nullcontext

import pytest

from second_brain.apps.compilation.services.agent.loop import RunOutcome, RunResult
from second_brain.apps.jobs.models import Job
from second_brain.apps.jobs.services.job_queue import claim_next_job
from second_brain.apps.jobs.worker import run_attempt
from second_brain.django_config.vault_storage import vault_lock


@pytest.fixture
def curation(config):
    source = config.raw_dir / "documents" / "notes.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("An eligible source")
    skipped = config.raw_dir / ".skipped" / "chatgpt" / "discarded.md"
    skipped.parent.mkdir(parents=True)
    skipped.write_text("Skipped conversation")
    plan = config.data_dir / ".clusters.json"
    plan.write_text(json.dumps({"groups": []}))
    return source, skipped, plan


@pytest.mark.parametrize("change", [None, "source", "skipped", "plan", "ingestion_active"])
def test_completed_job_cleans_only_unchanged_curation(
    api_client, config, curation, monkeypatch, change
):
    source, skipped, plan = curation
    monkeypatch.setattr(
        "second_brain.apps.compilation.services.compiler.run_agent",
        lambda *args, **kwargs: RunResult(0.01, RunOutcome.COMPLETED),
    )
    response = api_client.post("/v1/builds", data={}, content_type="application/json")
    assert response.status_code == 202
    if change == "source":
        source.write_text("Newly ingested version")
    elif change == "skipped":
        (skipped.parent / "new.md").write_text("Newly skipped chat")
    elif change == "plan":
        plan.write_text(json.dumps({"groups": [], "new_preview": True}))

    guard = vault_lock("imports") if change == "ingestion_active" else nullcontext()
    with guard:
        run_attempt(claim_next_job("build"))

    job = Job.objects.get(pk=response.json()["id"])
    assert job.status == "succeeded", job.error
    assert job.result["sources_compiled"] == 1
    assert skipped.exists() is (change is not None)
    assert plan.exists() is (change is not None)
    assert source.exists()


@pytest.mark.parametrize("dry_run", [True, False])
def test_planning_and_failed_jobs_preserve_curation(api_client, curation, monkeypatch, dry_run):
    _, skipped, plan = curation
    monkeypatch.setattr(
        "second_brain.apps.compilation.services.compiler.run_agent",
        lambda *args, **kwargs: RunResult(0.01, RunOutcome.FAILED, "Provider unavailable."),
    )
    response = api_client.post(
        "/v1/builds", data={"dry_run": dry_run}, content_type="application/json"
    )
    assert response.status_code == 202
    run_attempt(claim_next_job("build"))
    job = Job.objects.get(pk=response.json()["id"])
    assert job.status == ("succeeded" if dry_run else "failed")
    if not dry_run:
        assert job.error == "Provider unavailable."
    assert skipped.exists()
    assert plan.exists()
