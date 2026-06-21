"""Unit tests for per-page parse-lane classification."""

from __future__ import annotations

from second_brain.parsing.provider import ParseLane
from second_brain.parsing.router import classify_page


class FakePage:
    """Minimal stand-in for a fitz.Page for classification tests.

    Font tuples mirror PyMuPDF's ``get_fonts`` shape where index 3 is
    the base font name.
    """

    def __init__(self, text: str, font_names: list[str], n_images: int) -> None:
        self._text = text
        self._fonts = [(i, "", "", name, "", "") for i, name in enumerate(font_names)]
        self._images = [("img",)] * n_images

    def get_text(self) -> str:
        return self._text

    def get_fonts(self, full: bool = False):  # noqa: FBT001, FBT002
        return self._fonts

    def get_images(self):
        return self._images


def test_typed_page_routes_to_docling() -> None:
    page = FakePage("Lorem ipsum " * 50, ["CMR10", "CMBX10", "Calibri"], 0)
    assert classify_page(page) == ParseLane.DOCLING


def test_handwritten_page_routes_to_chandra() -> None:
    # Many San-Francisco subsets: the GoodNotes/Notability OCR-layer signature.
    sfns = [f".SFNS-Regular_subset{i}" for i in range(40)]
    page = FakePage("garbled ocr text here", sfns, 0)
    assert classify_page(page) == ParseLane.CHANDRA


def test_scanned_image_page_routes_to_chandra() -> None:
    # No text layer but a full-page image present.
    page = FakePage("", [], 1)
    assert classify_page(page) == ParseLane.CHANDRA


def test_sparse_typed_slide_stays_docling() -> None:
    # Figure-heavy typed slide: little text, an image, but zero SFNS subsets.
    page = FakePage("Farallon Capital Management", ["Helvetica"], 1)
    assert classify_page(page) == ParseLane.DOCLING


def test_blank_page_without_image_is_docling() -> None:
    # Near-empty and no image: a divider/blank, not a scan.
    page = FakePage("", [], 0)
    assert classify_page(page) == ParseLane.DOCLING


def test_few_sfns_fonts_below_threshold_stays_docling() -> None:
    # A typed Mac doc may embed 1-2 SF subsets; that must not trip detection.
    page = FakePage("A typed document " * 20, [".SFNS-Regular_a", ".SFNS-Bold_b"], 0)
    assert classify_page(page) == ParseLane.DOCLING
