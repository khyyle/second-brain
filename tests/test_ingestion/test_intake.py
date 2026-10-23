"""Watched originals feed managed copies into the shared ingestion queue."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from watchdog.events import FileCreatedEvent

from second_brain import cli
from second_brain.config import Config, SourceConfig
from second_brain.hashing import hash_file
from second_brain.ingestion import dispatch, watcher
from second_brain.ingestion.intake import stage_watched_folders
from second_brain.ingestion.manifest import Manifest


def _scan(config: Config, manifest: Manifest) -> int:
    def process(path: Path, name: str) -> None:
        dispatch.ingest_file(path, config, manifest, source_type=name)

    return watcher.batch_scan(config, manifest, process)


def _fail_scan(config: Config, manifest: Manifest) -> int:
    def fail(path: Path, name: str) -> None:
        manifest.mark_processing(path, name)
        manifest.mark_failed(path, "The parser is unavailable.")

    return watcher.batch_scan(config, manifest, fail)


def test_watched_files_are_all_copied_before_any_are_parsed(
    watched_config: Config, manifest: Manifest
) -> None:
    originals = [watched_config.sources["research"].path / name for name in ("one.md", "two.md")]
    for original in originals:
        original.write_text(original.name, encoding="utf-8")
    queued = watched_config.drops_dir / "research"
    parsed: list[Path] = []

    def process(path: Path, name: str) -> None:
        if not parsed:
            assert sorted(file.name for file in queued.iterdir()) == ["one.md", "two.md"]
        assert path.is_relative_to(watched_config.drops_dir)
        assert name == "research"
        parsed.append(path)
        assert dispatch.ingest_file(path, watched_config, manifest, source_type=name)

    assert watcher.batch_scan(watched_config, manifest, process) == 2
    assert all(original.read_text(encoding="utf-8") == original.name for original in originals)
    assert all(
        (watched_config.raw_dir / "research" / original.name).exists() for original in originals
    )
    assert not list(queued.iterdir())
    assert _scan(watched_config, manifest) == 0
    assert all(manifest.get_origin_copy(original).status == "complete" for original in originals)


@pytest.mark.parametrize("second_extension", ["md", "txt"])
def test_same_named_watched_inputs_keep_distinct_stable_output_names(
    watched_config: Config, manifest: Manifest, second_extension: str
) -> None:
    watched = watched_config.sources["research"].path
    first = watched / "first/notes.md"
    second = watched / f"second/notes.{second_extension}"
    for original, content in ((first, "first content"), (second, "second content")):
        original.parent.mkdir()
        original.write_text(content, encoding="utf-8")

    assert _scan(watched_config, manifest) == 2
    first_copy = manifest.get_origin_copy(first)
    second_copy = manifest.get_origin_copy(second)
    assert first_copy.raw_output_path == "research/notes.md"
    assert second_copy.raw_output_path == "research/notes-2.md"
    assert "first content" in (watched_config.raw_dir / first_copy.raw_output_path).read_text()
    assert "second content" in (watched_config.raw_dir / second_copy.raw_output_path).read_text()

    second.write_text("updated second content", encoding="utf-8")
    assert _scan(watched_config, manifest) == 1
    updated = manifest.get_origin_copy(second)
    assert updated.file_path == second_copy.file_path
    assert updated.raw_output_path == second_copy.raw_output_path
    assert (
        "updated second content" in (watched_config.raw_dir / updated.raw_output_path).read_text()
    )


def test_unchanged_failure_stays_visible_until_the_original_changes(
    watched_config: Config, manifest: Manifest
) -> None:
    original = watched_config.sources["research"].path / "notes.md"
    original.write_text("original content", encoding="utf-8")

    assert _fail_scan(watched_config, manifest) == 1
    failed = manifest.get_origin_copy(original)
    queued = Path(failed.file_path)
    assert queued.exists()
    assert manifest.get_ingest_failures() == {str(queued): "The parser is unavailable."}
    assert _fail_scan(watched_config, manifest) == 0
    assert queued.exists()
    assert original.exists()

    original.write_text("fixed content", encoding="utf-8")
    assert _scan(watched_config, manifest) == 1
    assert manifest.get_origin_copy(original).status == "complete"
    assert not manifest.get_ingest_failures()
    assert not queued.exists()
    assert "fixed content" in (watched_config.raw_dir / "research/notes.md").read_text()


@pytest.mark.parametrize("repair_original", [False, True])
def test_retry_uses_the_repaired_original_or_preserves_an_edited_queue_copy(
    watched_config: Config,
    manifest: Manifest,
    monkeypatch: pytest.MonkeyPatch,
    repair_original: bool,
) -> None:
    original = watched_config.sources["research"].path / "notes.md"
    original.write_text("original content", encoding="utf-8")
    _fail_scan(watched_config, manifest)
    queued = Path(manifest.get_origin_copy(original).file_path)
    (original if repair_original else queued).write_text("repaired content", encoding="utf-8")
    monkeypatch.setattr(cli, "_load_config", lambda _path: watched_config)
    monkeypatch.setattr(cli, "_preflight_check", lambda: None)

    retried = CliRunner().invoke(cli.main, ["ingest", "--path", str(queued)])

    assert retried.exit_code == 0, retried.output
    assert not queued.exists()
    assert not manifest.get_ingest_failures()
    assert "repaired content" in (watched_config.raw_dir / "research/notes.md").read_text()
    assert original.read_text() == ("repaired content" if repair_original else "original content")
    assert manifest.get_origin_copy(original).origin_sha256 == hash_file(original)
    assert _scan(watched_config, manifest) == 0


def test_pending_copy_can_finish_as_a_duplicate_without_losing_its_origin(
    watched_config: Config, manifest: Manifest
) -> None:
    dropped = watched_config.drops_dir / "documents/notes.md"
    dropped.write_text("shared content", encoding="utf-8")
    assert dispatch.ingest_file(dropped, watched_config, manifest)
    original = watched_config.sources["research"].path / "notes.md"
    original.write_text("shared content", encoding="utf-8")

    assert _scan(watched_config, manifest) == 0
    copied = manifest.get_origin_copy(original)
    assert copied.status == "duplicate"
    assert copied.raw_output_path == "documents/notes.md"
    assert not Path(copied.file_path).exists()
    assert original.exists()
    assert _scan(watched_config, manifest) == 0


def test_changed_legacy_original_keeps_its_existing_output_name(
    watched_config: Config, manifest: Manifest
) -> None:
    original = watched_config.sources["research"].path / "notes.md"
    original.write_text("old content", encoding="utf-8")
    assert dispatch.ingest_file(original, watched_config, manifest, source_type="research")
    original.write_text("new content", encoding="utf-8")

    assert _scan(watched_config, manifest) == 1
    copied = manifest.get_origin_copy(original)
    assert copied.raw_output_path == "research/notes.md"
    assert manifest.get_entry(original) is None
    assert "new content" in (watched_config.raw_dir / "research/notes.md").read_text()
    assert not (watched_config.raw_dir / "research/notes-2.md").exists()


def test_retry_keeps_the_watched_folders_pdf_parser_setting(
    watched_config: Config, manifest: Manifest, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = dict(watched_config.sources)
    sources["research"] = SourceConfig(
        path=sources["research"].path, file_types=("pdf",), force_parse_lane="docling"
    )
    config = watched_config.model_copy(update={"sources": sources})
    original = sources["research"].path / "notes.pdf"
    original.write_bytes(b"pdf input")
    _fail_scan(config, manifest)
    copied = manifest.get_origin_copy(original)
    lanes: list[str] = []

    def parse(path: Path, config: Config, manifest: Manifest, force_lane: str | None) -> None:
        lanes.append(force_lane)
        manifest.mark_complete(path, raw_output="research/notes.md")

    monkeypatch.setattr(dispatch, "_ingest_pdf", parse)
    monkeypatch.setattr(cli, "_load_config", lambda _path: config)
    monkeypatch.setattr(cli, "_preflight_check", lambda: None)

    retried = CliRunner().invoke(cli.main, ["ingest", "--path", copied.file_path])

    assert retried.exit_code == 0, retried.output
    assert lanes == ["docling"]
    assert manifest.get_origin_copy(original).source_type == "research"
    assert original.exists()


def test_continuous_watching_also_parses_a_managed_copy(
    watched_config: Config, manifest: Manifest, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = watched_config.sources["research"].path / "notes.md"
    original.write_text("watched content", encoding="utf-8")
    observer = Mock()

    def created() -> None:
        for call in observer.schedule.call_args_list:
            handler, directory = call.args
            if directory == str(original.parent):
                handler.on_created(FileCreatedEvent(str(original)))

    observer.start.side_effect = created
    monkeypatch.setattr(watcher, "Observer", lambda: observer)
    monkeypatch.setattr(watcher, "DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(watcher.time, "sleep", Mock(side_effect=[None, KeyboardInterrupt]))
    parsed: list[Path] = []

    def process(path: Path, name: str) -> None:
        parsed.append(path)
        dispatch.ingest_file(path, watched_config, manifest, source_type=name)

    watcher.watch_sources(watched_config, manifest, process)

    assert parsed == [watched_config.drops_dir / "research/notes.md"]
    assert original.exists()
    assert manifest.get_origin_copy(original).status == "complete"


def test_drops_only_parses_captured_files_without_scanning_watched_originals(
    watched_config: Config, manifest: Manifest
) -> None:
    watched = watched_config.sources["research"].path
    captured = watched / "captured.md"
    captured.write_text("already queued", encoding="utf-8")
    stage_watched_folders(watched_config, manifest)
    new = watched / "new.md"
    new.write_text("not yet queued", encoding="utf-8")

    count = watcher.batch_scan(
        watched_config,
        manifest,
        lambda path, name: dispatch.ingest_file(path, watched_config, manifest, source_type=name),
        drops_only=True,
    )

    assert count == 1
    assert manifest.get_origin_copy(captured).status == "complete"
    assert manifest.get_origin_copy(new) is None
    assert not (watched_config.raw_dir / "research/new.md").exists()


def test_missing_parsed_output_is_recovered_from_the_watched_original(
    watched_config: Config, manifest: Manifest
) -> None:
    original = watched_config.sources["research"].path / "notes.md"
    original.write_text("source content", encoding="utf-8")
    assert _scan(watched_config, manifest) == 1
    copied = manifest.get_origin_copy(original)
    (watched_config.raw_dir / copied.raw_output_path).unlink()

    assert _scan(watched_config, manifest) == 1
    assert manifest.get_origin_copy(original).file_path == copied.file_path
    assert (watched_config.raw_dir / copied.raw_output_path).exists()


def test_a_missing_pending_copy_does_not_lose_an_update_to_an_existing_source(
    watched_config: Config, manifest: Manifest
) -> None:
    original = watched_config.sources["research"].path / "notes.md"
    original.write_text("old content", encoding="utf-8")
    assert _scan(watched_config, manifest) == 1
    original.write_text("updated content", encoding="utf-8")
    stage_watched_folders(watched_config, manifest)
    pending = manifest.get_origin_copy(original)
    Path(pending.file_path).unlink()

    assert _scan(watched_config, manifest) == 1
    assert "updated content" in (watched_config.raw_dir / pending.raw_output_path).read_text()
