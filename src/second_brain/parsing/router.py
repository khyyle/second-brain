"""Routes PDF pages to the appropriate parse lane based on content type.

Routing is per-page, not per-document. Real-world PDFs are often hybrid:
a typed lecture handout with handwritten margin notes, or GoodNotes
exports that mix imported typed pages with handwritten solutions. A
single per-document decision misroutes these.

We route on *font fingerprint*, as opposed to something like text density. Apple
handwriting exports (GoodNotes / Notability / Apple Notes, printed via
macOS Quartz) bake in a low-quality OCR text layer where every
recognized fragment gets its own subset of the San Francisco system
font (".SFNS-..."). So a page with many distinct SFNS subsets is
handwriting-with-OCR even though it "has text" and would otherwise look
born-digital. Genuinely typed PDFs use a small set of named fonts
(Times, Computer Modern, Calibri, Arial) and zero SFNS subsets.
"""

from __future__ import annotations

import logging
from pathlib import Path

import fitz

from second_brain.parsing.provider import ParseLane

logger = logging.getLogger(__name__)

# A born-digital page with a real text layer has well over this many
# characters. Near-empty pages are either blank or scanned images.
SCANNED_TEXT_THRESHOLD = 20

# Number of distinct San-Francisco font subsets on a page above which we
# treat it as a handwriting-with-OCR export. Calibrated on goodnotes->pdf exports
HANDWRITING_SFNS_THRESHOLD = 3


def _count_sfns_fonts(page: fitz.Page) -> int:
    """Count distinct San-Francisco font subsets used on a page.

    Parameters
    ----------
    page: fitz.Page
        The page to inspect.

    Returns
    -------
    int
        Number of distinct fonts whose base name contains ``SFNS``.
    """
    base_name_index = 3  # position of the font base name in PyMuPDF's font tuple
    return sum(1 for font in page.get_fonts(full=False) if "SFNS" in str(font[base_name_index]))


def classify_page(page: fitz.Page) -> ParseLane:
    """Classify a single PDF page into a parse lane.

    Parameters
    ----------
    page: fitz.Page
        The page to classify.

    Returns
    -------
    ParseLane
        ``CHANDRA`` for handwriting-with-OCR or scanned image pages,
        ``DOCLING`` for genuinely born-digital typed pages.
    """
    text_len = len(page.get_text().strip())
    sfns = _count_sfns_fonts(page)

    # Handwriting export: many SF font subsets from the baked-in OCR layer.
    if sfns >= HANDWRITING_SFNS_THRESHOLD:
        return ParseLane.CHANDRA

    # Scanned image page: essentially no text layer but a page image present.
    if text_len < SCANNED_TEXT_THRESHOLD and bool(page.get_images()):
        return ParseLane.CHANDRA

    return ParseLane.DOCLING


def classify_pdf_pages(pdf_path: str | Path) -> list[ParseLane]:
    """Classify every page of a PDF into a parse lane.

    Parameters
    ----------
    pdf_path: str | Path
        Path to the PDF file.

    Returns
    -------
    list[ParseLane]
        One lane per page, in document order. Empty list for a
        zero-page PDF.
    """
    pdf_path = Path(pdf_path)
    doc = fitz.open(str(pdf_path))
    try:
        lanes = [classify_page(page) for page in doc]
    finally:
        doc.close()

    if lanes:
        chandra_count = sum(1 for lane in lanes if lane == ParseLane.CHANDRA)
        logger.info(
            "Classified %s: %d page(s), %d handwritten/scanned -> Chandra, %d typed -> Docling",
            pdf_path.name,
            len(lanes),
            chandra_count,
            len(lanes) - chandra_count,
        )
    return lanes
