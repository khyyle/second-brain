"""Exercise real API-to-service-to-worker flows in an isolated vault."""

from uuid import uuid4

import pytest

from second_brain.apps.jobs.models import Job
from second_brain.apps.jobs.services import job_progress, job_queue
from second_brain.apps.jobs.worker import run_attempt
from second_brain.apps.library.models import Source
from second_brain.apps.preferences.services.user_settings import get_preferences


def send(api_client, method, path, body):
    return getattr(api_client, method)(f"/v1{path}", data=body, content_type="application/json")


def test_settings_have_no_runtime_paths_and_patch_preserves_nested_values(api_client, config):
    assert "data_dir" not in api_client.get("/v1/settings").json()
    first = send(api_client, "patch", "/settings", {"compilation": {"max_iterations": 17}})
    assert first.status_code == 200
    second = send(api_client, "patch", "/settings", {"compilation": {"max_cost_per_build_usd": 3}})
    assert second.json()["compilation"]["max_iterations"] == 17
    assert "data_dir" not in (config.data_dir / "config.yaml").read_text()


def test_provider_model_patch_is_validated_after_merging(api_client):
    assert (
        send(
            api_client,
            "patch",
            "/settings",
            {"compilation": {"provider": "deepseek", "model": "deepseek-v4-flash"}},
        ).status_code
        == 200
    )
    response = send(
        api_client, "patch", "/settings", {"compilation": {"model": "deepseek-v4-pro"}}
    )
    assert response.status_code == 200
    assert response.json()["compilation"]["provider"] == "deepseek"


@pytest.mark.parametrize(
    "patch", [{"data_dir": "/tmp/other"}, {"triage": None}, {"compilation": {"max_iterations": 0}}]
)
def test_invalid_settings_do_not_replace_saved_file(api_client, config, patch):
    send(api_client, "patch", "/settings", {"triage": {"enabled": False}})
    before = (config.data_dir / "config.yaml").read_bytes()
    assert send(api_client, "patch", "/settings", patch).status_code in {400, 422}
    assert (config.data_dir / "config.yaml").read_bytes() == before


def test_preferences_cache_notices_manual_edits_without_mutating_older_snapshot(
    api_client, config
):
    old = get_preferences()
    (config.data_dir / "config.yaml").write_text("triage:\n  enabled: false\n")
    assert api_client.get("/v1/settings").json()["triage"]["enabled"] is False
    assert old.triage.enabled is True


def test_reading_source_does_not_depend_on_valid_preferences(api_client, config):
    source = config.raw_dir / "documents" / "a.md"
    source.parent.mkdir(parents=True)
    source.write_text("Readable without settings")
    (config.data_dir / "config.yaml").write_text("bad: [")
    response = api_client.get("/v1/sources/content", {"raw_path": "documents/a.md"})
    assert response.status_code == 200
    assert response.json()["markdown"] == "Readable without settings"


def test_import_is_queued_then_worker_owns_parsing(api_client, config, tmp_path):
    original = tmp_path / "notes.md"
    original.write_text("# Test\n\nDetailed source notes.")
    response = send(api_client, "post", "/imports", {"paths": [str(original)]})
    assert response.status_code == 202, response.content
    assert not list(config.raw_dir.rglob("*.md"))
    job = job_queue.claim_next_job("ingest")
    assert str(job.pk) == response.json()["job"]["id"]
    run_attempt(job)
    job.refresh_from_db()
    assert job.status == "succeeded", job.error
    assert Source.objects.get().status == "complete"
    assert original.exists()
    assert list(config.raw_dir.rglob("*.md"))
    detail = api_client.get(f"/v1/jobs/{job.pk}").json()
    assert detail["job"]["result"]["completed"] == 1
    assert "payload" not in detail["job"] and "config_snapshot" not in detail["job"]


def test_build_captures_bytes_and_preferences_before_worker_runs(api_client, config):
    source = config.raw_dir / "documents" / "a.md"
    source.parent.mkdir(parents=True)
    source.write_text("version one")
    response = send(api_client, "post", "/builds", {"dry_run": True})
    assert response.status_code == 202, response.content
    job = Job.objects.get(pk=response.json()["id"])
    captured = config.data_dir / ".backend" / "jobs" / str(job.pk) / "raw" / "documents/a.md"
    source.write_text("version two")
    send(api_client, "patch", "/settings", {"compilation": {"max_iterations": 7}})
    assert captured.read_text() == "version one"
    assert job.config_snapshot["compilation"]["max_iterations"] == 100
    assert "data_dir" not in job.config_snapshot
    run_attempt(job_queue.claim_next_job("build"))
    job.refresh_from_db()
    assert job.status == "succeeded", job.error


