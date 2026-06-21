"""Tests for the page cache and content-hash additions to the manifest."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from second_brain.ingestion.manifest import Manifest


@pytest.fixture
def manifest(tmp_path: Path) -> Manifest:
    """Fresh manifest backed by a temp SQLite file."""
    return Manifest(tmp_path / "manifest.db")


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    """A real on-disk file so SHA-256 computation has something to read."""
    p = tmp_path / "sample.pdf"
    p.write_bytes(b"%PDF-1.4\n% sample content\n")
    return p


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


def test_content_hash_preserved_on_partial_complete(
    manifest: Manifest, sample_file: Path
) -> None:
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


def test_get_all_with_limit(manifest: Manifest, tmp_path: Path) -> None:
    """The new `limit` parameter should cap returned rows."""
    for i in range(5):
        f = tmp_path / f"f{i}.pdf"
        f.write_bytes(f"pdf {i}".encode())
        manifest.mark_processing(f, "research_papers")

    rows = manifest.get_all(limit=3)
    assert len(rows) == 3


def test_identical_content_at_new_path_is_skipped(
    manifest: Manifest, tmp_path: Path
) -> None:
    """A duplicate file (same bytes, different name) should not need processing."""
    original = tmp_path / "paper.pdf"
    original.write_bytes(b"%PDF-1.4 identical bytes")
    manifest.mark_processing(original, "documents")
    manifest.mark_complete(original, parse_lane="docling", raw_output="documents/paper.md")

    duplicate = tmp_path / "paper-1.pdf"
    duplicate.write_bytes(b"%PDF-1.4 identical bytes")

    assert manifest.needs_processing(duplicate) is False


def test_different_content_at_new_path_still_processed(
    manifest: Manifest, tmp_path: Path
) -> None:
    """A genuinely different file must still be processed even if a name is similar."""
    original = tmp_path / "paper.pdf"
    original.write_bytes(b"%PDF-1.4 original")
    manifest.mark_processing(original, "documents")
    manifest.mark_complete(original, parse_lane="docling", raw_output="documents/paper.md")

    different = tmp_path / "paper-1.pdf"
    different.write_bytes(b"%PDF-1.4 a totally different document")

    assert manifest.needs_processing(different) is True


def test_duplicate_of_incomplete_is_still_processed(
    manifest: Manifest, tmp_path: Path
) -> None:
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


def test_compiled_tracking_is_separate_from_ingestion(
    manifest: Manifest, sample_file: Path
) -> None:
    """Ingestion completion must NOT mark a raw file as compiled."""
    manifest.mark_processing(sample_file, "documents")
    manifest.mark_complete(
        sample_file, parse_lane="docling", raw_output="documents/sample.md"
    )
    # Ingested, but never compiled.
    assert manifest.get_compiled_raw_paths() == set()

    manifest.mark_compiled(["documents/sample.md"])
    assert "documents/sample.md" in manifest.get_compiled_raw_paths()


def test_mark_compiled_is_idempotent(manifest: Manifest) -> None:
    manifest.mark_compiled(["a.md", "b.md"])
    manifest.mark_compiled(["a.md"])  # repeat
    assert manifest.get_compiled_raw_paths() == {"a.md", "b.md"}


def test_mark_compiled_empty_is_noop(manifest: Manifest) -> None:
    manifest.mark_compiled([])
    assert manifest.get_compiled_raw_paths() == set()


def test_reingest_invalidates_compiled(manifest: Manifest, sample_file: Path) -> None:
    """Re-ingesting a source clears its compiled marker so it recompiles."""
    manifest.mark_compiled(["documents/sample.md"])
    assert "documents/sample.md" in manifest.get_compiled_raw_paths()

    manifest.mark_processing(sample_file, "documents")
    manifest.mark_complete(
        sample_file, parse_lane="docling", raw_output="documents/sample.md"
    )

    assert "documents/sample.md" not in manifest.get_compiled_raw_paths()


def test_migration_adds_content_hash_to_legacy_db(tmp_path: Path) -> None:
    """An existing manifest table without content_hash should gain the column on open."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE manifest (
            file_path    TEXT PRIMARY KEY,
            sha256       TEXT NOT NULL,
            source_type  TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'pending',
            parse_lane   TEXT,
            raw_output   TEXT,
            ingested_at  TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        )"""
    )
    conn.execute(
        """INSERT INTO manifest VALUES
           ('/legacy.pdf', 'oldhash', 'documents', 'complete', 'docling',
            'documents/legacy.md', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"""
    )
    conn.commit()
    conn.close()

    Manifest(db_path)

    inspect = sqlite3.connect(db_path)
    cols = {row[1] for row in inspect.execute("PRAGMA table_info(manifest)")}
    inspect.close()

    assert "content_hash" in cols


def test_migration_drops_legacy_page_cache_confidence(tmp_path: Path) -> None:
    """A legacy page_cache with a confidence column is rebuilt without it."""
    db_path = tmp_path / "legacy_cache.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE page_cache (
            page_hash    TEXT PRIMARY KEY,
            parse_lane   TEXT NOT NULL,
            raw_markdown TEXT NOT NULL,
            confidence   REAL NOT NULL,
            parsed_at    TEXT NOT NULL
        )"""
    )
    conn.commit()
    conn.close()

    manifest = Manifest(db_path)

    inspect = sqlite3.connect(db_path)
    cols = {row[1] for row in inspect.execute("PRAGMA table_info(page_cache)")}
    inspect.close()

    assert "confidence" not in cols
    # The rebuilt table still works.
    manifest.put_cached_page("h", "chandra", "md")
    hit = manifest.get_cached_page("h")
    assert hit is not None and hit.raw_markdown == "md"
