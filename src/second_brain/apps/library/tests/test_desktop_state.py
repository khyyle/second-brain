"""Desktop composition and the authenticated streaming contract."""

import asyncio
import json
import os

import pytest
from watchdog.events import FileModifiedEvent, FileMovedEvent, FileOpenedEvent

from second_brain.apps.inference.services.worker_status import WorkerStatus, write_worker_status
from second_brain.apps.library.services import desktop_events


def test_snapshot_includes_both_workers_and_latest_outcomes(api_client, job_factory):
    old = job_factory(status="failed")
    latest = job_factory(status="succeeded")
    ingest = job_factory(kind="ingest", status="running", progress={"phase": "triage"})
    build = job_factory(
        status="running",
        progress={"phase": "compile"},
        payload={"private": "input"},
        config_snapshot={"private": "settings"},
    )
    result = api_client.get("/v1/desktop-state")
    assert result.status_code == 200
    body = result.json()
    jobs = {entry["job"]["id"]: entry for entry in body["jobs"]}
    assert set(jobs) == {str(latest.pk), str(ingest.pk), str(build.pk)}
    assert str(old.pk) not in jobs
    assert jobs[str(ingest.pk)]["progress"] == {"phase": "triage"}
    assert jobs[str(build.pk)]["progress"] == {"phase": "compile"}
    assert "payload" not in jobs[str(build.pk)]["job"]
    assert "config_snapshot" not in jobs[str(build.pk)]["job"]
    assert body["imports"] == body["triage"] == body["build_actions"] == []
    assert body["grouping_plan"] is None
    # A new vault has no provider or model chosen.
    assert (body["compilation_provider"], body["compilation_model"]) == ("", "")
    assert body["inference"] == {"worker_statuses": {"generation": None, "embedding": None}}


async def test_event_stream_requires_authentication(async_client):
    result = await async_client.get("/v1/events")
    assert result.status_code == 401


def test_notifications_cover_worker_writes_and_atomic_renames(tmp_path):
    changes = []
    handler = desktop_events.VaultChangeHandler(tmp_path, lambda: changes.append(True))
    for name in ("manifest.db-wal", "raw/documents/a.md", "wiki/topics/a.md"):
        handler.on_any_event(FileModifiedEvent(str(tmp_path / name)))
    handler.on_any_event(FileMovedEvent(str(tmp_path / ".tmp"), str(tmp_path / "config.yaml")))
    for purpose in ("generation", "embedding"):
        handler.on_any_event(
            FileMovedEvent(
                str(tmp_path / ".backend/.tmp"),
                str(tmp_path / f".backend/inference-{purpose}.json"),
            )
        )
    assert len(changes) == 6
    for name in (
        "manifest.db-shm",
        ".backend/locks/worker-build.lock",
        "wiki/.git/index",
        ".backend/inference-token",
        ".backend/inference-generation.json.tmp",
    ):
        handler.on_any_event(FileModifiedEvent(str(tmp_path / name)))
    handler.on_any_event(FileOpenedEvent(str(tmp_path / "raw/documents/a.md")))
    assert len(changes) == 6


def test_inference_changes_update_snapshots_and_match_status_endpoint(api_client, config):
    directory = config.data_dir / ".backend"
    directory.mkdir(exist_ok=True)
    status = WorkerStatus(
        purpose="embedding",
        model="nomic-embed-text",
        pid=os.getpid(),
        phase="downloading",
        completed=100,
        total=1000,
    )
    _, missing_signature = desktop_events.read_snapshot()
    write_worker_status(directory, status)
    payload, downloading_signature = desktop_events.read_snapshot()
    assert downloading_signature != missing_signature
    assert json.loads(payload)["inference"] == api_client.get("/v1/inference/status").json()

    status.phase = "error"
    status.error = "Model download failed."
    write_worker_status(directory, status)
    payload, failed_signature = desktop_events.read_snapshot()
    assert failed_signature != downloading_signature
    assert (
        json.loads(payload)["inference"]["worker_statuses"]["embedding"]["error"] == status.error
    )


