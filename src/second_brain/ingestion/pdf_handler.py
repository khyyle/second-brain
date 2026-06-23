"""PDF ingestion handler: per-page routing and parsing.

Routing is per-page (see `parsing/router.py`): each page is classified
as born-digital (Docling) or handwritten/scanned (Chandra). Three
shapes result:

- all typed   -> Docling on the whole document (fast, full structure)
- all handwritten -> Chandra with per-page caching
- hybrid      -> Docling for the typed pages + Chandra for the
                 handwritten pages, merged back in page order

The Chandra lane is ingestion-aware: when a manifest is provided, it
hashes each rendered page and reuses cached OCR output for any page
whose bytes match a prior parse, so Goodnotes-style "re-exported the
whole notebook because I edited one page" cycles stay cheap.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import logging
from dataclasses import dataclass
from pathlib import Path

from second_brain.config import Config
from second_brain.ingestion.manifest import Manifest
from second_brain.parsing.chandra_parser import (
    PAGE_SEPARATOR,
    RenderedPage,
    assemble_chandra_result,
)
from second_brain.parsing.output import write_parse_output
from second_brain.parsing.provider import ParseBlock, ParseLane, ParseResult
from second_brain.parsing.router import classify_pdf_pages

logger = logging.getLogger(__name__)

# Module-level singletons — parsers are expensive to initialize (model loading),
# so we create them once and reuse across files within a single process.
_docling_parser = None
_chandra_parser = None

_INSTALL_HINTS = {
    ParseLane.DOCLING: "docling (install with: uv pip install docling)",
    ParseLane.CHANDRA: "mlx-vlm + chandra-ocr (install with: uv pip install mlx-vlm chandra-ocr)",
}


class ParserNotAvailableError(RuntimeError):
    """Raised when a required parser package is not installed."""


@dataclass(frozen=True)
class CacheStats:
    """Page-cache effectiveness for a single PDF run."""

    pages_total: int
    pages_from_cache: int

    @property
    def pages_ocrd(self) -> int:
        return self.pages_total - self.pages_from_cache


@dataclass(frozen=True)
class PdfIngestResult:
    """Outcome of a single PDF ingestion."""

    result: ParseResult
    md_path: Path
    content_hash: str
    content_unchanged: bool  # True when the assembled markdown matches a prior run
    cache_stats: CacheStats | None  # None for the Docling lane


def check_parser_available(lane: ParseLane) -> None:
    """Verify that the package backing a parse lane is importable.

    Parameters
    ----------
    lane: ParseLane
        The parse lane whose dependency should be checked.

    Raises
    ------
    ParserNotAvailableError
        If the required package cannot be found.
    """
    module_map = {
        ParseLane.DOCLING: "docling",
        ParseLane.CHANDRA: "mlx_vlm",
    }
    module_name = module_map.get(lane)
    if module_name is None:
        return
    if importlib.util.find_spec(module_name) is None:
        raise ParserNotAvailableError(
            f"Parser '{lane.value}' requires package {_INSTALL_HINTS[lane]}"
        )


def _get_docling():  # type: ignore[no-untyped-def]
    """Lazy-load the Docling parser singleton."""
    global _docling_parser
    if _docling_parser is None:
        from second_brain.parsing.docling_parser import DoclingParser

        _docling_parser = DoclingParser()
    return _docling_parser


def _get_chandra(config: Config):  # type: ignore[no-untyped-def]
    """Lazy-load the (MLX) Chandra parser singleton at the configured precision."""
    global _chandra_parser
    if _chandra_parser is None:
        from second_brain.parsing.chandra_parser import ChandraParser

        _chandra_parser = ChandraParser(precision=config.parsing.chandra_precision)
    return _chandra_parser


def _render_pdf_pages(file_path: Path) -> list[RenderedPage]:
    """Rasterize PDF pages (parser-agnostic; uses PyMuPDF, loads no model)."""
    from second_brain.parsing.chandra_parser import ChandraParser

    return ChandraParser.render_pdf_pages(file_path)


def _hash_bytes(data: bytes) -> str:
    """SHA-256 a byte string and return the hex digest."""
    return hashlib.sha256(data).hexdigest()


def _hash_text(text: str) -> str:
    """SHA-256 a unicode string and return the hex digest."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _handwriting_pages_cached(
    pages: list[RenderedPage],
    manifest: Manifest | None,
    config: Config,
) -> tuple[list[str], CacheStats]:
    """OCR handwritten pages via the configured parser, reusing the page cache.

    The cacheable unit is a single rendered page keyed by the SHA-256 of
    its PNG bytes; a cache hit is reused regardless of which parser
    produced it. Only misses hit the model. With ``manifest=None`` every
    page is OCR'd.

    Parameters
    ----------
    pages: list[RenderedPage]
        Pages to OCR (may be a subset of a document, for hybrid PDFs).
    manifest: Manifest | None
        Manifest providing the page cache, or ``None`` to skip caching.
    config: Config
        Provides the handwriting parser choice.

    Returns
    -------
    results: list[str]
        One markdown string per input page, in the same order.
    stats: CacheStats
        Cache hit/miss counts for this batch.

    Raises
    ------
    PageParseError
        Propagated from the parser if any page fails. The document is
        marked failed and nothing from a failed batch is cached, so a
        retry re-OCRs cleanly.
    """
    parser = _get_chandra(config)
    lane = ParseLane.CHANDRA.value
    if not pages:
        return [], CacheStats(pages_total=0, pages_from_cache=0)

    if manifest is None:
        results = parser.parse_pages(pages)
        return results, CacheStats(pages_total=len(pages), pages_from_cache=0)

    page_hashes = [_hash_bytes(p.png_bytes) for p in pages]
    cached: list[str | None] = [
        c.raw_markdown if (c := manifest.get_cached_page(h)) is not None else None
        for h in page_hashes
    ]

    miss_indices = [i for i, c in enumerate(cached) if c is None]
    if miss_indices:
        logger.info(
            "Handwriting (%s) cache: %d/%d hits — OCRing %d page(s)",
            lane,
            len(pages) - len(miss_indices),
            len(pages),
            len(miss_indices),
        )
        fresh = parser.parse_pages([pages[i] for i in miss_indices])
        for i, page_md in zip(miss_indices, fresh, strict=True):
            cached[i] = page_md
            manifest.put_cached_page(page_hashes[i], lane, page_md)
    else:
        logger.info("Handwriting cache: %d/%d hits — no OCR required", len(pages), len(pages))

    results = [r for r in cached if r is not None]
    stats = CacheStats(
        pages_total=len(pages),
        pages_from_cache=len(pages) - len(miss_indices),
    )
    return results, stats


