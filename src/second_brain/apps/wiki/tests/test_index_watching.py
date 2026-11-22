"""Index refreshes follow stable wiki changes, including deletion."""

from collections.abc import Callable

import pytest

from second_brain.apps.jobs.models import Job
from second_brain.apps.jobs.services.job_execution import execute_job
from second_brain.apps.jobs.services.job_queue import claim_next_job
from second_brain.apps.jobs.worker import run_attempt
from second_brain.apps.wiki.services.index_watching import WikiIndexWatcher
from second_brain.apps.wiki.services.search_index import SearchIndex
from second_brain.apps.wiki.services.wiki_queries import refresh_index
from second_brain.pipeline_config import PipelineConfig


def test_deleting_last_page_queues_index_cleanup(config: PipelineConfig) -> None:
    page = config.wiki_dir / "concepts" / "note.md"
    page.parent.mkdir(exist_ok=True)
    page.write_text("---\ntitle: Note\ntype: concept\n---\nLast page")
    index = SearchIndex(config.search_db_path)
    index.sync_from_wiki(config.wiki_dir)
    assert len(index.list_pages()) == 1
    page.unlink()
    watcher = WikiIndexWatcher()

    watcher.scan(config)
    assert not Job.objects.exists()
    watcher.scan(config)
    watcher.scan(config)
    job = Job.objects.get(kind="index")
    execute_job(job)

    assert index.list_pages() == []
    assert not Job.objects.filter(kind="build").exists()


def test_wiki_changes_waiting_for_active_index_are_not_lost(
    config: PipelineConfig, job_factory: Callable[..., Job]
) -> None:
    (config.wiki_dir / "note.md").write_text("---\ntitle: Note\n---\nUpdated page")
    active = job_factory(kind="index", status=Job.Status.RUNNING)
    watcher = WikiIndexWatcher()
    watcher.scan(config)
    watcher.scan(config)
    assert Job.objects.filter(kind="index").count() == 1

    active.status = Job.Status.SUCCEEDED
    active.save(update_fields=["status"])
    watcher.scan(config)
    watcher.scan(config)

    assert Job.objects.filter(kind="index", status=Job.Status.QUEUED).count() == 1
    assert not Job.objects.filter(kind="build").exists()


def test_wiki_must_be_unchanged_between_scans(config: PipelineConfig) -> None:
    page = config.wiki_dir / "note.md"
    page.write_text("first version")
    watcher = WikiIndexWatcher()
    watcher.scan(config)
    page.write_text("a different version")
    watcher.scan(config)
    assert not Job.objects.exists()

    watcher.scan(config)
    watcher.scan(config)
    assert Job.objects.filter(kind="index").count() == 1


@pytest.mark.parametrize("already_claimed", [False, True], ids=["queued", "running"])
def test_restart_with_active_first_index_does_not_repeat_it(
    config: PipelineConfig, already_claimed: bool
) -> None:
    (config.wiki_dir / "index.md").write_text("Wiki navigation")
    watcher = WikiIndexWatcher()
    watcher.scan(config)
    watcher.scan(config)
    job = claim_next_job("build") if already_claimed else None

    restarted = WikiIndexWatcher()
    restarted.scan(config)
    restarted.scan(config)
    job = job or claim_next_job("build")
    assert job is not None
    run_attempt(job)
    restarted.scan(config)
    assert Job.objects.get(kind="index").status == Job.Status.SUCCEEDED


def test_empty_wiki_without_an_index_does_not_queue_work(config: PipelineConfig) -> None:
    watcher = WikiIndexWatcher()
    watcher.scan(config)
    watcher.scan(config)
    assert not Job.objects.exists()


def test_edit_during_indexing_schedules_one_successor(config: PipelineConfig) -> None:
    page = config.wiki_dir / "concepts" / "note.md"
    page.write_text("First version")
    watcher = WikiIndexWatcher()
    watcher.scan(config)
    watcher.scan(config)
    assert claim_next_job("build") is not None

    page.write_text("Second version with an edit")
    refresh_index(config)
    watcher.scan(config)
    watcher.scan(config)
    refresh_index(config)
    assert Job.objects.filter(kind="index", status=Job.Status.QUEUED).count() == 1
