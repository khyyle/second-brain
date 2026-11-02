"""Stable source scans, pending changes, and automatic ingestion."""

from collections.abc import Callable
from pathlib import Path

from second_brain.apps.ingestion.services.source_manifest import Manifest
from second_brain.apps.ingestion.services.source_watching import SourceWatcher, source_fingerprint
from second_brain.apps.jobs.models import Job
from second_brain.apps.jobs.services.job_execution import execute_job
from second_brain.apps.jobs.services.job_queue import claim_next_job
from second_brain.apps.preferences.services.user_settings import (
    get_pipeline_config,
    update_preferences,
)
from second_brain.pipeline_config import PipelineConfig, SourceConfig


def test_stable_watched_input_queues_ingestion_of_a_managed_copy(
    config: PipelineConfig, manifest: Manifest, tmp_path: Path
) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    original = watched / "note.md"
    original.write_text("Watched content")
    config.sources["research"] = SourceConfig(path=watched, file_types=("md",))
    update_preferences({"sources": {"research": {"path": str(watched), "file_types": ["md"]}}})
    watcher = SourceWatcher()

    watcher.scan(config)
    assert not Job.objects.exists()
    watcher.scan(config)
    watcher.scan(config)
    assert Job.objects.filter(kind="ingest").count() == 1
    result = execute_job(claim_next_job("ingest"))

    assert result == {"completed": 1, "failed": 0}
    assert original.read_text() == "Watched content"
    assert not (config.drops_dir / "research" / "note.md").exists()
    assert manifest.get_origin_copy(original).status == "complete"
    assert "Watched content" in (config.raw_dir / "research" / "note.md").read_text()
    assert not Job.objects.filter(kind="build").exists()


def test_source_changes_waiting_for_active_ingestion_are_not_lost(
    config: PipelineConfig, job_factory: Callable[..., Job]
) -> None:
    note = config.drops_dir / "documents" / "note.md"
    note.write_text("First version")
    watcher = SourceWatcher()
    active = job_factory(kind="ingest", status=Job.Status.RUNNING)
    watcher.scan(config)
    note.write_text("Changed while ingestion is active")
    watcher.scan(config)
    watcher.scan(config)
    assert not Job.objects.filter(status=Job.Status.QUEUED).exists()

    active.status = Job.Status.SUCCEEDED
    active.save(update_fields=["status"])
    watcher.scan(config)
    watcher.scan(config)

    assert Job.objects.filter(kind="ingest", status=Job.Status.QUEUED).count() == 1


def test_preference_changes_trigger_another_scan_of_unchanged_sources(
    config: PipelineConfig,
) -> None:
    (config.drops_dir / "documents" / "note.md").write_text("Source")
    watcher = SourceWatcher()
    watcher.scan(config)
    watcher.scan(config)
    Job.objects.update(status=Job.Status.SUCCEEDED)

    update_preferences({"triage": {"min_word_count": config.triage.min_word_count + 1}})
    changed = get_pipeline_config()
    assert source_fingerprint(changed) == source_fingerprint(config)
    watcher.scan(changed)
    assert not Job.objects.filter(status=Job.Status.QUEUED).exists()
    watcher.scan(changed)

    queued = Job.objects.get(kind="ingest", status=Job.Status.QUEUED)
    assert queued.config_snapshot["triage"]["min_word_count"] == changed.triage.min_word_count


def test_fingerprint_notices_changed_inputs(config: PipelineConfig, tmp_path: Path) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    preferences = config.model_copy(
        update={"sources": {"notes": SourceConfig(path=watched, file_types=("md",))}}
    )
    assert source_fingerprint(preferences) == ()
    note = watched / "note.md"
    note.write_text("first")
    before = source_fingerprint(preferences)
    note.write_text("second version")
    assert source_fingerprint(preferences) != before


def test_fingerprint_ignores_hidden_unsupported_and_symlinked_files(
    config: PipelineConfig,
) -> None:
    documents = config.drops_dir / "documents"
    (documents / ".note.md").write_text("hidden")
    (documents / "photo.png").write_text("image")
    (documents / "linked.md").symlink_to(documents / ".note.md")
    hidden = documents / ".hidden"
    hidden.mkdir()
    (hidden / "nested.md").write_text("hidden directory")
    assert source_fingerprint(config) == ()
