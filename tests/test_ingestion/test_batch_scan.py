"""Tests for how the batch scan treats files whose content is already ingested."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from second_brain import cli
from second_brain.config import Config, SourceConfig
from second_brain.ingestion import dispatch
from second_brain.ingestion.manifest import Manifest
from second_brain.ingestion.triage.gemma import TriageDecision, TriageResult
from second_brain.ingestion.watcher import batch_scan

CONTENT = "# Notes\n\nSame bytes in every copy.\n"


@pytest.fixture
def watched_dir(tmp_path: Path) -> Path:
    path = tmp_path / "watched"
    path.mkdir()
    return path


@pytest.fixture
def scan_config(config: Config, watched_dir: Path) -> Config:
    """The test config with a drop lane and one watched folder, both taking Markdown."""
    return Config(
        data_dir=config.data_dir,
        sources={
            "documents": SourceConfig(path=config.drops_dir / "documents", file_types=("md",)),
            "watched": SourceConfig(path=watched_dir, file_types=("md",)),
        },
    )


def _record_ingested(manifest: Manifest, config: Config, path: Path, raw_output: str) -> None:
    """Record `path` as ingested, with its raw Markdown present on disk."""
    raw = config.raw_dir / raw_output
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(CONTENT, encoding="utf-8")
    manifest.mark_processing(path, "documents")
    manifest.mark_complete(path, raw_output=raw_output)


def _scan(config: Config, manifest: Manifest) -> list[Path]:
    processed: list[Path] = []
    batch_scan(config, manifest, lambda path, _source: processed.append(path))
    return processed


def test_redropped_copy_under_new_name_leaves_the_drop_queue(
    scan_config: Config, manifest: Manifest, watched_dir: Path
) -> None:
    original = watched_dir / "original.md"
    original.write_text(CONTENT, encoding="utf-8")
    _record_ingested(manifest, scan_config, original, "watched/original.md")
    copy = scan_config.drops_dir / "documents" / "copy.md"
    copy.write_text(CONTENT, encoding="utf-8")

    processed = _scan(scan_config, manifest)

    assert copy not in processed
    assert not copy.exists()
    entry = manifest.get_entry(copy)
    assert entry is not None
    assert entry.status == "duplicate"


def test_redropped_copy_under_same_name_leaves_the_drop_queue(
    scan_config: Config, manifest: Manifest
) -> None:
    dropped = scan_config.drops_dir / "documents" / "paper.md"
    dropped.write_text(CONTENT, encoding="utf-8")
    _record_ingested(manifest, scan_config, dropped, "documents/paper.md")

    processed = _scan(scan_config, manifest)

    assert dropped not in processed
    assert not dropped.exists()


def test_already_ingested_watched_original_is_left_in_place(
    scan_config: Config, manifest: Manifest, watched_dir: Path
) -> None:
    original = watched_dir / "original.md"
    original.write_text(CONTENT, encoding="utf-8")
    _record_ingested(manifest, scan_config, original, "watched/original.md")

    processed = _scan(scan_config, manifest)

    assert original not in processed
    assert original.exists()


def test_all_exports_are_unpacked_before_documents_regardless_of_source_order(
    config: Config, manifest: Manifest
) -> None:
    sources = {
        "documents": SourceConfig(path=config.drops_dir / "documents", file_types=("md",)),
        "chatgpt": SourceConfig(path=config.drops_dir / "chatgpt", file_types=("json",)),
    }
    mixed = config.model_copy(update={"sources": sources})
    document = sources["documents"].path / "a.md"
    exports = [sources["chatgpt"].path / name for name in ("b.json", "a.json")]
    for source in (document, *exports):
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(source.name, encoding="utf-8")
    processed: list[Path] = []

    batch_scan(mixed, manifest, lambda path, _source: processed.append(path))

    assert processed == sorted(exports) + [document]


def test_unpacked_chats_are_published_before_document_parsing(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = {
        "documents": SourceConfig(path=config.drops_dir / "documents", file_types=("md",)),
        "chatgpt": SourceConfig(path=config.drops_dir / "chatgpt", file_types=("json",)),
    }
    mixed = config.model_copy(update={"sources": sources})
    (sources["documents"].path / "notes.md").write_text(CONTENT, encoding="utf-8")
    export = sources["chatgpt"].path / "conversations.json"
    export.write_text(
        json.dumps(
            [
                {
                    "title": "Batch chat",
                    "id": "batch123",
                    "mapping": {
                        "node": {
                            "message": {
                                "author": {"role": "user"},
                                "content": {"parts": ["A conversation to import"]},
                            }
                        }
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    ingest_file = dispatch.ingest_file
    published: list[str] = []

    def observe_parse(path: Path, config: Config, manifest: Manifest, source_type: str) -> bool:
        if source_type == "documents":
            snapshot = json.loads((config.data_dir / ".state.json").read_text())
            published.extend(snapshot["pending_triage"])
        return ingest_file(path, config, manifest, source_type=source_type)

    monkeypatch.setattr(cli, "_load_config", lambda _path: mixed)
    monkeypatch.setattr(cli, "_preflight_check", lambda: None)
    monkeypatch.setattr(dispatch, "ingest_file", observe_parse)
    monkeypatch.setattr(
        "second_brain.ingestion.triage.pipeline.triage_file",
        lambda *_args: TriageResult(decision=TriageDecision.WORTHWHILE, confidence=1.0),
    )

    imported = CliRunner().invoke(cli.main, ["ingest", "--drops-only"])

    assert imported.exit_code == 0, imported.output
    assert published == ["chatgpt/batch-chat-batch123.md"]
