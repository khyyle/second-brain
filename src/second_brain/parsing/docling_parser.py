"""Born-digital PDF parser using Docling (IBM Research).

Handles typed documents with tables, formulas, and layout natively.
Runs locally on Apple Silicon with MPS acceleration.
"""

from __future__ import annotations

import logging
from pathlib import Path

from second_brain.parsing.provider import (
    DocumentParser,
    ParseBlock,
    ParseLane,
    ParseResult,
)

logger = logging.getLogger(__name__)

_BLOCK_TYPE_MAP = {
    "paragraph": "text",
    "heading": "heading",
    "list": "list",
    "table": "table",
    "formula": "equation",
    "picture": "diagram",
    "code": "code",
}


def _map_block_type(docling_type: str) -> str:
    """Normalize Docling's internal element labels to our canonical block types."""
    return _BLOCK_TYPE_MAP.get(docling_type.lower(), "text")


class DoclingParser(DocumentParser):
    """Parses born-digital PDFs via Docling with MPS acceleration."""

    def __init__(self) -> None:
        self._converter = None

    def _get_converter(self):  # type: ignore[no-untyped-def]
        """Lazy-load the Docling converter to avoid import cost at startup."""
        if self._converter is not None:
            return self._converter

        from docling.document_converter import DocumentConverter

        self._converter = DocumentConverter()
        return self._converter

    async def parse(self, file_path: str) -> ParseResult:
        """
        Parse a born-digital PDF into structured blocks via Docling.

        Parameters
        ----------
        file_path: str
            Absolute path to the PDF file.

        Returns
        -------
        ParseResult
            Markdown, blocks with bounding boxes, and diagram
            metadata extracted by Docling.
        """
        converter = self._get_converter()
        source = Path(file_path)
        result = converter.convert(str(source))
        doc = result.document

        md_text = doc.export_to_markdown()

        blocks: list[ParseBlock] = []
        order = 0
        for item in doc.iterate_items():
            # iterate_items() yields tuples (key, element) or bare elements
            # depending on Docling version — handle both
            element = item[1] if isinstance(item, tuple) else item
            label = getattr(element, "label", "paragraph")
            text = getattr(element, "text", "") or ""
            prov = getattr(element, "prov", [])

            page_num = prov[0].page_no if prov else 1
            bbox_val = None
            if prov and hasattr(prov[0], "bbox"):
                b = prov[0].bbox
                bbox_val = (b.l, b.t, b.r, b.b) if b else None

            blocks.append(
                ParseBlock(
                    content=text,
                    block_type=_map_block_type(str(label)),
                    page_number=page_num,
                    reading_order=order,
                    bbox=bbox_val,
                )
            )
            order += 1

        diagrams = [
            {
                "page": b.page_number,
                "bbox": b.bbox,
                "description": b.content or "diagram",
            }
            for b in blocks
            if b.block_type == "diagram"
        ]

        return ParseResult(
            markdown=md_text,
            blocks=blocks,
            metadata={
                "source": str(source),
                "parse_lane": ParseLane.DOCLING.value,
                "pages": (
                    doc.num_pages()
                    if hasattr(doc, "num_pages")
                    else len({b.page_number for b in blocks})
                ),
            },
            diagrams=diagrams,
        )

    async def health_check(self) -> bool:
        """
        Verify Docling loads without error.

        Downloads the model on first run.

        Returns
        -------
        bool
            True if the Docling converter initialises successfully.
        """
        try:
            self._get_converter()
            return True
        except Exception:
            logger.exception("Docling health check failed")
            return False