def test_queued_cancellation_and_retry_create_distinct_attempts(api_client):
    response = send(api_client, "post", "/ingestion/runs", {"drops_only": True})
    first_id = response.json()["id"]
    assert send(api_client, "post", f"/jobs/{first_id}/cancel", {}).json()["status"] == "cancelled"
    assert job_queue.claim_next_job("ingest") is None
    retried = send(api_client, "post", f"/jobs/{first_id}/retry", {})
    assert retried.status_code == 202, retried.content
    assert retried.json()["id"] != first_id
    assert str(Job.objects.get(pk=retried.json()["id"]).retry_of_id) == first_id
    assert Job.objects.get(pk=first_id).status == "cancelled"


def test_recovery_marks_only_abandoned_running_lane_jobs(api_client, job_factory):
    running = job_factory(kind="build", lane="build", status="running")
    queued = job_factory(kind="build", lane="build")
    other = job_factory(kind="ingest", lane="ingest", status="running")
    assert job_queue.recover_lane("build") == 1
    running.refresh_from_db()
    queued.refresh_from_db()
    other.refresh_from_db()
    assert (running.status, queued.status, other.status) == ("interrupted", "queued", "running")


def test_progress_and_cancellation_are_attempt_scoped(api_client, job_factory):
    first = job_factory(kind="build", lane="build", status="running")
    second = job_factory(kind="ingest", lane="ingest", status="running")
    try:
        job_progress.bind_job(first.pk)
        job_progress.update_progress(
            phase="compile", current=1, total=4, started_at=job_progress.now_iso()
        )
        job_queue.cancel(second.pk)
        assert not job_progress.stop_requested()
        job_queue.cancel(first.pk)
        assert job_progress.stop_requested()
        second.refresh_from_db()
        assert second.progress == {}
    finally:
        job_progress.bind_job(None)


def test_wiki_read_domain_edit_search_and_history(api_client, config):
    page = config.wiki_dir / "concepts" / "test.md"
    page.write_text("---\ntitle: Test\ntype: concept\ndomains: [old]\n---\n\nSearchable material.")
    assert api_client.get("/v1/wiki/pages").json()[0]["title"] == "Test"
    assert api_client.get("/v1/wiki/content", {"path": "concepts/test.md"}).status_code == 200
    response = send(api_client, "post", "/domains/rename", {"old": "old", "new": "new"})
    assert response.status_code == 200, response.content
    assert response.json()["changed_pages"] == 1
    assert api_client.get("/v1/wiki/search", {"q": "Searchable"}).json()[0]["domains"] == ["new"]
    assert api_client.get("/v1/wiki/history").json()


def test_missing_job_and_unsafe_wiki_path_are_public_errors(api_client):
    assert api_client.get(f"/v1/jobs/{uuid4()}").status_code == 404
    assert api_client.get("/v1/wiki/content", {"path": "../../config.yaml"}).status_code == 400


def test_grouping_preview_without_clustering_needs_no_model(api_client, config):
    source = config.raw_dir / "documents" / "a.md"
    source.parent.mkdir(parents=True)
    source.write_text("A source")
    response = send(api_client, "post", "/grouping-plan/preview", {})
    assert response.status_code == 202
    run_attempt(job_queue.claim_next_job("build"))
    job = Job.objects.get(pk=response.json()["id"])
    assert job.status == "succeeded", job.error
    plan = api_client.get("/v1/grouping-plan").json()
    assert plan["source_count"] == 1
    assert (
        send(
            api_client, "put", "/grouping-plan/overrides", {"split_groups": ["unknown"]}
        ).status_code
        == 400
    )


def test_source_edit_cannot_invalidate_an_active_build(api_client, config):
    path = config.raw_dir / "documents/a.md"
    path.parent.mkdir(parents=True)
    path.write_text("captured")
    assert send(api_client, "post", "/builds", {"dry_run": True}).status_code == 202
    response = api_client.delete("/v1/sources?raw_path=documents/a.md")
    assert response.status_code == 409
    assert path.exists()


