"""Capture build inputs at request time"""

import json
import shutil
from uuid import uuid4

from second_brain.apps.compilation.services.build_curation import capture_curation_version
from second_brain.apps.compilation.services.clustering import preview as clustering
from second_brain.apps.compilation.services.staging import find_new_sources
from second_brain.apps.ingestion.services.triage.pipeline import worthwhile_sources
from second_brain.apps.jobs.models import Job
from second_brain.apps.jobs.services.job_queue import enqueue_job
from second_brain.apps.library.services.source_files import checked_path
from second_brain.apps.preferences.services.user_settings import (
    get_pipeline_config,
    get_preferences,
)
from second_brain.django_config.errors import ConflictError, ServiceError
from second_brain.django_config.paths import get_vault_paths
from second_brain.django_config.vault_storage import atomic_write, vault_lock
from second_brain.file_hashing import hash_file
from second_brain.llm.client import require_api_key
from second_brain.llm.errors import ModelError
from second_brain.llm.providers import resolve_profile


def request_build(*, full: bool = False, dry_run: bool = False, preview: bool = False) -> Job:
    """
    Freeze eligible Markdown bytes, preferences, and reviewed grouping before enqueueing.

    Parameters
    ----------
    full: bool
        Include previously built versions. Chat triage eligibility still applies.
    dry_run: bool
        Plan the captured inputs without invoking the compilation agent.
    preview: bool
        Compute optional chat grouping instead of building wiki content.

    Returns
    -------
    Job
        Durable queued attempt referencing its private input directory. New ingestion
        and preference edits cannot change these bytes or settings.

    Notes
    -----
    Inputs are copied before the job row becomes visible. An unsuccessful enqueue
    removes the copies. Crash-orphaned directories are harmless and retained for review.
    Builds with eligible inputs check local credential availability before enqueueing.
    This does not contact the provider or prove the key is valid. The worker reads
    Keychain again at execution time; secrets are never captured with these inputs.
    """
    preferences = get_preferences().model_dump(mode="json")
    config = get_pipeline_config(preferences)
    job_id = uuid4()
    root = get_vault_paths().backend_dir / "jobs" / str(job_id) / "raw"
    root.mkdir(parents=True, mode=0o700)
    try:
        with vault_lock("raw-sources"), vault_lock("grouping"):
            sources = (
                sorted(
                    str(path.relative_to(config.raw_dir))
                    for path in config.raw_dir.rglob("*.md")
                    if not any(
                        part.startswith(".") for part in path.relative_to(config.raw_dir).parts
                    )
                )
                if full
                else find_new_sources(config)
            )
            if config.triage.enabled:
                sources = worthwhile_sources(config, sources)
            if sources and not dry_run and not preview:
                profile = resolve_profile(config.compilation.provider, config.compilation.model)
                try:
                    require_api_key(profile)
                except ModelError as exc:
                    # Missing local credentials are a correctable setup conflict.
                    raise ConflictError(exc.reason) from exc
            hashes = {}
            for relative in sources:
                source = checked_path(config.data_dir, "raw", relative)
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                # Atomic parser publication lets a reader see one complete version.
                destination.write_bytes(source.read_bytes())
                destination.chmod(0o400)
                hashes[relative] = hash_file(destination)
            units = clustering.reconcile_work_units(config.data_dir, sources)
            return enqueue_job(
                "preview" if preview else "build",
                {
                    "full": full,
                    "dry_run": dry_run,
                    "sources": sources,
                    "hashes": hashes,
                    "work_units": units,
                    "curation_version": capture_curation_version(),
                },
                snapshot=preferences,
                job_id=job_id,
            )
    except BaseException:
        shutil.rmtree(root.parent)
        raise


def get_grouping() -> dict | None:
    """Return the last preview and its current user corrections, or None."""
    root = get_vault_paths().data_dir
    with vault_lock("grouping"):
        plan = clustering.load_preview(root)
        return {**plan, "overrides": clustering._load_overrides(root)} if plan else None


def update_grouping(overrides: dict) -> dict:
    """Validate member/group IDs against the current preview and atomically save corrections."""
    root = get_vault_paths().data_dir
    with vault_lock("grouping"):
        plan = clustering.load_preview(root)
        if plan is None:
            raise ServiceError("Create a grouping preview first.")
        if not set(overrides["excluded"]) <= clustering.preview_members(plan):
            raise ServiceError("An excluded source is not part of this preview.")
        if not set(overrides["split_groups"]) <= {group["id"] for group in plan["groups"]}:
            raise ServiceError("A split group is not part of this preview.")
        atomic_write(root / clustering.OVERRIDES_FILENAME, json.dumps(overrides))
    return overrides


def clear_grouping() -> None:
    """Discard the preview and corrections without changing source eligibility."""
    with vault_lock("grouping"):
        clustering.clear_preview(get_vault_paths().data_dir)
