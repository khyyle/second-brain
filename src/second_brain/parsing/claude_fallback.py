"""Claude vision parser for handwritten / scanned pages.

Used when ``parsing.handwriting_parser`` names a Claude model (the paid,
high-quality alternative to local Chandra). Sends each page image to
Claude Sonnet with vision for extraction.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import anthropic
import fitz

from second_brain.parsing import RENDER_DPI
from second_brain.parsing.provider import (
    DocumentParser,
    PageParseError,
    ParseBlock,
    ParseLane,
    ParseResult,
)

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """Extract all content from this page image into structured markdown.

Rules:
- Preserve all text exactly as written
- Convert mathematical notation to LaTeX (inline $...$ or display $$...$$)
- Preserve headings, lists, tables
- For diagrams, provide a text description in [brackets]
- Maintain reading order

Output the markdown content only, no commentary."""

DEFAULT_PARSING_FALLBACK_MODEL = "claude-sonnet-4-6"


class ClaudeFallbackParser(DocumentParser):
    """Extracts page content via Claude Sonnet vision API."""

    REQUEST_TIMEOUT_S = 120.0

    def __init__(self, model: str = DEFAULT_PARSING_FALLBACK_MODEL) -> None:
        self._model = model
        self._client = anthropic.Anthropic(timeout=self.REQUEST_TIMEOUT_S)

    def parse_pages(self, pages: list) -> list[str]:  # type: ignore[type-arg]
        """OCR pre-rendered pages via Claude vision, one API call per page.

        Mirrors :meth:`ChandraParser.parse_pages` so the handwriting lane
        can swap parsers. Used when ``parsing.handwriting_parser`` names a
        Claude model.

        Parameters
        ----------
        pages: list[RenderedPage]
            Pages to OCR (PNG bytes already rendered).

        Returns
        -------
        list[str]
            One markdown string per input page, in order.

        Raises
        ------
        PageParseError
            If any page fails to extract.
        """
        results: list[str] = []
        for page in pages:
            b64 = base64.standard_b64encode(page.png_bytes).decode("ascii")
            try:
                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=4096,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": b64,
                                    },
                                },
                                {"type": "text", "text": EXTRACTION_PROMPT},
                            ],
                        }
                    ],
                )
            except Exception as e:
                logger.exception("Claude vision failed on page %d", page.page_number)
                raise PageParseError(f"Claude vision failed on page {page.page_number}") from e
            results.append(response.content[0].text)
        return results

    async def parse(self, file_path: str) -> ParseResult:
        """
        Send each page as an image to Claude's vision API for
        extraction.

        The paid handwriting lane: one API call per page. Used only when
        ``parsing.handwriting_parser`` names a Claude model.

        Parameters
        ----------
        file_path: str
            Absolute path to the PDF file.

        Returns
        -------
        ParseResult
            Markdown and blocks extracted via Claude vision.
        """
        source = Path(file_path)
        doc = fitz.open(str(source))

        all_blocks: list[ParseBlock] = []
        md_parts: list[str] = []
        order = 0

        try:
            for page_idx in range(len(doc)):
                page = doc[page_idx]
                pix = page.get_pixmap(dpi=RENDER_DPI)
                img_bytes = pix.tobytes("png")
                # standard_b64encode (not urlsafe) — Anthropic API expects RFC 4648
                b64 = base64.standard_b64encode(img_bytes).decode("ascii")

                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=4096,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": b64,
                                    },
                                },
                                {"type": "text", "text": EXTRACTION_PROMPT},
                            ],
                        }
                    ],
                )

                page_md = response.content[0].text
                md_parts.append(page_md)

                all_blocks.append(
                    ParseBlock(
                        content=page_md,
                        block_type="text",
                        page_number=page_idx + 1,
                        reading_order=order,
                    )
                )
                order += 1
        finally:
            doc.close()

        return ParseResult(
            markdown="\n\n---\n\n".join(md_parts),
            blocks=all_blocks,
            metadata={
                "source": str(source),
                "parse_lane": ParseLane.CLAUDE_FALLBACK.value,
                "pages": len(doc),
            },
        )

    async def health_check(self) -> bool:
        """
        Verify the Anthropic API key is valid and reachable.

        Returns
        -------
        bool
            True if the Anthropic API responds successfully.
        """
        try:
            self._client.models.list()
            return True
        except Exception:
            logger.exception("Claude fallback health check failed")
            return False