async def _parse_handwritten_with_cache(
    file_path: Path,
    manifest: Manifest | None,
    config: Config,
) -> tuple[ParseResult, CacheStats]:
    """Parse a wholly-handwritten document with the configured parser + cache.

    Parameters
    ----------
    file_path: Path
        PDF to parse.
    manifest: Manifest | None
        Manifest used for page-cache lookups and updates.
    config: Config
        Provides the handwriting parser choice.

    Returns
    -------
    result: ParseResult
        The assembled document parse result.
    stats: CacheStats
        Cache hit/miss counts for this run.
    """
    rendered = _render_pdf_pages(file_path)
    if not rendered:
        return _empty_result(file_path), CacheStats(pages_total=0, pages_from_cache=0)

    results, stats = _handwriting_pages_cached(rendered, manifest, config)
    result = assemble_chandra_result(file_path, rendered, results)
    return result, stats


def _empty_result(file_path: Path) -> ParseResult:
    """Build an empty parse result for a zero-page PDF."""
    return ParseResult(
        markdown="",
        blocks=[],
        metadata={
            "source": str(file_path),
            "parse_lane": ParseLane.CHANDRA.value,
            "pages": 0,
        },
    )


def _docling_pages_markdown(result: ParseResult) -> dict[int, str]:
    """Group a Docling result's blocks into per-page markdown.

    Used only for hybrid documents, where typed pages come from Docling
    and handwritten pages from Chandra and the two are merged in page
    order. Reconstructs light markdown (headings, list items) from the
    block stream; pure-Docling documents still use Docling's richer
    whole-document ``export_to_markdown`` instead.

    Parameters
    ----------
    result: ParseResult
        A Docling parse result whose blocks carry ``page_number``.

    Returns
    -------
    dict[int, str]
        Map of 1-indexed page number to assembled markdown.
    """
    pages: dict[int, list[str]] = {}
    ordered = sorted(result.blocks, key=lambda b: (b.page_number, b.reading_order))
    for b in ordered:
        text = (b.content or "").strip()
        if not text:
            continue
        if b.block_type == "heading":
            line = f"## {text}"
        elif b.block_type == "list":
            line = f"- {text}"
        else:
            line = text
        pages.setdefault(b.page_number, []).append(line)
    return {p: "\n\n".join(lines) for p, lines in pages.items()}


