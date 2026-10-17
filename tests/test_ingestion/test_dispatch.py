"""Tests for routing one dropped file to its parser."""

from __future__ import annotations

from pathlib import Path

from second_brain.config import Config
from second_brain.ingestion.dispatch import ingest_file
from second_brain.ingestion.manifest import Manifest
from second_brain.ingestion.parsing.router import UNREADABLE_PDF_REASON


def test_a_file_that_isnt_an_export_is_marked_failed_without_raising(
    config: Config, manifest: Manifest, tmp_path: Path
) -> None:
    drop = tmp_path / "conversations.json"
    drop.write_text('{"not": "an export"}', encoding="utf-8")

    assert ingest_file(drop, config, manifest, source_type="chatgpt") is False

    entry = manifest.get_entry(drop)
    assert entry is not None
    assert entry.status == "failed"
    assert manifest.get_ingest_failures()[str(drop)]
    assert drop.exists()


def test_a_damaged_pdf_fails_with_a_plain_reason(config: Config, manifest: Manifest) -> None:
    drop = config.drops_dir / "documents" / "truncated.pdf"
    drop.write_bytes(b"%PDF-1.4\n% truncated during upload\n")

    assert ingest_file(drop, config, manifest, source_type="documents") is False

    assert manifest.get_ingest_failures() == {str(drop): UNREADABLE_PDF_REASON}


def test_a_text_file_is_ingested_and_its_drop_copy_removed(
    config: Config, manifest: Manifest
) -> None:
    drop = config.drops_dir / "documents" / "notes.md"
    drop.write_text("# Notes\n\nSome text.\n", encoding="utf-8")

    assert ingest_file(drop, config, manifest, source_type="documents") is True

    assert (config.raw_dir / "documents" / "notes.md").is_file()
    assert not drop.exists()
