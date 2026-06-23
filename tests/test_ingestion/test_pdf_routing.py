"""Tests for per-page routing and hybrid Docling+Chandra merging."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from second_brain.config import Config
from second_brain.ingestion import pdf_handler
from second_brain.ingestion.manifest import Manifest
from second_brain.ingestion.pdf_handler import process_pdf_sync
from second_brain.parsing.chandra_parser import RenderedPage
from second_brain.parsing.provider import PageParseError, ParseBlock, ParseLane, ParseResult


class FakeChandra:
    """Chandra stub that records which pages it OCRs."""

    def __init__(self, pages: list[RenderedPage]) -> None:
        self._pages = pages
        self.parsed: list[list[RenderedPage]] = []

    def render_pdf_pages(self, _file_path: Path) -> list[RenderedPage]:
        return list(self._pages)

    def parse_pages(self, pages: list[RenderedPage]) -> list[str]:
        self.parsed.append(list(pages))
        return [f"CHANDRA-p{p.page_number}" for p in pages]


class FakeDocling:
    """Docling stub returning one text block per typed page."""

    def __init__(self, typed_pages: list[int]) -> None:
        self._typed_pages = typed_pages
        self.parse_calls = 0

    async def parse(self, _file_path: str) -> ParseResult:
        self.parse_calls += 1
        blocks = [
            ParseBlock(
                content=f"DOCLING-p{pn}",
                block_type="text",
                page_number=pn,
                reading_order=pn - 1,
            )
            for pn in self._typed_pages
        ]
        return ParseResult(
            markdown="\n".join(b.content or "" for b in blocks),
            blocks=blocks,
            metadata={"parse_lane": ParseLane.DOCLING.value, "pages": len(blocks)},
        )


@pytest.fixture
def config(tmp_path: Path) -> Config:
    cfg = Config(data_dir=tmp_path / "second-brain")
    cfg.ensure_directories()
    return cfg


@pytest.fixture
def manifest(config: Config) -> Manifest:
    return Manifest(config.manifest_db_path)


@pytest.fixture
def pdf_path(tmp_path: Path) -> Path:
    p = tmp_path / "hybrid.pdf"
    p.write_bytes(b"%PDF-1.4\nstub\n")
    return p


@pytest.fixture(autouse=True)
def _reset_singletons() -> Generator[None, None, None]:
    pdf_handler._chandra_parser = None
    pdf_handler._docling_parser = None
    pdf_handler._fallback_parser = None
    yield
    pdf_handler._chandra_parser = None
    pdf_handler._docling_parser = None
    pdf_handler._fallback_parser = None


def _rendered(n: int) -> list[RenderedPage]:
    return [RenderedPage(page_number=i + 1, png_bytes=f"png-{i}".encode()) for i in range(n)]


def test_hybrid_merges_docling_and_chandra_in_page_order(
    config: Config,
    manifest: Manifest,
    pdf_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pages 1,3 handwritten -> Chandra; page 2 typed -> Docling; merged in order."""
    pages = _rendered(3)
    fake_chandra = FakeChandra(pages)
    fake_docling = FakeDocling(typed_pages=[2])

    monkeypatch.setattr(pdf_handler, "_get_chandra", lambda config=None: fake_chandra)
    monkeypatch.setattr(pdf_handler, "_get_docling", lambda: fake_docling)
    monkeypatch.setattr(pdf_handler, "check_parser_available", lambda _lane: None)
    monkeypatch.setattr(
        pdf_handler,
        "_render_pdf_pages",
        lambda _p: pdf_handler._get_chandra().render_pdf_pages(_p),
    )
    monkeypatch.setattr(
        pdf_handler,
        "classify_pdf_pages",
        lambda _p: [ParseLane.CHANDRA, ParseLane.DOCLING, ParseLane.CHANDRA],
    )

    ingest = process_pdf_sync(pdf_path, config.raw_dir / "documents", config, manifest=manifest)
    md = ingest.result.markdown

    assert md.index("CHANDRA-p1") < md.index("DOCLING-p2") < md.index("CHANDRA-p3")
    # Chandra only ran on the two handwritten pages, not the typed one.
    assert sum(len(batch) for batch in fake_chandra.parsed) == 2
    assert ingest.result.metadata["parse_lane"] == "hybrid"
    assert ingest.cache_stats is not None
    assert ingest.cache_stats.pages_total == 2