async def _parse_routed(
    file_path: Path,
    page_lanes: list[ParseLane],
    manifest: Manifest | None,
    config: Config,
) -> tuple[ParseResult, CacheStats | None]:
    """Parse a PDF according to its per-page lane classification.

    Parameters
    ----------
    file_path: Path
        PDF to parse.
    page_lanes: list[ParseLane]
        Per-page lane classification from the router.
    manifest: Manifest | None
        Manifest for page-cache reuse on handwritten pages.
    config: Config
        Provides the handwriting parser choice.

    Returns
    -------
    result: ParseResult
        The parsed document.
    cache_stats: CacheStats | None
        Handwriting cache stats, or ``None`` when no handwritten pages ran.
    """
    if not page_lanes:
        return _empty_result(file_path), None

    distinct = set(page_lanes)

    if distinct == {ParseLane.DOCLING}:
        check_parser_available(ParseLane.DOCLING)
        return await _get_docling().parse(str(file_path)), None

    if distinct == {ParseLane.CHANDRA}:
        check_parser_available(ParseLane.CHANDRA)
        return await _parse_handwritten_with_cache(file_path, manifest, config)

    return await _parse_hybrid(file_path, page_lanes, manifest, config)


async def _parse_hybrid(
    file_path: Path,
    page_lanes: list[ParseLane],
    manifest: Manifest | None,
    config: Config,
) -> tuple[ParseResult, CacheStats]:
    """Parse a mixed PDF: Docling for typed pages, configured parser for handwritten.

    Both parsers run, then their outputs are merged page by page so a
    typed handout with handwritten annotations comes through intact.

    Parameters
    ----------
    file_path: Path
        PDF to parse.
    page_lanes: list[ParseLane]
        Per-page lane classification.
    manifest: Manifest | None
        Manifest for page-cache reuse on the handwritten pages.
    config: Config
        Provides the handwriting parser choice.

    Returns
    -------
    result: ParseResult
        The merged document.
    stats: CacheStats
        Cache stats for the handwritten pages only.
    """
    check_parser_available(ParseLane.DOCLING)
    check_parser_available(ParseLane.CHANDRA)

    rendered = _render_pdf_pages(file_path)

    docling_md = _docling_pages_markdown(await _get_docling().parse(str(file_path)))

    chandra_indices = [i for i, lane in enumerate(page_lanes) if lane == ParseLane.CHANDRA]
    chandra_pages = [rendered[i] for i in chandra_indices]
    chandra_results, stats = _handwriting_pages_cached(chandra_pages, manifest, config)
    chandra_by_index = dict(zip(chandra_indices, chandra_results, strict=True))

    md_parts: list[str] = []
    blocks: list[ParseBlock] = []
    for i, lane in enumerate(page_lanes):
        page_number = i + 1
        if lane == ParseLane.CHANDRA:
            page_md = chandra_by_index[i]
        else:
            page_md = docling_md.get(page_number, "")
        md_parts.append(page_md)
        blocks.append(
            ParseBlock(
                content=page_md,
                block_type="text",
                page_number=page_number,
                reading_order=i,
            )
        )

    result = ParseResult(
        markdown=PAGE_SEPARATOR.join(md_parts),
        blocks=blocks,
        metadata={
            "source": str(file_path),
            "parse_lane": "hybrid",
            "pages": len(page_lanes),
        },
    )
    return result, stats