@pytest.fixture
def quiet_observer(monkeypatch):
    """No OS thread in framing tests; real notifications are tested by gui-smoke."""

    class Observer:
        stopped = False
        joined = False

        def schedule(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            self.stopped = True

        def join(self):
            self.joined = True

    observer = Observer()
    monkeypatch.setattr(desktop_events, "Observer", lambda: observer)
    monkeypatch.setattr(desktop_events, "HEARTBEAT_SECONDS", 0.01)
    return observer


async def test_stream_deduplicates_and_reconciles_missed_changes(
    tmp_path,
    monkeypatch,
    quiet_observer,
):
    values = iter([('{"version":1}', "one"), ('{"version":1}', "one"), ('{"version":2}', "two")])
    monkeypatch.setattr(desktop_events, "read_snapshot", lambda: next(values))
    stream = desktop_events.stream_desktop_state(tmp_path)
    try:
        assert await anext(stream) == 'event: snapshot\ndata: {"version":1}\n\n'
        assert await anext(stream) == ": keepalive\n\n"
        assert await anext(stream) == ": keepalive\n\n"  # Same data wasn't resent.
        assert await asyncio.wait_for(anext(stream), 1) == (
            'event: snapshot\ndata: {"version":2}\n\n'
        )
    finally:
        await stream.aclose()
    assert quiet_observer.stopped and quiet_observer.joined


def test_snapshot_ignores_clock_only_changes(config, job_factory):
    from django.utils import timezone

    from second_brain.apps.jobs.models import Job

    job = job_factory(status="running", progress={"current": 1, "updated_at": "old"})
    first_payload, first_signature = desktop_events.read_snapshot()
    Job.objects.filter(pk=job.pk).update(
        heartbeat_at=timezone.now(), progress={"current": 1, "updated_at": "new"}
    )
    second_payload, second_signature = desktop_events.read_snapshot()
    assert first_payload != second_payload
    assert first_signature == second_signature


def test_estimates_use_the_chosen_models_price_and_stay_unknown_without_one(
    api_client, config, monkeypatch
):
    from second_brain.apps.preferences.services.agent_provider import model_selection
    from second_brain.apps.preferences.services.user_settings import update_preferences
    from second_brain.cloud_llm.pi import pi_helper

    sonnet = {"id": "claude-sonnet-4-6", "name": "Sonnet", "price": {"input": 3, "output": 15}}
    unpriced = {"id": "unpriced", "name": "Unpriced", "price": None}
    offered = [{**model, "contextWindow": 200_000} for model in (sonnet, unpriced)]
    lookups = []

    async def helper(command: str, provider: str) -> object:
        lookups.append(command)
        return offered if command == "list-models" else {"connected": True}

    monkeypatch.setattr(pi_helper, "run_json", helper)
    update_preferences({"compilation": {"provider": "anthropic", "model": "claude-sonnet-4-6"}})
    documents = config.raw_dir / "documents"
    documents.mkdir(parents=True)
    members = []
    for name in ("a.md", "b.md"):
        (documents / name).write_text("A source worth building. " * 40)
        members.append({"rel": f"documents/{name}", "bytes": (documents / name).stat().st_size})
    plan = {
        "generated_at": "2026-01-01T00:00:00+00:00",
        "algorithm": "threshold",
        "enabled": True,
        "source_count": 2,
        "group_count": 1,
        # A plan saved by an earlier version carried prices for a fixed list of models.
        "estimated_cost_usd": 99.0,
        "costs": {"claude-sonnet-4-6": 99.0},
        "groups": [{"id": "group", "title": "A", "members": members, "costs": {}}],
    }
    (config.data_dir / ".clusters.json").write_text(json.dumps(plan))

    def estimates() -> list:
        body = api_client.get("/v1/desktop-state").json()
        state, grouping = body["state"], body["grouping_plan"]
        return [
            state["estimated_cost_usd"],
            grouping["estimated_cost_usd"],
            grouping["groups"][0]["estimated_cost_usd"],
            *(source["estimated_cost_usd"] for source in state["staged"]),
        ]

    figures = estimates()
    assert len(figures) == 5 and all(0 < figure < 1 for figure in figures)
    # Snapshots are frequent; the helper is asked once per choice, not once per snapshot.
    assert estimates() == figures
    assert lookups == ["list-models"]

    update_preferences({"compilation": {"model": "unpriced"}})
    assert estimates() == [None] * 5
    update_preferences({"compilation": {"model": "not-offered"}})
    assert estimates() == [None] * 5
    assert lookups == ["list-models"] * 3

    # With no model chosen there is nothing to look up, and a stuck helper is not waited for.
    update_preferences({"compilation": {"provider": "deepseek"}})
    assert estimates() == [None] * 5
    assert lookups == ["list-models"] * 3

    async def stuck(command: str, provider: str) -> object:
        await asyncio.sleep(30)

    monkeypatch.setattr(pi_helper, "run_json", stuck)
    monkeypatch.setattr(model_selection, "_ESTIMATE_LOOKUP_SECONDS", 0.05)
    update_preferences({"compilation": {"model": "claude-sonnet-4-6"}})
    assert estimates() == [None] * 5
