"""Worker lifecycle and automatic input/index refreshes."""

import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from second_brain.apps.jobs.models import Job
from second_brain.apps.jobs.services import worker_supervisor
from second_brain.apps.jobs.services.job_execution import execute_job
from second_brain.apps.jobs.services.job_queue import claim_next_job
from second_brain.apps.jobs.services.worker_supervisor import input_fingerprint
from second_brain.apps.preferences.services.user_settings import update_preferences
from second_brain.apps.wiki.services.search_index import SearchIndex
from second_brain.pipeline_config import SourceConfig


@pytest.fixture
def supervisor(monkeypatch, config):
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
    monkeypatch.setattr(worker_supervisor.subprocess, "Popen", popen)
    monkeypatch.setattr(
        worker_supervisor, "threading", SimpleNamespace(Event=StopEvent, Thread=MonitorThread)
    )
    monkeypatch.setattr(worker_supervisor, "get_pipeline_config", lambda: config)
    # The production monitor owns a separate thread/connection; this fixture shares ours.
    monkeypatch.setattr(worker_supervisor, "close_old_connections", lambda: None)
    monkeypatch.setattr(worker_supervisor, "connections", SimpleNamespace(close_all=lambda: None))
    return SimpleNamespace(passes=passes, children=children, popen=popen, spawn=spawn)


def test_partial_startup_stops_already_started_worker(supervisor):
    child = supervisor.spawn()
    supervisor.popen.side_effect = [child, OSError("cannot spawn build worker")]

    with pytest.raises(OSError, match="cannot spawn build worker"):
        with worker_supervisor.supervised_workers():
            pytest.fail("Startup must fail before serving requests")

    child.terminate.assert_called_once()
    child.wait.assert_called_once()


def test_failed_restart_is_retried_and_other_lane_survives(supervisor, caplog):
    crashed, healthy, replacement = [supervisor.spawn() for _ in range(3)]
    crashed.poll.return_value = 1
    crashed.returncode = 1
    supervisor.popen.side_effect = [crashed, healthy, OSError("spawn failed"), replacement]
    supervisor.passes.extend([lambda: None, lambda: None])

    with worker_supervisor.supervised_workers():
        assert supervisor.popen.call_count == 4
        healthy.terminate.assert_not_called()
        assert "Unable to restart ingest worker" in caplog.text

    healthy.terminate.assert_called_once()
    replacement.terminate.assert_called_once()


def test_shutdown_kills_worker_that_does_not_stop(supervisor):
    with worker_supervisor.supervised_workers():
        child = supervisor.children[0]
        child.wait.side_effect = [subprocess.TimeoutExpired("worker", 10), 0]

    child.terminate.assert_called_once()
    child.kill.assert_called_once()
    assert child.wait.call_count == 2


def test_deleting_last_page_queues_index_cleanup(supervisor, config):
    page = config.wiki_dir / "concepts" / "note.md"
    page.parent.mkdir(exist_ok=True)
    page.write_text("---\ntitle: Note\ntype: concept\n---\nLast page")
    index = SearchIndex(config.search_db_path, config.search)
    index.sync_from_wiki(config.wiki_dir)
    assert len(index.list_pages()) == 1
    page.unlink()
    supervisor.passes.extend([lambda: None, lambda: None, lambda: None])

    with worker_supervisor.supervised_workers():
        job = Job.objects.get(kind="index")
        job.config_snapshot["search"]["semantic_enabled"] = False
        execute_job(job)

    assert index.list_pages() == []
    assert not Job.objects.filter(kind="build").exists()


def test_wiki_changes_waiting_for_active_index_are_not_lost(supervisor, config, job_factory):
    (config.wiki_dir / "note.md").write_text("---\ntitle: Note\n---\nUpdated page")
    active = job_factory(kind="index", status=Job.Status.RUNNING)

    def finish_active():
        assert Job.objects.filter(kind="index").count() == 1
        active.status = Job.Status.SUCCEEDED
        active.save(update_fields=["status"])

    supervisor.passes.extend([lambda: None, lambda: None, finish_active, lambda: None])
    with worker_supervisor.supervised_workers():
        assert Job.objects.filter(kind="index", status=Job.Status.QUEUED).count() == 1
    assert not Job.objects.filter(kind="build").exists()


def test_stable_watched_input_queues_ingestion_of_a_managed_copy(
    supervisor, config, manifest, tmp_path
):
    watched = tmp_path / "watched"
    watched.mkdir()
    original = watched / "note.md"
    original.write_text("Watched content")
    config.sources["research"] = SourceConfig(path=watched, file_types=("md",))
    update_preferences({"sources": {"research": {"path": str(watched), "file_types": ["md"]}}})
    supervisor.passes.extend([lambda: None, lambda: None, lambda: None])

    with worker_supervisor.supervised_workers():
        assert Job.objects.filter(kind="ingest").count() == 1
        result = execute_job(claim_next_job("ingest"))

    assert result == {"completed": 1, "failed": 0}
    assert original.read_text() == "Watched content"
    captured = config.drops_dir / "research" / "note.md"
    assert not captured.exists()
    assert manifest.get_origin_copy(original).status == "complete"
    assert "Watched content" in (config.raw_dir / "research" / "note.md").read_text()
    assert not Job.objects.filter(kind="build").exists()


def test_fingerprint_notices_changed_inputs(config, tmp_path):
    watched = tmp_path / "watched"
    watched.mkdir()
    settings = config.model_copy(
        update={"sources": {"notes": SourceConfig(path=watched, file_types=("md",))}}
    )
    assert input_fingerprint(settings) == ()
    note = watched / "note.md"
    note.write_text("first")
    before = input_fingerprint(settings)
    note.write_text("second version")
    assert input_fingerprint(settings) != before


def test_fingerprint_ignores_hidden_and_unsupported_files(config):
    (config.drops_dir / "documents" / ".note.md").write_text("hidden")
    (config.drops_dir / "documents" / "photo.png").write_text("image")
    assert input_fingerprint(config) == ()
