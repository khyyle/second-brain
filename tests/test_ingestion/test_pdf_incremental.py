"""Integration tests for the per-page cache + content-hash short-circuit in pdf_handler.

These tests stub out the Chandra parser singleton so we never need the
real ML models. They exercise the orchestration logic: rendering pages,
hashing them, looking up the cache, and assembling the final result.
"""

from __future__ import annotations

import hashlib
from collections.abc import Generator
from pathlib import Path

import pytest

from second_brain.config import Config
from second_brain.ingestion import pdf_handler
from second_brain.ingestion.manifest import Manifest
from second_brain.ingestion.pdf_handler import process_pdf_sync
from second_brain.parsing.chandra_parser import RenderedPage
from second_brain.parsing.provider import ParseLane


class FakeChandraParser:
    """Stand-in for ChandraParser that records every page it OCRs."""

    def __init__(self, pages: list[RenderedPage]) -> None:
        self._pages = pages
        self.parse_pages_calls: list[list[RenderedPage]] = []

    def render_pdf_pages(self, _file_path: Path) -> list[RenderedPage]:
        return list(self._pages)

    def parse_pages(self, pages: list[RenderedPage]) -> list[str]:
        self.parse_pages_calls.append(list(pages))
        return [f"page-{p.page_number}" for p in pages]


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """Config rooted under tmp_path so writes are isolated per test."""
    cfg = Config(data_dir=tmp_path / "second-brain")
    cfg.ensure_directories()
    return cfg


@pytest.fixture
def manifest(config: Config) -> Manifest:
    return Manifest(config.manifest_db_path)


@pytest.fixture
def pdf_path(tmp_path: Path) -> Path:
    """Empty file standing in for a PDF — never parsed by the real parser."""
    p = tmp_path / "notebook.pdf"
    p.write_bytes(b"%PDF-1.4\nstub\n")
    return p


@pytest.fixture(autouse=True)
def _reset_singletons() -> Generator[None, None, None]:
    """Ensure no parser singleton leaks across tests."""
    pdf_handler._chandra_parser = None
    pdf_handler._docling_parser = None
    pdf_handler._fallback_parser = None
    yield
    pdf_handler._chandra_parser = None
    pdf_handler._docling_parser = None
    pdf_handler._fallback_parser = None


def _install_fake_chandra(
    monkeypatch: pytest.MonkeyPatch, pages: list[RenderedPage]
) -> FakeChandraParser:
    """Replace the handwriting parser + renderer so no real ML deps are touched."""
    fake = FakeChandraParser(pages)
    monkeypatch.setattr(pdf_handler, "_get_chandra", lambda config=None: fake)
    monkeypatch.setattr(pdf_handler, "_render_pdf_pages", lambda _p: list(pages))
    monkeypatch.setattr(pdf_handler, "check_parser_available", lambda _lane: None)
    monkeypatch.setattr(pdf_handler, "_check_handwriting_available", lambda _c: None)
    return fake


def _make_pages(payloads: list[bytes]) -> list[RenderedPage]:
    return [RenderedPage(page_number=i + 1, png_bytes=b) for i, b in enumerate(payloads)]