def test_wiki_edit_rejects_a_concurrent_writer(api_client, config):
    from second_brain.django_config.vault_storage import vault_lock

    with vault_lock("wiki"):
        response = send(api_client, "post", "/domains/rename", {"old": "a", "new": "b"})
    assert response.status_code == 409


def test_provider_status_never_serializes_the_key(api_client, monkeypatch):
    monkeypatch.setattr(
        "second_brain.apps.preferences.services.provider_credentials.read_api_key",
        lambda _: "secret-test-value",
    )
    response = api_client.get("/v1/credentials/anthropic")
    assert response.json() == {"provider": "anthropic", "configured": True}
    assert b"secret-test-value" not in response.content


def test_worker_retains_counts_and_marks_failed_results(api_client, monkeypatch):
    monkeypatch.setattr(
        "second_brain.apps.jobs.services.job_execution.execute_job",
        lambda _: {"outcome": "failed", "reason": "Local triage unavailable", "completed": 1},
    )
    job_queue.enqueue_job("ingest", {})
    job = job_queue.claim_next_job("ingest")
    run_attempt(job)
    job.refresh_from_db()
    assert job.status == "failed"
    assert job.result["completed"] == 1
    assert job.error == "Local triage unavailable"


def test_build_worker_commits_captured_version_and_leaves_newer_version_staged(
    api_client, config, monkeypatch, provider_key
):
    from second_brain.apps.compilation.models import CompiledSource
    from second_brain.apps.compilation.services.agent.loop import RunOutcome, RunResult
    from second_brain.file_hashing import hash_file

    source = config.raw_dir / "documents/a.md"
    source.parent.mkdir(parents=True)
    source.write_text("captured version")
    captured_hash = hash_file(source)
    response = send(api_client, "post", "/builds", {})
    source.write_text("edited after the request")
    observed = []

    def agent(context, sources, **kwargs):
        observed.append((context.raw_dir / sources[0]).read_text())
        page = context.wiki_dir / "concepts" / "built.md"
        page.write_text(
            "---\ntitle: Built\ntype: concept\ndomains: [testing]\n---\n\nCompiled content."
        )
        return RunResult(0.01, RunOutcome.COMPLETED)

    monkeypatch.setattr("second_brain.apps.compilation.services.compiler.run_agent", agent)
    run_attempt(job_queue.claim_next_job("build"))
    job = Job.objects.get(pk=response.json()["id"])
    assert job.status == "succeeded", job.error
    assert job.result["outcome"] == "ok"
    assert observed == ["captured version"]
    assert CompiledSource.objects.get(pk="documents/a.md").content_hash == captured_hash
    assert hash_file(source) != captured_hash
    assert api_client.get("/v1/wiki/history").json()


def test_diagnostics_uses_runtime_paths_and_returns_actionable_model_status(
    api_client, monkeypatch
):
    from second_brain.ollama.client import OllamaStatus

    monkeypatch.setattr(
        "second_brain.apps.preferences.views.check_ollama",
        lambda config: OllamaStatus(
            "http://localhost:11434", True, ("test-model",), ("test-model",)
        ),
    )
    response = api_client.get("/v1/diagnostics")
    assert response.status_code == 200
    assert response.json()["healthy"] is False
    assert "ollama pull test-model" in response.json()["message"]


def test_link_repair_api_passes_detected_renames_and_honors_dry_run(
    api_client, config, monkeypatch
):
    from second_brain.apps.wiki.services import git_history

    page = config.wiki_dir / "concepts" / "references.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("---\ntitle: References\ndomains: []\n---\nSee [[before]].\n")
    monkeypatch.setattr(git_history, "detect_renames", lambda _: {"before": "after"})
    preview = send(api_client, "post", "/wiki/repair-links", {"dry_run": True})
    assert preview.status_code == 200
    assert preview.json() == [{"rel_path": "concepts/references.md", "links_repointed": 1}]
    assert "[[before]]" in page.read_text()
    applied = send(api_client, "post", "/wiki/repair-links", {"dry_run": False})
    assert applied.status_code == 200
    assert "[[after]]" in page.read_text()