async def process_pdf(
    file_path: Path,
    output_dir: Path,
    config: Config,
    force_lane: str | None = None,
    manifest: Manifest | None = None,
) -> PdfIngestResult:
    """Process a PDF through the appropriate parse lane with caching.

    Classifies each page (Docling for typed, Chandra for
    handwritten/scanned) and parses accordingly, unless ``force_lane``
    pins the whole document to one lane. When ``manifest`` is supplied,
    Chandra pages reuse the per-page cache and the function reports
    whether the assembled markdown is identical to a previously stored
    run (so callers can skip downstream compilation work).

    Parameters
    ----------
    file_path: Path
        PDF file to process.
    output_dir: Path
        Directory to write the resulting markdown into.
    config: Config
        Application config (parser choices).
    force_lane: str | None
        If ``"chandra"`` or ``"docling"``, skip per-page routing and use
        this parser for the whole document. Comes from
        ``SourceConfig.force_parse_lane``.
    manifest: Manifest | None
        Manifest for page-cache lookups and content-hash comparison.
        When ``None``, behaves like the legacy whole-file parser with
        no caching.

    Returns
    -------
    PdfIngestResult
        Parse result, output path, the assembled-markdown hash, a
        flag indicating whether the content was unchanged from a prior
        run, and Chandra cache stats (or ``None`` for the Docling lane).

    Raises
    ------
    ParserNotAvailableError
        If a required parser package is not installed.
    PageParseError
        If any page fails to parse, so the document is marked failed and
        retried on the next ingest rather than stored with a gap.
    """
    cache_stats: CacheStats | None = None
    forced_lane: ParseLane | None = None

    if force_lane is not None:
        forced_lane = ParseLane(force_lane)
        logger.info("Forced parse lane %s for %s", forced_lane.value, file_path.name)
        if forced_lane == ParseLane.DOCLING:
            check_parser_available(forced_lane)
            result = await _get_docling().parse(str(file_path))
        else:
            check_parser_available(ParseLane.CHANDRA)
            result, cache_stats = await _parse_handwritten_with_cache(file_path, manifest, config)
    else:
        page_lanes = classify_pdf_pages(file_path)
        result, cache_stats = await _parse_routed(file_path, page_lanes, manifest, config)

    content_hash = _hash_text(result.markdown)
    prior_hash = manifest.get_content_hash(file_path) if manifest is not None else None
    content_unchanged = prior_hash is not None and prior_hash == content_hash

    stem = file_path.stem
    md_path, _ = write_parse_output(result, output_dir, stem)

    if content_unchanged:
        logger.info(
            "Content hash unchanged for %s — downstream compilation can skip",
            file_path.name,
        )

    return PdfIngestResult(
        result=result,
        md_path=md_path,
        content_hash=content_hash,
        content_unchanged=content_unchanged,
        cache_stats=cache_stats,
    )


def process_pdf_sync(
    file_path: Path,
    output_dir: Path,
    config: Config,
    force_lane: str | None = None,
    manifest: Manifest | None = None,
) -> PdfIngestResult:
    """Synchronous wrapper for :func:`process_pdf`.

    Parameters
    ----------
    file_path: Path
        PDF file to process.
    output_dir: Path
        Directory to write the resulting markdown into.
    config: Config
        Application configuration.
    force_lane: str | None
        Parse lane override from source config.
    manifest: Manifest | None
        Manifest for page-cache lookups and content-hash comparison.

    Returns
    -------
    PdfIngestResult
        Same structure as :func:`process_pdf`.
    """
    return asyncio.run(process_pdf(file_path, output_dir, config, force_lane, manifest))