def test_first_run_ocrs_all_pages(
    config: Config,
    manifest: Manifest,
    pdf_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a cold cache, every page should be sent to the parser."""
    pages = _make_pages([b"png-A", b"png-B", b"png-C"])
    fake = _install_fake_chandra(monkeypatch, pages)

    out_dir = config.raw_dir / "goodnotes"
    ingest = process_pdf_sync(
        pdf_path,
        out_dir,
        config,
        force_lane="chandra",
        manifest=manifest,
    )

    assert len(fake.parse_pages_calls) == 1
    assert len(fake.parse_pages_calls[0]) == 3
    assert ingest.cache_stats is not None
    assert ingest.cache_stats.pages_total == 3
    assert ingest.cache_stats.pages_from_cache == 0
    assert ingest.cache_stats.pages_ocrd == 3
    assert ingest.content_unchanged is False
    assert manifest.page_cache_size() == 3


def test_second_run_with_unchanged_pages_hits_cache(
    config: Config,
    manifest: Manifest,
    pdf_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-running on a PDF whose pages render identically should make zero parser calls."""
    pages = _make_pages([b"png-A", b"png-B", b"png-C"])
    _install_fake_chandra(monkeypatch, pages)

    out_dir = config.raw_dir / "goodnotes"
    first = process_pdf_sync(
        pdf_path,
        out_dir,
        config,
        force_lane="chandra",
        manifest=manifest,
    )
    manifest.mark_processing(pdf_path, "goodnotes")
    manifest.mark_complete(
        pdf_path,
        parse_lane="chandra",
        raw_output=str(first.md_path.relative_to(config.raw_dir)),
        content_hash=first.content_hash,
    )

    fake_again = _install_fake_chandra(monkeypatch, pages)
    second = process_pdf_sync(
        pdf_path,
        out_dir,
        config,
        force_lane="chandra",
        manifest=manifest,
    )

    assert fake_again.parse_pages_calls == []
    assert second.cache_stats is not None
    assert second.cache_stats.pages_from_cache == 3
    assert second.cache_stats.pages_ocrd == 0
    assert second.content_unchanged is True
    assert second.content_hash == first.content_hash


def test_partial_change_ocrs_only_new_pages(
    config: Config,
    manifest: Manifest,
    pdf_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adding one page should trigger OCR for that page only — the rest hit cache."""
    initial = _make_pages([b"png-A", b"png-B", b"png-C"])
    _install_fake_chandra(monkeypatch, initial)

    out_dir = config.raw_dir / "goodnotes"
    first = process_pdf_sync(
        pdf_path,
        out_dir,
        config,
        force_lane="chandra",
        manifest=manifest,
    )
    manifest.mark_processing(pdf_path, "goodnotes")
    manifest.mark_complete(
        pdf_path,
        parse_lane="chandra",
        raw_output=str(first.md_path.relative_to(config.raw_dir)),
        content_hash=first.content_hash,
    )

    expanded = _make_pages([b"png-A", b"png-B", b"png-C", b"png-D"])
    fake = _install_fake_chandra(monkeypatch, expanded)

    second = process_pdf_sync(
        pdf_path,
        out_dir,
        config,
        force_lane="chandra",
        manifest=manifest,
    )

    assert len(fake.parse_pages_calls) == 1
    miss_batch = fake.parse_pages_calls[0]
    assert len(miss_batch) == 1
    assert miss_batch[0].page_number == 4
    assert miss_batch[0].png_bytes == b"png-D"

    assert second.cache_stats is not None
    assert second.cache_stats.pages_total == 4
    assert second.cache_stats.pages_from_cache == 3
    assert second.cache_stats.pages_ocrd == 1
    assert second.content_unchanged is False


def test_no_manifest_skips_caching(
    config: Config,
    pdf_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing manifest=None should bypass the cache entirely (legacy callsites)."""
    pages = _make_pages([b"png-A", b"png-B"])
    _install_fake_chandra(monkeypatch, pages)

    ingest = process_pdf_sync(
        pdf_path,
        config.raw_dir / "documents",
        config,
        force_lane="chandra",
        manifest=None,
    )

    assert ingest.cache_stats is not None
    assert ingest.cache_stats.pages_total == 2
    assert ingest.cache_stats.pages_from_cache == 0
    assert ingest.content_unchanged is False


def test_page_hash_is_sha256_of_png_bytes(
    config: Config,
    manifest: Manifest,
    pdf_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache key must be the raw SHA-256 of PNG bytes — verify by direct lookup."""
    pages = _make_pages([b"distinctive-page-bytes"])
    _install_fake_chandra(monkeypatch, pages)

    process_pdf_sync(
        pdf_path,
        config.raw_dir / "goodnotes",
        config,
        force_lane="chandra",
        manifest=manifest,
    )

    expected_hash = hashlib.sha256(b"distinctive-page-bytes").hexdigest()
    cached = manifest.get_cached_page(expected_hash)
    assert cached is not None
    assert cached.parse_lane == ParseLane.CHANDRA.value
