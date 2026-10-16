"""Tests for ingestion's manifest: file rows, failures, and the page cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from second_brain.compilation.store import BuildStore
from second_brain.ingestion.manifest import Manifest


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    """A real on-disk file so SHA-256 computation has something to read."""
    p = tmp_path / "sample.pdf"
    p.write_bytes(b"%PDF-1.4\n% sample content\n")
    return p


def test_mark_duplicate_records_terminal_state(manifest: Manifest, tmp_path: Path) -> None:
    original = tmp_path / "original.md"
    original.write_text("same bytes", encoding="utf-8")
    manifest.mark_processing(original, "research-papers")
    manifest.mark_complete(original, raw_output="research-papers/original.md")

    copy = tmp_path / "copy.md"
    copy.write_text("same bytes", encoding="utf-8")
    manifest.mark_duplicate(copy, "documents")

    # Terminal: the duplicate no longer needs processing, and re-marking is a no-op.
    assert manifest.needs_processing(copy) is False
    manifest.mark_duplicate(copy, "documents")
    entry = manifest.get_entry(copy)
    assert entry is not None
    assert entry.status == "duplicate"
    assert entry.raw_output_path == "research-papers/original.md"

    # Editing the copy makes it real new content again.
    copy.write_text("different bytes", encoding="utf-8")
    assert manifest.needs_processing(copy) is True


def test_reingesting_a_source_releases_its_deferral(
    manifest: Manifest, build_store: BuildStore, tmp_path: Path
) -> None:
    # The content that failed to compile is no longer the content on disk,
    # so an updated drop gets a fresh attempt instead of staying parked.
    source = tmp_path / "notes.md"
    source.write_text("v2 of the notes", encoding="utf-8")
    manifest.mark_processing(source, "documents")
    build_store.defer_sources(["documents/notes.md"], "did not finish", retryable=True)

    manifest.mark_complete(source, raw_output="documents/notes.md")

    assert build_store.get_deferred_sources() == {}


def test_page_cache_round_trip(manifest: Manifest) -> None:
    """A put_cached_page entry should be returned verbatim by get_cached_page."""
    manifest.put_cached_page(
        page_hash="a" * 64,
        parse_lane="chandra",
        raw_markdown="# Page 1\nfoo",
    )

    hit = manifest.get_cached_page("a" * 64)

    assert hit is not None
    assert hit.parse_lane == "chandra"
    assert hit.raw_markdown == "# Page 1\nfoo"


def test_page_cache_miss_returns_none(manifest: Manifest) -> None:
    assert manifest.get_cached_page("missing") is None


def test_page_cache_upsert_overwrites(manifest: Manifest) -> None:
    """Re-storing the same hash should update the markdown."""
    manifest.put_cached_page("h", "chandra", "old")
    manifest.put_cached_page("h", "chandra", "new")

    hit = manifest.get_cached_page("h")
    assert hit is not None
    assert hit.raw_markdown == "new"


def test_page_cache_size(manifest: Manifest) -> None:
    assert manifest.page_cache_size() == 0
    manifest.put_cached_page("h1", "chandra", "a")
    manifest.put_cached_page("h2", "chandra", "b")
    assert manifest.page_cache_size() == 2


def test_content_hash_round_trip(manifest: Manifest, sample_file: Path) -> None:
    """mark_complete should persist content_hash and get_content_hash should read it."""
    manifest.mark_processing(sample_file, "research_papers")
    manifest.mark_complete(
        sample_file,
        parse_lane="docling",
        raw_output="research_papers/sample.md",
        content_hash="c" * 64,
    )

    assert manifest.get_content_hash(sample_file) == "c" * 64


def test_content_hash_default_none(manifest: Manifest, sample_file: Path) -> None:
    """A newly-tracked file with no content_hash should report None."""
    manifest.mark_processing(sample_file, "research_papers")
    assert manifest.get_content_hash(sample_file) is None


def test_content_hash_preserved_on_partial_complete(manifest: Manifest, sample_file: Path) -> None:
    """Re-marking complete without supplying content_hash must not overwrite a stored value."""
    manifest.mark_processing(sample_file, "research_papers")
    manifest.mark_complete(
        sample_file,
        parse_lane="docling",
        raw_output="research_papers/sample.md",
        content_hash="d" * 64,
    )
    manifest.mark_complete(
        sample_file,
        parse_lane="docling",
        raw_output="research_papers/sample.md",
        content_hash=None,
    )

    assert manifest.get_content_hash(sample_file) == "d" * 64


def test_identical_content_at_new_path_is_skipped(manifest: Manifest, tmp_path: Path) -> None:
    """A duplicate file (same bytes, different name) should not need processing."""
    original = tmp_path / "paper.pdf"
    original.write_bytes(b"%PDF-1.4 identical bytes")
    manifest.mark_processing(original, "documents")
    manifest.mark_complete(original, parse_lane="docling", raw_output="documents/paper.md")

    duplicate = tmp_path / "paper-1.pdf"
    duplicate.write_bytes(b"%PDF-1.4 identical bytes")

    assert manifest.needs_processing(duplicate) is False


def test_different_content_at_new_path_still_processed(manifest: Manifest, tmp_path: Path) -> None:
    """A genuinely different file must still be processed even if a name is similar."""
    original = tmp_path / "paper.pdf"
    original.write_bytes(b"%PDF-1.4 original")
    manifest.mark_processing(original, "documents")
    manifest.mark_complete(original, parse_lane="docling", raw_output="documents/paper.md")

    different = tmp_path / "paper-1.pdf"
    different.write_bytes(b"%PDF-1.4 a totally different document")

    assert manifest.needs_processing(different) is True


def test_duplicate_of_incomplete_is_still_processed(manifest: Manifest, tmp_path: Path) -> None:
    """Cross-path skip only applies once the first copy actually completed."""
    original = tmp_path / "paper.pdf"
    original.write_bytes(b"same bytes")
    manifest.mark_processing(original, "documents")  # processing, not complete

    duplicate = tmp_path / "paper-1.pdf"
    duplicate.write_bytes(b"same bytes")

    assert manifest.needs_processing(duplicate) is True


def test_remove_entries_deletes_rows(manifest: Manifest, tmp_path: Path) -> None:
    """remove_entries should delete the named rows and return the count."""
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    manifest.mark_processing(a, "documents")
    manifest.mark_processing(b, "documents")

    deleted = manifest.remove_entries([a])

    assert deleted == 1
    assert manifest.get_entry(a) is None
    assert manifest.get_entry(b) is not None


def test_remove_entries_empty_is_noop(manifest: Manifest) -> None:
    assert manifest.remove_entries([]) == 0


def test_reingest_invalidates_compiled(
    manifest: Manifest, build_store: BuildStore, sample_file: Path
) -> None:
    """Re-ingesting a source clears its compiled marker so it recompiles."""
    build_store.mark_compiled(["documents/sample.md"])

    manifest.mark_processing(sample_file, "documents")
    manifest.mark_complete(sample_file, parse_lane="docling", raw_output="documents/sample.md")

    assert "documents/sample.md" not in build_store.get_compiled_raw_paths()


def test_ingesting_a_source_does_not_mark_it_compiled(
    manifest: Manifest, build_store: BuildStore, sample_file: Path
) -> None:
    manifest.mark_processing(sample_file, "documents")
    manifest.mark_complete(sample_file, parse_lane="docling", raw_output="documents/sample.md")

    assert build_store.get_compiled_raw_paths() == set()


def test_failure_reason_is_kept_until_the_file_is_retried(
    manifest: Manifest, sample_file: Path
) -> None:
    manifest.mark_processing(sample_file, "documents")
    manifest.mark_failed(sample_file, "Chandra failed on page 3")

    assert manifest.get_ingest_failures() == {str(sample_file): "Chandra failed on page 3"}

    manifest.mark_processing(sample_file, "documents")

    assert manifest.get_ingest_failures() == {}
