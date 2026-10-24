"""Desktop composition and the authenticated streaming contract."""

import asyncio

import pytest
from watchdog.events import FileModifiedEvent, FileMovedEvent, FileOpenedEvent

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
    assert body["compilation_model"]


async def test_event_stream_requires_authentication(async_client):
    result = await async_client.get("/v1/events")
    assert result.status_code == 401


def test_notifications_cover_worker_writes_and_atomic_renames(tmp_path):
    changes = []
    handler = desktop_events.VaultChangeHandler(tmp_path, lambda: changes.append(True))
    for name in ("manifest.db-wal", "raw/documents/a.md", "wiki/topics/a.md"):
        handler.on_any_event(FileModifiedEvent(str(tmp_path / name)))
    handler.on_any_event(FileMovedEvent(str(tmp_path / ".tmp"), str(tmp_path / "config.yaml")))
    assert len(changes) == 4
    for name in ("manifest.db-shm", ".backend/locks/worker-build.lock", "wiki/.git/index"):
        handler.on_any_event(FileModifiedEvent(str(tmp_path / name)))
    handler.on_any_event(FileOpenedEvent(str(tmp_path / "raw/documents/a.md")))
    assert len(changes) == 4


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
