"""Worker-side ingestion orchestration; builds remain an explicit separate operation."""

from pathlib import Path

from second_brain.apps.ingestion.services.file_imports import drop_path
from second_brain.apps.ingestion.services.file_ingestion import ingest_file
from second_brain.apps.ingestion.services.source_manifest import Manifest
from second_brain.apps.ingestion.services.source_scanning import batch_scan
from second_brain.apps.ingestion.services.source_staging import stage_watched_file
from second_brain.apps.ingestion.services.triage.pipeline import triage_pending
from second_brain.apps.jobs.services.job_progress import stop_requested
from second_brain.errors import SecondBrainError
from second_brain.file_hashing import hash_file
from second_brain.pipeline_config import PipelineConfig


class IngestionCancelled(BaseException):
    """Escapes the batch scanner's per-file error handling at a safe checkpoint."""


def run_ingestion(config: PipelineConfig, payload: dict) -> dict:
    """Parse queued copies and triage new chats with the attempt's captured preferences.

    Parameters
    ----------
    config: PipelineConfig
        Fixed execution paths and preference snapshot.
    payload: dict
        Optional selected paths or a request to scan drops and watched folders.

    Returns
    -------
    dict
        Completed/failed counts and a cancelled marker. Individual failures stay visible
        on Source records. Unchanged failures are retried only through an explicit retry.
    """
    config.ensure_directories()
    manifest = Manifest()
    counts = {"completed": 0, "failed": 0}

    def process(path: Path, source_type: str | None = None) -> None:
        if stop_requested():
            raise IngestionCancelled()
        ok = ingest_file(path, config, manifest, source_type)
        counts["completed" if ok else "failed"] += 1

    try:
        if "paths" in payload:
            for value in payload["paths"]:
                path = drop_path(value)
                if payload.get("retry"):
                    entry = manifest.get_entry(path)
                    if entry is not None and entry.origin_path:
                        original = Path(entry.origin_path)
                        if original.is_file() and (
                            not path.exists() or hash_file(original) != entry.origin_sha256
                        ):
                            path = (
                                stage_watched_file(
                                    original, entry.source_type, config, manifest, force=True
                                )
                                or path
                            )
                if not path.exists():
                    # Successfully consumed copies need not be parsed on a retry.
                    entry = manifest.get_entry(path)
                    if entry is None or entry.status not in {"complete", "duplicate"}:
                        counts["failed"] += 1
                    continue
                if payload.get("retry") or manifest.needs_processing(path, config.raw_dir):
                    process(path)
        else:
            batch_scan(config, manifest, process, drops_only=payload.get("drops_only", False))
        if config.triage.enabled and not stop_requested():
            triage_pending(config)
    except SecondBrainError as exc:
        return {**counts, "outcome": "failed", "reason": exc.reason}
    except IngestionCancelled:
        return {**counts, "cancelled": True}
    return counts
