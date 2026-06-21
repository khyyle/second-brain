"""Parsing service — document conversion to structured markdown + JSON."""

from second_brain.parsing.provider import (
    DocumentParser,
    ParseBlock,
    ParseLane,
    ParseResult,
)

# Shared rasterization DPI for PDF-to-image conversion.
# 200 balances OCR accuracy against memory — higher yields diminishing returns.
RENDER_DPI = 200

__all__ = ["DocumentParser", "ParseBlock", "ParseLane", "ParseResult", "RENDER_DPI"]