def test_all_typed_uses_docling_whole_doc(
    config: Config,
    manifest: Manifest,
    pdf_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fully typed PDF should go straight through Docling, no Chandra."""
    fake_chandra = FakeChandra(_rendered(2))
    fake_docling = FakeDocling(typed_pages=[1, 2])

    monkeypatch.setattr(pdf_handler, "_get_chandra", lambda config=None: fake_chandra)
    monkeypatch.setattr(pdf_handler, "_get_docling", lambda: fake_docling)
    monkeypatch.setattr(pdf_handler, "check_parser_available", lambda _lane: None)
    monkeypatch.setattr(
        pdf_handler,
        "_render_pdf_pages",
        lambda _p: pdf_handler._get_chandra().render_pdf_pages(_p),
    )
    monkeypatch.setattr(
        pdf_handler,
        "classify_pdf_pages",
        lambda _p: [ParseLane.DOCLING, ParseLane.DOCLING],
    )

    ingest = process_pdf_sync(pdf_path, config.raw_dir / "documents", config, manifest=manifest)

    assert fake_docling.parse_calls == 1
    assert fake_chandra.parsed == []
    assert ingest.cache_stats is None


def test_all_handwritten_uses_chandra_with_cache(
    config: Config,
    manifest: Manifest,
    pdf_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fully handwritten PDF should route every page to Chandra."""
    pages = _rendered(3)
    fake_chandra = FakeChandra(pages)
    fake_docling = FakeDocling(typed_pages=[])

    monkeypatch.setattr(pdf_handler, "_get_chandra", lambda config=None: fake_chandra)
    monkeypatch.setattr(pdf_handler, "_get_docling", lambda: fake_docling)
    monkeypatch.setattr(pdf_handler, "check_parser_available", lambda _lane: None)
    monkeypatch.setattr(
        pdf_handler,
        "_render_pdf_pages",
        lambda _p: pdf_handler._get_chandra().render_pdf_pages(_p),
    )
    monkeypatch.setattr(
        pdf_handler,
        "classify_pdf_pages",
        lambda _p: [ParseLane.CHANDRA] * 3,
    )

    ingest = process_pdf_sync(pdf_path, config.raw_dir / "documents", config, manifest=manifest)

    assert fake_docling.parse_calls == 0
    assert sum(len(b) for b in fake_chandra.parsed) == 3
    assert ingest.cache_stats is not None
    assert ingest.cache_stats.pages_total == 3


class FailingChandra:
    """Chandra stub that raises on the second page (fail-fast)."""

    def __init__(self, pages: list[RenderedPage]) -> None:
        self._pages = pages
        self.parsed: list[list[RenderedPage]] = []

    def render_pdf_pages(self, _file_path: Path) -> list[RenderedPage]:
        return list(self._pages)

    def parse_pages(self, pages: list[RenderedPage]) -> list[str]:
        self.parsed.append(list(pages))
        for page in pages:
            if page.page_number == 2:
                raise PageParseError("Chandra failed on page 2")
        return [f"CHANDRA-p{p.page_number}" for p in pages]


def test_failed_page_raises_and_is_not_cached(
    config: Config,
    manifest: Manifest,
    pdf_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page that fails to parse raises PageParseError and caches nothing.

    The whole batch aborts (fail-fast), so no raw markdown is written and no
    page from the failed batch is cached; a retry re-OCRs cleanly.
    """
    pages = _rendered(2)
    failing = FailingChandra(pages)

    monkeypatch.setattr(pdf_handler, "_get_chandra", lambda config=None: failing)
    monkeypatch.setattr(pdf_handler, "check_parser_available", lambda _lane: None)
    monkeypatch.setattr(
        pdf_handler,
        "_render_pdf_pages",
        lambda _p: pdf_handler._get_chandra().render_pdf_pages(_p),
    )
    monkeypatch.setattr(pdf_handler, "classify_pdf_pages", lambda _p: [ParseLane.CHANDRA] * 2)

    with pytest.raises(PageParseError):
        process_pdf_sync(pdf_path, config.raw_dir / "documents", config, manifest=manifest)

    assert not list((config.raw_dir / "documents").glob("*.md"))
    for page in pages:
        assert manifest.get_cached_page(pdf_handler._hash_bytes(page.png_bytes)) is None


def test_hybrid_reuses_chandra_page_cache(
    config: Config,
    manifest: Manifest,
    pdf_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second hybrid run with unchanged handwritten pages does no OCR."""
    pages = _rendered(2)
    fake_docling = FakeDocling(typed_pages=[1])
    monkeypatch.setattr(pdf_handler, "_get_docling", lambda: fake_docling)
    monkeypatch.setattr(pdf_handler, "check_parser_available", lambda _lane: None)
    monkeypatch.setattr(
        pdf_handler,
        "_render_pdf_pages",
        lambda _p: pdf_handler._get_chandra().render_pdf_pages(_p),
    )
    monkeypatch.setattr(
        pdf_handler,
        "classify_pdf_pages",
        lambda _p: [ParseLane.DOCLING, ParseLane.CHANDRA],
    )

    first_chandra = FakeChandra(pages)
    monkeypatch.setattr(pdf_handler, "_get_chandra", lambda config=None: first_chandra)
    process_pdf_sync(pdf_path, config.raw_dir / "documents", config, manifest=manifest)
    assert sum(len(b) for b in first_chandra.parsed) == 1  # page 2 OCR'd once

    second_chandra = FakeChandra(pages)
    monkeypatch.setattr(pdf_handler, "_get_chandra", lambda config=None: second_chandra)
    ingest = process_pdf_sync(pdf_path, config.raw_dir / "documents", config, manifest=manifest)

    assert second_chandra.parsed == []  # fully served from cache
    assert ingest.cache_stats is not None
    assert ingest.cache_stats.pages_from_cache == 1
