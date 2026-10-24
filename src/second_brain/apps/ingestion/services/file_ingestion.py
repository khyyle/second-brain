"""Ingest one file: pick its parser, write the raw output, and record the result."""

from __future__ import annotations

import logging
from pathlib import Path

from second_brain.apps.ingestion.services.source_manifest import Manifest
from second_brain.apps.ingestion.services.source_staging import source_name
from second_brain.apps.jobs.outcomes import CRASH_REASON
from second_brain.apps.library.services.source_files import checked_path
from second_brain.errors import SecondBrainError
from second_brain.pipeline_config import PipelineConfig

logger = logging.getLogger(__name__)

_TEXT_SUFFIXES = (".md", ".txt", ".tex")


def ingest_file(
    file_path: Path,
    config: PipelineConfig,
    manifest: Manifest,
    source_type: str | None = None,
) -> bool:
    """
    Route a single file to the appropriate parser and record the result.

    A failure is recorded on the file's manifest row and does not raise, so
    one bad file never stops the rest of a batch. Unchanged failed files wait
    for an explicit retry; changed files are eligible on the next scan.

    Parameters
    ----------
    file_path: Path
        Absolute path to the file to ingest.
    config: PipelineConfig
        Application configuration (used for output directories).
    manifest: Manifest
        Ingestion manifest to record processing status.
    source_type: str | None
        Source label, inferred from its queue lane or watched folder when omitted.

    Returns
    -------
    bool
        ``True`` when the file was ingested, ``False`` when it was missing or
        marked failed.
    """
    if not file_path.exists():
        logger.warning("File not found: %s", file_path)
        return False

    source_type = source_type or source_name(file_path, config)
    source = config.sources.get(source_type)
    force_lane = source.force_parse_lane if source else None
    manifest.mark_processing(file_path, source_type)

    suffix = file_path.suffix.lower()
    try:
        if suffix == ".pdf":
            _ingest_pdf(file_path, config, manifest, force_lane)
        elif suffix in _TEXT_SUFFIXES:
            _ingest_text(file_path, config, manifest)
        elif suffix == ".json":
            _ingest_chatgpt_export(file_path, config, manifest)
        else:
            manifest.mark_failed(file_path, f"Unsupported file type: {suffix}.")
            return False
    except SecondBrainError as exc:
        manifest.mark_failed(file_path, exc.reason)
        return False
    except Exception:
        logger.exception("Failed to ingest %s", file_path)
        manifest.mark_failed(file_path, CRASH_REASON)
        return False

    from second_brain.apps.ingestion.services.source_scanning import remove_drop_copy

    remove_drop_copy(file_path, config)
    return True


def _ingest_pdf(
    file_path: Path, config: PipelineConfig, manifest: Manifest, force_lane: str | None
) -> None:
    from second_brain.apps.ingestion.services.parsing.pdf_handler import process_pdf_sync

    output_dir = checked_path(
        config.data_dir, "raw", source_name(file_path, config), ".publication"
    ).parent
    ingest = process_pdf_sync(
        file_path, output_dir, config, force_lane=force_lane, manifest=manifest
    )
    manifest.mark_complete(
        file_path,
        parse_lane=force_lane or "auto",
        raw_output=str(ingest.md_path.relative_to(config.raw_dir)),
        content_hash=ingest.content_hash,
    )
    stats = ingest.cache_stats
    if stats is not None and stats.pages_total > 0:
        logger.info(
            "Page cache: %d/%d hits (%d OCR'd)",
            stats.pages_from_cache,
            stats.pages_total,
            stats.pages_ocrd,
        )
    if ingest.content_unchanged:
        logger.info("Content unchanged; the build can skip %s", file_path.name)


def _ingest_text(file_path: Path, config: PipelineConfig, manifest: Manifest) -> None:
    from second_brain.apps.ingestion.services.parsing.text_handler import process_text_file

    output_dir = checked_path(
        config.data_dir, "raw", source_name(file_path, config), ".publication"
    ).parent
    md_path = process_text_file(file_path, output_dir)
    manifest.mark_complete(
        file_path, parse_lane="passthrough", raw_output=str(md_path.relative_to(config.raw_dir))
    )


def _ingest_chatgpt_export(file_path: Path, config: PipelineConfig, manifest: Manifest) -> None:
    from second_brain.apps.ingestion.services.parsing.chatgpt_parser import process_chatgpt_export

    for conversation in process_chatgpt_export(
        file_path, checked_path(config.data_dir, "raw", "chatgpt", ".publication").parent
    ):
        manifest.mark_complete(
            file_path,
            parse_lane="passthrough",
            raw_output=str(conversation.relative_to(config.raw_dir)),
        )
