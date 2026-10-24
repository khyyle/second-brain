"""Validate and stage imports; parsing and model work belong to the ingestion worker."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from django.db import transaction

from second_brain.apps.ingestion.services.source_manifest import Manifest
from second_brain.apps.ingestion.services.source_staging import (
    SUPPORTED_EXTENSIONS,
    stage_watched_file,
)
from second_brain.apps.jobs.models import Job
from second_brain.apps.jobs.services.job_queue import enqueue_job
from second_brain.apps.library.models import Source
from second_brain.apps.library.services.source_files import checked_path, reversible_moves
from second_brain.apps.preferences.services.user_settings import get_pipeline_config
from second_brain.django_config.errors import ConflictError, NotFoundError, ServiceError
from second_brain.django_config.paths import get_vault_paths
from second_brain.django_config.vault_storage import vault_lock


@dataclass(frozen=True)
class ImportedFile:
    file_path: str
    status: str
    updated_at: datetime
    error: str | None = None


@dataclass(frozen=True)
class ImportSubmission:
    job: Job
    files: list[ImportedFile]


def list_imports() -> list[ImportedFile]:
    """Read queued copies and durable outcomes for the import screen."""
    paths = get_vault_paths()
    records = {
        row.file_path: ImportedFile(row.file_path, row.status, row.updated_at, row.error)
        for row in Source.objects.all()
    }
    if paths.drops_dir.exists():
        for path in paths.drops_dir.rglob("*"):
            if (
                path.is_symlink()
                or not path.is_file()
                or path.suffix.lower() not in SUPPORTED_EXTENSIONS
                or any(part.startswith(".") for part in path.relative_to(paths.drops_dir).parts)
            ):
                continue
            if str(path) not in records:
                records[str(path)] = ImportedFile(
                    str(path), "pending", datetime.fromtimestamp(path.stat().st_mtime, UTC)
                )
    return sorted(records.values(), key=lambda row: (row.updated_at, row.file_path), reverse=True)


def import_files(paths: list[str], source_type: str) -> ImportSubmission:
    """Copy selected files into the vault and queue ingestion of those durable copies.

    Parameters
    ----------
    paths: list[str]
        Absolute local files selected by the user. Originals are never changed.
    source_type: str
        documents or chatgpt. JSON exports are accepted only on the chatgpt lane.

    Returns
    -------
    ImportSubmission
        Queued attempt and the selected files with their durable paths and statuses.

    Raises
    ------
    ServiceError
        Unsupported or inaccessible selection. Validate the entire batch before copying.
    """
    config = get_pipeline_config()
    selected = []
    for value in dict.fromkeys(paths):
        path = Path(value).expanduser()
        if not path.is_absolute() or not path.is_file() or path.is_symlink():
            raise ServiceError("Import selections must be existing absolute files, not symlinks.")
        path = path.resolve()
        if path.is_relative_to(config.data_dir):
            raise ServiceError("Choose an original outside the vault to import.")
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS or (
            source_type != "auto" and (suffix == ".json") != (source_type == "chatgpt")
        ):
            raise ServiceError(
                "Choose documents for document imports, or a JSON export for chats."
            )
        selected.append(path)
    with vault_lock("imports", blocking=False):
        if Job.objects.filter(lane="ingest", status="running").exists():
            raise ConflictError(
                "Wait for the current ingestion attempt before replacing imported copies."
            )
        config.ensure_directories()
        manifest = Manifest()
        staged = []
        files = []
        for path in selected:
            lane = (
                ("chatgpt" if path.suffix.lower() == ".json" else "documents")
                if source_type == "auto"
                else source_type
            )
            copy = stage_watched_file(path, lane, config, manifest)
            if copy is not None:
                staged.append(str(copy))
            record = Source.objects.filter(origin_path=str(path)).first()
            if record is not None:
                files.append(
                    ImportedFile(record.file_path, record.status, record.updated_at, record.error)
                )
        job = enqueue_job("ingest", {"paths": staged, "drops_only": True})
        return ImportSubmission(job, files)


def request_ingestion(*, drops_only: bool, file_path: str | None = None) -> Job:
    """Queue a scan or explicit retry of a vault-owned dropped file."""
    if file_path is not None:
        path = drop_path(file_path)
        if not path.is_file():
            raise NotFoundError("The queued file is no longer available.")
        return enqueue_job("ingest", {"paths": [str(path)], "retry": True, "drops_only": True})
    return enqueue_job("ingest", {"drops_only": drops_only})


def drop_path(file_path: str) -> Path:
    """Resolve only regular files beneath drops/, rejecting traversal and symlinks."""
    paths = get_vault_paths()
    supplied = Path(file_path)
    if not supplied.is_absolute() or ".." in supplied.parts:
        raise ServiceError("Use an absolute path inside this vault's drops folder.")
    try:
        relative = supplied.relative_to(paths.drops_dir)
    except ValueError as exc:
        raise ServiceError("Only copies inside drops can be removed or retried.") from exc
    if any(part.startswith(".") for part in relative.parts):
        raise ServiceError("Hidden import paths are not accessible.")
    return checked_path(paths.data_dir, "drops", str(relative))


def delete_import(file_path: str) -> None:
    """Trash a queued copy and remove its record; reject deletion while ingest work is active."""
    path = drop_path(file_path)
    paths = get_vault_paths()
    with (
        vault_lock("imports", blocking=False),
        reversible_moves() as files,
        transaction.atomic(durable=True),
    ):
        if Job.objects.filter(lane="ingest", status__in=["queued", "running"]).exists():
            raise ConflictError("Wait for ingestion to finish or cancel its queued jobs first.")
        if path.exists():
            destination = checked_path(paths.data_dir, ".trash", uuid4().hex, path.name)
            files.move(path, destination)
        Source.objects.filter(pk=str(path)).delete()
